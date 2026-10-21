"""Immutable, evidence-linked skill analysis contracts. Scores are raw 0–100 points."""
from datetime import datetime
from typing import Literal
from pydantic import Field, model_validator
from app.schemas.profile import Schema
from app.schemas.jobs import JobFilters

ACTIVE_GAPS = ('queued','preparing','analysing_profile','analysing_requirements','comparing_skills','prioritising_gaps','generating_guidance','validating')
GapStatus = Literal['queued','preparing','analysing_profile','analysing_requirements','comparing_skills','prioritising_gaps','generating_guidance','validating','completed','partial','needs_review','failed','cancelled']
Classification = Literal['confirmed_match','strong_related_match','partial_match','transferable_match','missing','explicitly_mismatched','unknown','not_applicable']

class GapStart(Schema):
    mode: Literal['single','selected','top_ranked','target'] = 'top_ranked'
    job_ids: list[str] = Field(default_factory=list,max_length=30)
    matching_run_id: str | None = Field(default=None,max_length=36)
    top_k: int = Field(default=10,ge=1,le=30)
    filters: JobFilters = Field(default_factory=lambda:JobFilters(availability='active',location_scope='compatible'))
    use_ai: bool = False
    allow_external_processing: bool = False

    @model_validator(mode='after')
    def valid_selection(self):
        if len(set(self.job_ids)) != len(self.job_ids): raise ValueError('Select each job only once')
        if self.mode=='single' and len(self.job_ids)!=1: raise ValueError('Single mode requires one job')
        if self.mode=='selected' and not self.job_ids: raise ValueError('Select at least one job')
        if self.mode in ('target','top_ranked') and self.job_ids: raise ValueError('This mode selects its own jobs')
        if self.mode=='target' and not (self.filters.role or self.filters.category): raise ValueError('Enter a target role or category')
        if self.mode=='target' and self.matching_run_id: raise ValueError('Target mode uses current job filters rather than a saved matching run')
        if self.use_ai and not self.allow_external_processing: raise ValueError('Optional Groq guidance review needs consent')
        return self

class SkillEvidence(Schema):
    path: str
    original: str
    origin: Literal['direct','project','experience','certification','objective']
    strength: Literal['confirmed','supported_by_project','supported_by_experience','supported_by_certification','mentioned_only','inferred','uncertain']
    confidence: str = 'unknown'
    source: dict = Field(default_factory=dict)
    denied: bool = False

class InventorySkill(Schema):
    normalized: str
    originals: list[str]
    category: str
    evidence: list[SkillEvidence]
    explicitly_confirmed: bool = False
    proficiency: str = 'Not established; evidence strength is not proficiency.'

class Requirement(Schema):
    job_id: str
    normalized: str
    original: str
    path: str
    type: Literal['required','preferred','optional','unknown']
    category: str
    critical: bool = False
    inferred: bool = False

class SkillComparison(Schema):
    job_id: str
    normalized: str
    classification: Classification
    relationship: Literal['exact','synonym','related','transferable','weak_association','unrelated','unknown']
    requirement: Requirement
    candidate_evidence: list[SkillEvidence] = Field(default_factory=list)
    related_skills: list[dict] = Field(default_factory=list)
    counts_as_requirement_match: bool = False
    reason: str
    review_status: Literal['unreviewed','needs_review'] = 'unreviewed'

class ImpactEstimate(Schema):
    job_id: str
    current_score: float = Field(ge=0,le=100)
    simulated_score: float = Field(ge=0,le=100)
    delta: float = Field(ge=-100,le=100)
    saved_match_score: float | None = None
    confidence: Literal['low','medium'] = 'low'
    method: str = 'Fixed-text-feature counterfactual using existing hybrid scorer; not a promise.'

class GapRecord(Schema):
    id: str
    skill: str
    normalized: str
    category: str
    requirement_type: Literal['required','preferred','optional','mixed','unknown']
    original_wordings: list[str]
    affected_jobs: list[str]
    affected_count: int
    required_count: int
    preferred_count: int
    optional_count: int = 0
    corpus_required_count: int
    corpus_preferred_count: int
    priority: Literal['Critical','High','Medium','Low']
    priority_score: float = Field(ge=0,le=100)
    priority_components: dict
    priority_reason: str
    candidate_evidence: list[SkillEvidence]
    related_skills: list[dict]
    classification_counts: dict[str,int]
    learnability: str
    impact: list[ImpactEstimate]
    average_score_impact: float
    confidence: Literal['low','medium','high']
    review_status: Literal['unreviewed','needs_review']
    explanation: str
    guidance: list[dict]

class GapResults(Schema):
    inventory: list[InventorySkill]
    requirements: list[Requirement]
    comparisons: list[SkillComparison]
    gaps: list[GapRecord]
    jobs: list[dict]
    candidate_snapshot: dict
    selection: dict
    warnings: list[str]
    conflicts: list[dict] = Field(default_factory=list)
    validation: dict
    tasks: list[dict]
    provider_metadata: dict
    status: Literal['completed','partial','needs_review']

class GapRunResponse(Schema):
    id: str
    status: GapStatus
    stage: str
    profile_revision: int
    stale: bool
    request: dict
    config: dict
    job_count: int
    gap_count: int
    tasks: list[dict]
    warnings: list[str]
    provider_metadata: dict
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None
    elapsed_ms: int | None

class GapReviewInput(Schema):
    decision: Literal['reviewed','dismissed','needs_review']
    note: str = Field(default='',max_length=500)

class GuidanceChoice(Schema):
    gap_id: str
    action_ids: list[str] = Field(min_length=1,max_length=3)

class GuidanceChoices(Schema):
    selections: list[GuidanceChoice] = Field(max_length=8)

class GuidanceReview(Schema):
    issues: list[str] = Field(max_length=10)
