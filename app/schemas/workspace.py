from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    content_type: str
    size_bytes: int
    upload_status: Literal["stored"]
    extraction_status: str
    processing_status: str = "uploaded"
    verification_status: str = "unverified"
    processing_id: str | None = None
    extraction_version: int | None = None
    created_at: datetime


class ActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    message: str
    created_at: datetime


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: Literal["connected", "unavailable"]
    access_mode: Literal["local_browser_session"] = "local_browser_session"


class SummaryResponse(BaseModel):
    data_mode: Literal["live_local"] = "live_local"
    profile_exists: bool
    candidate_name: str | None
    profile_completion: int
    profile_revision: int | None
    verification_state: Literal["missing", "unverified", "verified"]
    uploaded_document_count: int
    profile_skill_count: int
    extracted_skill_count: int = 0
    recommended_job_count: int | None = None
    latest_matching_status: str | None = None
    matching_stale: bool = False
    skill_gap_count: int | None = None
    pending_approval_count: int | None = None
    application_status_summary: dict[str, int] | None = None
    recent_activity: list[ActivityResponse]
    feature_states: dict[str, str] = {
        "extraction": "available", "jobs": "seeded_demo", "matching": "available",
        "applications": "planned", "orchestration": "resume_and_matching",
    }
