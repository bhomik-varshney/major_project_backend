from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import current_user, get_db
from app.core.errors import AppError
from app.db.models import CandidateProfile, User, VerificationRecord
from app.repositories.profiles import find_profile, update_profile
from app.schemas.profile import ProfileInput, ProfileResponse, ProfileUpdate, VerificationInput
from app.services.profiles import add_activity, serialize_profile

router = APIRouter(tags=["Profile"])


@router.post("/session")
def start_session(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None:
        user = User()
        db.add(user)
        db.commit()
        request.session["user_id"] = user.id
    return {"access_mode": "local_browser_session", "user_id": user.id}


@router.get("/profile", response_model=ProfileResponse)
def get_profile(user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = find_profile(db, user.id)
    if profile is None:
        raise AppError(404, "profile_not_found", "Create your candidate profile to get started.")
    return serialize_profile(profile, db)


@router.post("/profile", response_model=ProfileResponse, status_code=201)
def create_profile(payload: ProfileInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if find_profile(db, user.id):
        raise AppError(409, "profile_exists", "A profile already exists. Reload it before editing.")
    profile = CandidateProfile(user_id=user.id, facts=payload.model_dump())
    db.add(profile)
    add_activity(db, user.id, "profile_created", "Candidate profile created. Review your facts when ready.")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "profile_exists", "A profile already exists. Reload it before editing.") from exc
    db.refresh(profile)
    return serialize_profile(profile, db)


@router.put("/profile", response_model=ProfileResponse)
def put_profile(payload: ProfileUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if find_profile(db, user.id) is None:
        raise AppError(404, "profile_not_found", "Create your candidate profile first.")
    update_profile(db, user.id, payload.expected_revision, {
        "facts": payload.model_dump(exclude={"expected_revision"}), "verified": False,
    })
    add_activity(db, user.id, "profile_updated", "Profile updated. Changes need a new review.")
    db.commit()
    db.expire_all()
    return serialize_profile(find_profile(db, user.id), db)


@router.post("/profile/verify", response_model=ProfileResponse)
def verify_profile(payload: VerificationInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if find_profile(db, user.id) is None:
        raise AppError(404, "profile_not_found", "Create your candidate profile first.")
    update_profile(db, user.id, payload.expected_revision, {"verified": True})
    profile = find_profile(db, user.id)
    db.add(VerificationRecord(user_id=user.id, profile_id=profile.id,
        profile_revision=payload.expected_revision + 1, confirmed_values=profile.facts,
        applied_fields=list(profile.facts)))
    add_activity(db, user.id, "profile_verified", "Current profile facts confirmed by you.")
    db.commit()
    db.expire_all()
    return serialize_profile(find_profile(db, user.id), db)
