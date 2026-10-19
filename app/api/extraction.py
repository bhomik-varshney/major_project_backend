from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import current_user, get_db
from app.api.documents import owned_document
from app.core.errors import AppError
from app.db.models import DocumentExtraction, User, VerificationRecord, ResumeAgentRun, now
from app.repositories.profiles import find_profile
from app.schemas.extraction import CorrectionInput, DraftUpdate, ExtractedDataResponse, ExtractionStatus, ExtractionVerification, ProcessInput
from app.schemas.profile import ProfileResponse
from app.services.document_processing import ACTIVE
from app.services.extraction_review import latest_extraction, require_draft, save_edited_draft, verify_extraction
from app.services.profiles import add_activity, serialize_profile
from app.services.resume_presenter import review_view

router = APIRouter(prefix="/documents", tags=["Document processing"])


@router.post("/{document_id}/process", response_model=ExtractionStatus, status_code=202)
def process(document_id: str, payload: ProcessInput, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_document(db, user.id, document_id)
    if payload.use_groq and not payload.allow_external_processing:
        raise AppError(422, "processing_consent_required", "Confirm that this document’s extracted text may be sent to Groq, or choose local processing.")
    latest = latest_extraction(db, document_id)
    if latest and (latest.status in ACTIVE or (latest.status not in {"failed", "cancelled", "rejected"} and not payload.restart)):
        return latest
    processor = request.app.state.processor
    processor.reserve()
    profile = find_profile(db, user.id)
    extraction = DocumentExtraction(user_id=user.id, document_id=document_id,
        profile_id=profile.id if profile else None, active_document=document_id,
        run_version=(latest.run_version + 1) if latest else 1,
        allow_external_processing=payload.use_groq and payload.allow_external_processing)
    extraction.agent_run=ResumeAgentRun(user_id=user.id)
    db.add(extraction)
    try:
        db.commit()
    except IntegrityError:
        processor.capacity.release()
        db.rollback()
        existing = latest_extraction(db, document_id)
        if existing and existing.status in ACTIVE:
            return existing
        raise AppError(409, "processing_conflict", "This document changed. Refresh before processing again.") from None
    except Exception:
        processor.capacity.release()
        raise
    db.refresh(extraction)
    snapshot = ExtractionStatus.model_validate(extraction)
    try:
        processor.submit(extraction.id, payload.use_groq)
    except AppError:
        if extraction.agent_run:
            extraction.agent_run.status, extraction.agent_run.error_code = "failed", "processing_unavailable"
            extraction.agent_run.completed_at = now()
        extraction.status, extraction.stage, extraction.active_document = "failed", "failed", None
        extraction.error_code, extraction.error_message = "processing_unavailable", "The worker is unavailable. Retry after restarting."
        db.commit()
        raise
    return snapshot


@router.get("/{document_id}/status", response_model=ExtractionStatus)
def status(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_document(db, user.id, document_id)
    return require_draft(db, document_id)


def data_response(db, extraction, user_id):
    profile = find_profile(db, user_id)
    receipt = db.scalar(select(VerificationRecord).where(VerificationRecord.extraction_id == extraction.id))
    structured, evidence, skills = review_view(extraction.values, extraction.evidence, extraction.status,
        receipt.applied_fields if receipt else [], extraction.method, extraction.warnings)
    return ExtractedDataResponse(**ExtractionStatus.model_validate(extraction).model_dump(),
        values=extraction.values, evidence=evidence, structured=structured, skill_details=skills,
        current_profile_revision=profile.revision if profile else 0,
        current_profile_values=profile.facts if profile else {}, applied_fields=receipt.applied_fields if receipt else [])


@router.get("/{document_id}/extracted-data", response_model=ExtractedDataResponse)
def extracted_data(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_document(db, user.id, document_id)
    extraction = require_draft(db, document_id)
    if extraction.status in (*ACTIVE, "failed", "cancelled"):
        raise AppError(409, "extraction_not_ready", "A review draft is not available. Check processing status or retry.")
    return data_response(db, extraction, user.id)


@router.put("/{document_id}/extracted-data", response_model=ExtractedDataResponse)
def edit_extracted_data(document_id: str, payload: DraftUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_document(db, user.id, document_id)
    extraction = require_draft(db, document_id, payload.processing_id, payload.expected_revision)
    updated = save_edited_draft(db, extraction, payload)
    return data_response(db, updated, user.id)


@router.post("/{document_id}/verify", response_model=ProfileResponse)
def verify(document_id: str, payload: ExtractionVerification, user: User = Depends(current_user), db: Session = Depends(get_db)):
    document = owned_document(db, user.id, document_id)
    extraction = require_draft(db, document_id, payload.processing_id, payload.expected_revision)
    profile = verify_extraction(db, document, extraction, payload, user.id)
    return serialize_profile(profile, db)


@router.post("/{document_id}/request-correction", response_model=ExtractionStatus)
def correct(document_id: str, payload: CorrectionInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_document(db, user.id, document_id)
    extraction = require_draft(db, document_id, payload.processing_id, payload.expected_revision)
    permitted = ACTIVE if payload.action == "cancel" else ("awaiting_review", "needs_correction")
    if extraction.status not in permitted:
        raise AppError(409, "extraction_conflict", "This action is not available in the current processing state.")
    target = {"cancel": "cancelled", "reject": "rejected", "correction": "needs_correction"}[payload.action]
    result = db.execute(update(DocumentExtraction).where(DocumentExtraction.id == extraction.id,
        DocumentExtraction.revision == extraction.revision, DocumentExtraction.status.in_(permitted)).values(
        status=target, active_document=None, revision=extraction.revision + 1, updated_at=now()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise AppError(409, "extraction_conflict", "Processing changed. Reload before continuing.")
    db.execute(update(ResumeAgentRun).where(ResumeAgentRun.extraction_id == extraction.id).values(
        status="needs_review" if target == "needs_correction" else target, completed_at=now()))
    add_activity(db, user.id, "extraction_" + target, "Document review " + target.replace("_", " ") + ". No profile changes were made.")
    db.commit()
    db.expire_all()
    return db.get(DocumentExtraction, extraction.id)
