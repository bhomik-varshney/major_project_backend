import copy
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.db.models import CandidateProfile, DocumentExtraction, VerificationRecord, now
from app.repositories.profiles import find_profile, update_profile
from app.schemas.profile import ProfileInput
from app.services.profiles import add_activity, confirmed_fields
from app.services.resume_structure import leaves, normalize


def latest_extraction(db, document_id):
    return db.scalar(select(DocumentExtraction).where(DocumentExtraction.document_id == document_id).order_by(DocumentExtraction.run_version.desc()).limit(1))


def require_draft(db, document_id, processing_id=None, revision=None):
    extraction = latest_extraction(db, document_id)
    if extraction is None:
        raise AppError(404, "extraction_not_found", "Start processing this document to create a review draft.")
    if (processing_id and extraction.id != processing_id) or (revision and extraction.revision != revision):
        raise AppError(409, "extraction_conflict", "This extraction changed. Reload its latest review before continuing.")
    return extraction


def save_edited_draft(db, extraction, payload):
    if extraction.status not in {"awaiting_review", "needs_correction"}:
        raise AppError(409, "extraction_not_editable", "This extraction is not awaiting review. Start a new run if needed.")
    values = payload.values.model_dump()
    old_leaves, new_leaves = leaves(extraction.values), leaves(values)
    evidence = {}
    remap = {}
    for field in ("skills", "links", "certifications", "achievements", "publications", "education", "projects", "experience", "additional_sections"):
        old_items = extraction.values.get(field, [])
        anchor = {"education": "qualification", "projects": "name", "experience": "role", "additional_sections":"heading"}.get(field)
        for index, item in enumerate(values[field]):
            matches = [i for i, old in enumerate(old_items) if old == item]
            if not matches and anchor:
                matches = [i for i, old in enumerate(old_items) if old.get(anchor) and old.get(anchor) == item.get(anchor)]
            if len(matches) == 1:
                remap[f"{field}.{index}"] = f"{field}.{matches[0]}"
    for path, value in new_leaves.items():
        parts = path.split(".")
        prefix = ".".join(parts[:2])
        old_path = remap.get(prefix, "__new__") + ("." + ".".join(parts[2:]) if len(parts) > 2 else "") if len(parts) > 1 and parts[1].isdigit() else path
        if old_leaves.get(old_path) == value and old_path in extraction.evidence:
            evidence[path] = {**extraction.evidence[old_path], "verified": False, "review_status": "pending"}
        else:
            evidence[path] = {"source": None, "confidence": "unknown", "method": "user", "edited": True,
                "edited_by_user": True, "classification": "ambiguous", "verified": False, "review_status": "pending",
                "warning": "Candidate correction; this value is not asserted to appear in the source document."}
    result = db.execute(update(DocumentExtraction).where(DocumentExtraction.id == extraction.id,
        DocumentExtraction.revision == payload.expected_revision,
        DocumentExtraction.status.in_(("awaiting_review", "needs_correction"))).values(values=values, evidence=evidence,
        revision=extraction.revision + 1, status="awaiting_review", updated_at=now()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise AppError(409, "extraction_conflict", "This extraction changed. Reload before saving edits.")
    db.commit()
    db.expire_all()
    return db.get(DocumentExtraction, extraction.id)


def verify_extraction(db, document, extraction, payload, user_id):
    if extraction.status != "awaiting_review":
        raise AppError(409, "extraction_not_reviewable", "This extraction is not awaiting verification.")
    profile = find_profile(db, user_id)
    current_revision = profile.revision if profile else 0
    if current_revision != payload.expected_profile_revision:
        raise AppError(409, "revision_conflict", "Your profile changed. Reload the review and check the current profile before confirming.")
    selected = list(dict.fromkeys(payload.fields))
    if not set(payload.replace_fields).issubset(selected):
        raise AppError(422, "replacement_invalid", "Replacement choices must be among the selected sections.")
    original = ProfileInput.model_validate(profile.facts).model_dump() if profile else ProfileInput.model_construct(name="").model_dump()
    merged = copy.deepcopy(original)
    proposed = extraction.values
    for field in selected:
        value = proposed.get(field)
        if not value:
            raise AppError(422, "empty_selection", "A selected section is empty. Deselect it or add the missing information.")
        existing = merged.get(field)
        if isinstance(value, list) and field not in payload.replace_fields:
            combined = list(existing or [])
            for item in value:
                if not any(normalize(item) == normalize(prior) for prior in combined):
                    combined.append(item)
            merged[field] = combined
        elif existing and existing != value and field not in payload.replace_fields:
            raise AppError(409, "overwrite_confirmation_required", "A selected value differs from your saved profile. Explicitly choose replacement or leave that section unselected.")
        else:
            merged[field] = value
    # Full profile validation rejects incomplete selected education/work records.
    try:
        merged = ProfileInput.model_validate(merged).model_dump()
    except Exception:
        raise AppError(422, "profile_incomplete", "The selected information is incomplete or invalid. Check the name, contact details, and required education/experience/project fields.") from None
    reviewed = confirmed_fields(db, profile) if profile else {}
    reviewed.update({field: merged[field] for field in selected})
    fully_verified = all(not value or reviewed.get(field) == value for field, value in merged.items())
    # Claim the exact draft within the same transaction as the profile and receipt.
    result = db.execute(update(DocumentExtraction).where(DocumentExtraction.id == extraction.id,
        DocumentExtraction.revision == payload.expected_revision, DocumentExtraction.status == "awaiting_review").values(
        status="verified", verified_at=now(), updated_at=now(), revision=extraction.revision + 1,
        evidence={path: {**proof, "verified": path.split(".")[0] in selected,
            "review_status": "confirmed" if path.split(".")[0] in selected else "not_applied"}
            for path, proof in extraction.evidence.items()}).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise AppError(409, "extraction_conflict", "This extraction changed. Reload before confirming.")
    if profile:
        update_profile(db, user_id, current_revision, {"facts": merged, "verified": fully_verified})
        profile_revision = current_revision + 1
    else:
        profile = CandidateProfile(user_id=user_id, facts=merged, verified=fully_verified)
        db.add(profile)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise AppError(409, "revision_conflict", "A profile was created in another tab. Reload the review.") from None
        profile_revision = 1
    db.execute(update(DocumentExtraction).where(DocumentExtraction.id == extraction.id).values(profile_id=profile.id))
    db.add(VerificationRecord(user_id=user_id, profile_id=profile.id, document_id=document.id,
        extraction_id=extraction.id, source_document_ref=document.id, source_filename=document.filename,
        profile_revision=profile_revision, confirmed_values={field: merged[field] for field in selected},
        selected_values={**{field: proposed[field] for field in selected},
            "_skill_metadata": {name: {key: extraction.evidence.get(f"skills.{index}", {}).get(key)
                for key in ("method", "edited", "edited_by_user", "source_section", "confidence")}
                for index, name in enumerate(proposed.get("skills", []))} if "skills" in selected else {}},
        applied_fields=selected, source_evidence={path: {**proof, "verified": True, "review_status": "confirmed"} for path, proof in extraction.evidence.items() if path.split(".")[0] in selected}))
    if extraction.agent_run:
        extraction.agent_run.status = "confirmed"
    add_activity(db, user_id, "extraction_verified", "Reviewed document information saved to your profile.")
    db.commit()
    db.expire_all()
    return find_profile(db, user_id)
