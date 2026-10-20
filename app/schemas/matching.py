"""Traceable job-matching contracts; profile facts are always read-only."""
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator
from app.schemas.profile import Schema
from app.schemas.jobs import Job, JobFilters

MatchStatus = Literal['queued','preparing','normalizing','calculating_baseline','calculating_hybrid','retrieving','calibrating','generating_explanations','validating','completed','partial','failed','cancelled']
ACTIVE_MATCHING = ('queued','preparing','normalizing','calculating_baseline','calculating_hybrid','retrieving','calibrating','generating_explanations','validating')


class MatchingStart(Schema):
    filters: JobFilters = Field(default_factory=lambda: JobFilters(availability='active',page_size=50))
    minimum_score: float | None = Field(default=None,ge=0,le=100)
    retrieval_pool_size: int | None = Field(default=None,ge=50,le=10000)
    use_ai: bool = False
    allow_external_processing: bool = False

    @model_validator(mode='after')
    def consent(self):
        if self.use_ai and not self.allow_external_processing:
            raise ValueError('AI explanation review requires explicit consent')
        return self


class MatchComponent(Schema):
    score: float | None = Field(default=None,ge=0,le=100)
    state: Literal['confirmed_match','likely_match','missing_information','explicit_mismatch','not_applicable']
    configured_weight: float = Field(default=0,ge=0,le=1)
    effective_weight: float = Field(default=0,ge=0,le=1)
    contribution: float = Field(default=0,ge=0,le=100)
    reason: str
    evidence: list[dict] = Field(default_factory=list)


class MatchReason(Schema):
    id: str
    kind: Literal['positive','gap','consideration','limitation']
    text: str
    evidence: list[dict] = Field(default_factory=list)


class RankedMatch(Schema):
    job: Job
    rank: int = Field(ge=1)
    baseline_score: float = Field(ge=0,le=100)
    hybrid_score: float = Field(ge=0,le=100)
    match_percentage: float | None = Field(default=None,ge=0,le=100)
    calibration: dict = Field(default_factory=dict)
    retrieval: dict = Field(default_factory=dict)
    method_scores: dict[str,float | None] = Field(default_factory=dict)
    filter_evidence: dict = Field(default_factory=dict)
    components: dict[str,MatchComponent]
    calculation: dict
    matched_required: list[dict] = Field(default_factory=list)
    matched_preferred: list[dict] = Field(default_factory=list)
    missing_required: list[dict] = Field(default_factory=list)
    missing_preferred: list[dict] = Field(default_factory=list)
    transferable_skills: list[dict] = Field(default_factory=list)
    reasons: list[MatchReason]
    explanation: str
    explanation_method: str = 'grounded_rules'
    highlighted_reason_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MatchingRunResponse(Schema):
    id: str
    status: MatchStatus
    stage: str
    profile_revision: int
    stale: bool = False
    dataset_version: str
    dataset_reference_date: str
    total_jobs: int = 0
    eligible_jobs: int = 0
    result_count: int = 0
    minimum_score: float
    filters: dict
    config: dict = Field(default_factory=dict)
    provider_metadata: dict = Field(default_factory=dict)
    tasks: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation: dict = Field(default_factory=dict)
    error_code: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    elapsed_ms: int | None = None


class MatchingPage(Schema):
    diagnostics: dict = Field(default_factory=dict)
    run: MatchingRunResponse
    items: list[RankedMatch]
    total: int
    page: int
    page_size: int


class MatchSelection(Schema):
    job_id: str
    reason_ids: list[str] = Field(max_length=5)


class MatchSelections(Schema):
    selections: list[MatchSelection] = Field(max_length=12)


class MatchReview(Schema):
    issues: list[Literal['unknown_job','duplicate_job','unknown_reason','duplicate_reason','missing_required_gap','missing_job','empty_selection']] = Field(max_length=12)


class MatchingReadiness(Schema):
    profile_exists: bool
    profile_revision: int
    can_match: bool
    confirmed_fields: list[str]
    excluded_unconfirmed_fields: list[str]
    missing_fields: list[str]
    message: str


class CandidateSnapshot(Schema):
    confirmed_facts: dict
    normalized: dict | None = None


class MatchingAudit(Schema):
    filter_audit: dict = Field(default_factory=dict)
    retrieval: dict = Field(default_factory=dict)
    run: MatchingRunResponse
    candidate_snapshot: CandidateSnapshot
    profile_source: Literal['confirmed_fields_only'] = 'confirmed_fields_only'
    draft_data_used: Literal[False] = False


class MatchingDetail(Schema):
    run: MatchingRunResponse
    recommendation: RankedMatch
    normalized_job: dict
    candidate_snapshot: CandidateSnapshot


class MatchingExplanation(Schema):
    job_id: str
    score: float = Field(ge=0,le=100)
    text: str
    method: str
    reasons: list[MatchReason]
    highlighted_reason_ids: list[str]
    stale: bool


class SavedMatch(Schema):
    job: Job
    saved_at: datetime
