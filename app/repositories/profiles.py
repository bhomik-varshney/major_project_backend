from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import CandidateProfile, now


def find_profile(db: Session, user_id: str):
    return db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))


def update_profile(db: Session, user_id: str, revision: int, values: dict):
    result = db.execute(update(CandidateProfile).where(
        CandidateProfile.user_id == user_id, CandidateProfile.revision == revision,
    ).values(**values, revision=revision + 1, updated_at=now()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        raise AppError(409, "revision_conflict", "Your profile changed in another tab. Reload the saved version before editing.")
