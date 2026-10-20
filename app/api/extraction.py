from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from app.schemas.intelligence import ProcessingRun, ReviewDecision
from app.schemas.canonical import CanonicalResume,RawEvidence
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import current_user, get_db
from app.api.documents import owned_document
from app.core.errors import AppError
from app.db.models import DocumentExtraction, User, VerificationRecord, ResumeAgentRun, ResumeProcessingAudit, ResumePageArtifact, now
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
    if payload.use_vision and not (payload.use_groq and payload.allow_external_processing and payload.allow_image_processing):
        raise AppError(422,'image_processing_consent_required','Vision requires explicit consent to send page images and extracted text to Groq for this run.')
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
        db.flush()
        db.add(ResumeProcessingAudit(extraction_id=extraction.id,user_id=user.id,data={'consent':{
            'text':bool(payload.use_groq and payload.allow_external_processing),
            'images':bool(payload.use_vision and payload.allow_image_processing)}}))
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


@router.get('/{document_id}/audit', response_model=ProcessingRun)
def audit_data(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db), run_id: str | None = None):
    from app.services.intelligence_audit import draft_audit
    owned_document(db,user.id,document_id)
    extraction=db.scalar(select(DocumentExtraction).where(DocumentExtraction.id==run_id,DocumentExtraction.document_id==document_id,DocumentExtraction.user_id==user.id)) if run_id else require_draft(db,document_id)
    if extraction is None:raise AppError(404,'processing_run_not_found','This processing run was not found in your workspace.')
    audit=db.get(ResumeProcessingAudit,extraction.id)
    try:
        raw=dict(audit.data) if audit else {}
        # Old runs expose actual saved text, without inventing missing page artifacts.
        if not audit:
            raw['exact_text']=[dict(block_id=b['block_id'],text=b['text'],page=b.get('page'),paragraph=b.get('paragraph'),order=i,metadata={}) for i,b in enumerate(extraction.text_blocks)]
            raw['warnings']=[{'code':'legacy_run','message':'This run predates the audit pipeline. Reprocess for page previews and OCR.'}]
        raw.update(id=extraction.id,document_id=document_id,stage=extraction.stage,
            status={'awaiting_review':'draft_ready','verified':'confirmed','needs_correction':'needs_review'}.get(extraction.status,extraction.status))
        receipt=db.scalar(select(VerificationRecord).where(VerificationRecord.extraction_id==extraction.id))
        raw['review']=ReviewDecision(action={'verified':'confirmed','rejected':'rejected','cancelled':'cancelled','needs_correction':'needs_review'}.get(extraction.status,'pending'),
            revision=extraction.revision,fields=receipt.applied_fields if receipt else [],decided_at=(extraction.verified_at or extraction.updated_at).isoformat() if extraction.status in {'verified','rejected','cancelled','needs_correction'} else None).model_dump()
        if extraction.values:
            raw['draft']=draft_audit(extraction.values,extraction.evidence,raw.get('other_sections',[]),raw.get('warnings',[]))
        raw.pop('other_sections',None)
        if extraction.error_message:
            raw.setdefault('warnings',[]).append({'code':extraction.error_code or 'processing_failed','message':extraction.error_message,'severity':'failure'})
        return ProcessingRun.model_validate(raw)
    except (ValidationError,TypeError,ValueError,KeyError,AttributeError):
        raise AppError(409,'audit_invalid','Stored audit data could not be read. Reprocess this document to rebuild its audit; saved profile facts remain available.') from None


@router.get('/{document_id}/audit/pages/{artifact_id}')
def page_image(document_id: str, artifact_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from fastapi import Response
    owned_document(db,user.id,document_id)
    page=db.scalar(select(ResumePageArtifact).join(DocumentExtraction,DocumentExtraction.id==ResumePageArtifact.extraction_id).where(
        ResumePageArtifact.id==artifact_id,ResumePageArtifact.user_id==user.id,DocumentExtraction.document_id==document_id))
    if not page:
        raise AppError(404,'page_not_found','This private page preview was not found.')
    return Response(page.content,media_type='image/png',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Disposition':'inline; filename="resume-page.png"'})


@router.get('/{document_id}/runs')
def processing_history(document_id: str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    owned_document(db,user.id,document_id)
    return [{'id':r.id,'run_version':r.run_version,'revision':r.revision,'status':r.status,'method':r.method,'created_at':r.created_at,'completed_at':r.completed_at}
        for r in db.scalars(select(DocumentExtraction).where(DocumentExtraction.document_id==document_id,DocumentExtraction.user_id==user.id).order_by(DocumentExtraction.run_version.desc()).limit(30))]


@router.get('/{document_id}/canonical',response_model=CanonicalResume)
def canonical_representation(document_id:str,run_id:str|None=None,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from app.schemas.canonical import CanonicalResume
    data=audit_data(document_id,user,db,run_id)
    if data.canonical is None:raise AppError(409,'canonical_not_available','This run has no connected representation. Reprocess to create it.')
    return CanonicalResume.model_validate(data.canonical)


@router.get('/{document_id}/compare')
def compare_runs(document_id:str,left_run_id:str,right_run_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from app.services.resume_structure import leaves
    owned_document(db,user.id,document_id)
    rows=[]
    for id in (left_run_id,right_run_id):
        row=db.scalar(select(DocumentExtraction).where(DocumentExtraction.id==id,DocumentExtraction.document_id==document_id,DocumentExtraction.user_id==user.id))
        if row is None:raise AppError(404,'processing_run_not_found','A requested extraction version was not found in your workspace.')
        if row.status in ACTIVE:raise AppError(409,'comparison_not_ready','Wait for processing to complete before comparing saved drafts.')
        rows.append(row)
    left,right=(leaves(row.values) for row in rows)
    differences=[{'field':field,'before':left.get(field),'after':right.get(field),'change':'added' if field not in left else 'removed' if field not in right else 'changed'}
        for field in sorted(set(left)|set(right)) if left.get(field)!=right.get(field)]
    return {'left_run_id':left_run_id,'right_run_id':right_run_id,'left_version':rows[0].run_version,'right_version':rows[1].run_version,'differences':differences,'profile_updated':False}


@router.get('/{document_id}/evidence',response_model=list[RawEvidence])
def evidence_records(document_id:str,run_id:str|None=None,user:User=Depends(current_user),db:Session=Depends(get_db)):
    return audit_data(document_id,user,db,run_id).raw_evidence
