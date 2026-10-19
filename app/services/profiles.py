from app.db.models import Activity, CandidateProfile
from app.schemas.profile import ProfileResponse


def serialize_profile(profile: CandidateProfile) -> ProfileResponse:
    return ProfileResponse(**profile.facts, id=profile.id, revision=profile.revision,
        verification_state="verified" if profile.verified else "unverified",
        created_at=profile.created_at, updated_at=profile.updated_at)


def add_activity(db, user_id: str, kind: str, message: str):
    db.add(Activity(user_id=user_id, kind=kind, message=message))


def completion(facts: dict) -> int:
    fields = ("name", "email", "location", "career_objective", "education", "skills",
              "projects", "preferred_roles", "preferred_locations", "experience_level")
    return sum(bool(facts.get(field)) for field in fields) * 10
