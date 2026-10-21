from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import current_user, get_db
from app.db.models import Activity, Document, User, VerificationRecord, MatchingRun, SkillGapRun,ApplicationRecord
from app.repositories.profiles import find_profile
from app.schemas.workspace import SummaryResponse
from app.services.profiles import completion, document_skills
from app.services.matching import run_response
from app.services.skill_gaps import gap_run_response

router = APIRouter(tags=["Dashboard"])


@router.get("/dashboard/summary", response_model=SummaryResponse)
def summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = find_profile(db, user.id)
    facts = profile.facts if profile else {}
    latest = db.scalar(select(MatchingRun).where(MatchingRun.user_id == user.id).order_by(MatchingRun.created_at.desc()).limit(1))
    matching = run_response(db, latest) if latest else None
    latest_gap=db.scalar(select(SkillGapRun).where(SkillGapRun.user_id==user.id).order_by(SkillGapRun.created_at.desc()).limit(1))
    gap=gap_run_response(db,latest_gap) if latest_gap else None
    return SummaryResponse(
        application_status_summary=dict(db.execute(select(ApplicationRecord.status,func.count()).where(ApplicationRecord.user_id==user.id).group_by(ApplicationRecord.status)).all()),
        pending_approval_count=db.scalar(select(func.count()).select_from(ApplicationRecord).where(ApplicationRecord.user_id==user.id,ApplicationRecord.status.in_(['needs_user_review','needs_submission_approval']))),
        skill_gap_count=gap.gap_count if gap and gap.status in {'completed','partial','needs_review'} and not gap.stale else None,
        recommended_job_count=matching.result_count if matching and matching.status in {"completed", "partial"} and not matching.stale else None,
        latest_matching_status=matching.status if matching else None, matching_stale=matching.stale if matching else False,
        profile_exists=profile is not None, candidate_name=facts.get("name"),
        profile_completion=completion(facts), profile_revision=profile.revision if profile else None,
        verification_state=("verified" if profile.verified else "unverified") if profile else "missing",
        uploaded_document_count=db.scalar(select(func.count()).select_from(Document).where(Document.user_id == user.id)),
        profile_skill_count=len(facts.get("skills", [])),
        extracted_skill_count=len({str(skill).casefold() for review in db.scalars(select(VerificationRecord).where(VerificationRecord.user_id == user.id, VerificationRecord.source_document_ref.is_not(None))) for skill in document_skills(review) if skill in facts.get("skills", [])}),
        recent_activity=db.scalars(select(Activity).where(Activity.user_id == user.id).order_by(Activity.created_at.desc(), Activity.id).limit(12)).all(),
    )
