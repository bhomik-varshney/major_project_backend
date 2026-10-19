from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import current_user, get_db
from app.db.models import Activity, Document, User, VerificationRecord
from app.repositories.profiles import find_profile
from app.schemas.workspace import SummaryResponse
from app.services.profiles import completion, document_skills

router = APIRouter(tags=["Dashboard"])


@router.get("/dashboard/summary", response_model=SummaryResponse)
def summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = find_profile(db, user.id)
    facts = profile.facts if profile else {}
    return SummaryResponse(
        profile_exists=profile is not None, candidate_name=facts.get("name"),
        profile_completion=completion(facts), profile_revision=profile.revision if profile else None,
        verification_state=("verified" if profile.verified else "unverified") if profile else "missing",
        uploaded_document_count=db.scalar(select(func.count()).select_from(Document).where(Document.user_id == user.id)),
        profile_skill_count=len(facts.get("skills", [])),
        extracted_skill_count=len({str(skill).casefold() for review in db.scalars(select(VerificationRecord).where(VerificationRecord.user_id == user.id, VerificationRecord.source_document_ref.is_not(None))) for skill in document_skills(review) if skill in facts.get("skills", [])}),
        recent_activity=db.scalars(select(Activity).where(Activity.user_id == user.id).order_by(Activity.created_at.desc(), Activity.id).limit(12)).all(),
    )
