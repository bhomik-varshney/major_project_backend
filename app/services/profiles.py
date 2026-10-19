from sqlalchemy import select
from app.db.models import Activity, CandidateProfile, VerificationRecord
from app.schemas.profile import ProfileResponse
from app.schemas.profile import ProfileInput


def canonical(field, value):
    if field in ProfileInput.model_fields:
        return ProfileInput.model_validate({"name": "_", field: value}).model_dump()[field]
    return value


def skill_proof(review, name):
    selected = review.selected_values.get("skills", [])
    if name not in selected:
        return {}
    return review.source_evidence.get(f"skills.{selected.index(name)}") or review.selected_values.get("_skill_metadata", {}).get(name, {})


def document_skills(review):
    return [name for name in review.selected_values.get("skills", []) if skill_proof(review, name).get("method") != "user"]


def confirmed_fields(db, profile):
    if not profile:
        return {}
    confirmed = {field: canonical(field, value) for field, value in profile.facts.items()} if profile.verified else {}
    if db is not None:
        for review in db.scalars(select(VerificationRecord).where(VerificationRecord.profile_id == profile.id).order_by(VerificationRecord.created_at)):
            for field, value in review.confirmed_values.items():
                if canonical(field, profile.facts.get(field)) == canonical(field, value):
                    confirmed[field] = canonical(field, value)
    return confirmed


def serialize_profile(profile: CandidateProfile, db=None) -> ProfileResponse:
    sources, verified_at, reviews = [], None, []
    if db is not None:
        reviews = db.scalars(select(VerificationRecord).where(VerificationRecord.profile_id == profile.id).order_by(VerificationRecord.created_at.desc())).all()
        verified_at = reviews[0].created_at if reviews else None
        for review in reviews:
            fields = [field for field in review.applied_fields if canonical(field, profile.facts.get(field)) == canonical(field, review.confirmed_values.get(field))]
            if review.source_document_ref and fields:
                sources.append({"document_id": review.document_id, "source_document_ref": review.source_document_ref,
                    "filename": review.source_filename, "fields": fields, "verified_at": review.created_at.isoformat(),
                    "source_available": review.document_id is not None})
    skills = []
    for name in profile.facts.get("skills", []):
        detail = {"name": name, "original_text": name, "source": "manual", "source_section": "",
            "evidence": None, "confidence": "unknown", "method": "user", "edited_by_user": True,
            "verified": bool(profile.verified), "user_confirmed": bool(profile.verified)}
        for review in reviews:
            if name in review.confirmed_values.get("skills", []):
                detail.update(verified=True, user_confirmed=True)
            selected = review.selected_values.get("skills", [])
            if review.source_document_ref and name in selected:
                proof = skill_proof(review, name)
                detail.update(source="manual" if proof.get("edited_by_user") or proof.get("method") == "user" else "resume",
                    original_text=proof.get("original_text") or name, source_section=proof.get("source_section", ""),
                    evidence=proof.get("source"), confidence=proof.get("confidence", "unknown"),
                    method=proof.get("method", "deterministic"), edited_by_user=proof.get("edited_by_user", proof.get("edited", False)),
                    source_available=review.document_id is not None)
                break
        skills.append(detail)
    return ProfileResponse(**profile.facts, id=profile.id, revision=profile.revision, skill_details=skills,
        verification_state="verified" if profile.verified else "unverified",
        evidence_origin="mixed" if sources else "manual", sources=sources,
        verified_at=verified_at, verified_fields=list(confirmed_fields(db, profile)),
        created_at=profile.created_at, updated_at=profile.updated_at)


def add_activity(db, user_id: str, kind: str, message: str):
    db.add(Activity(user_id=user_id, kind=kind, message=message))


def completion(facts: dict) -> int:
    fields = ("name", "email", "location", "career_objective", "education", "skills",
              "projects", "preferred_roles", "preferred_locations", "experience_level")
    return sum(bool(facts.get(field)) for field in fields) * 10
