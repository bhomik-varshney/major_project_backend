"""Private, versioned audit contracts. Coordinates use normalized top-left regions."""
from typing import Literal
from pydantic import Field, model_validator
from app.schemas.profile import Schema
from app.schemas.extraction import CandidateDraft


class Region(Schema):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode='after')
    def within_page(self):
        if self.x + self.width > 1.001 or self.y + self.height > 1.001:
            raise ValueError('Region exceeds page')
        return self


class ConfidenceRecord(Schema):
    label: Literal['high','medium','low','unknown'] = 'unknown'
    score: float | None = Field(default=None, ge=0, le=1)
    basis: str = 'Heuristic; not a calibrated probability.'


class ValidationWarning(Schema):
    code: str
    message: str
    field: str = ''
    severity: Literal['info','warning','needs_review','failure'] = 'warning'
    sources: list[dict] = Field(default_factory=list)


class ExtractedTextBlock(Schema):
    block_id: int = Field(ge=1)
    text: str = Field(max_length=100000)
    page: int | None = Field(default=None, ge=1)
    paragraph: int | None = None
    order: int = Field(ge=0)
    method: Literal['text','ocr'] = 'text'
    region: Region | None = None
    metadata: dict = Field(default_factory=dict)


class OCRBlock(Schema):
    block_id: int = Field(ge=1)
    page: int = Field(ge=1)
    text: str = Field(max_length=2000)
    region: Region | None = None
    confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class PageArtifact(Schema):
    id: str
    page: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    sha256: str
    bytes: int = Field(ge=0)
    media_type: Literal['image/png'] = 'image/png'
    renderer: str
    coordinate_system: str = 'normalized_top_left'


class LayoutBlock(Schema):
    id: str = Field(max_length=100)
    page: int = Field(ge=1)
    section: str = Field(default='', max_length=160)
    raw_text: str = Field(default='', max_length=2000)
    block_type: Literal['heading','project','technology','experience','education','sidebar','table','date','link','bullet_group','contact','text','card','icon'] = 'text'
    region: Region | None = None
    related_block_ids: list[str] = Field(default_factory=list, max_length=40)
    source_block_ids: list[int] = Field(default_factory=list, max_length=40)
    reading_order: int = Field(default=0, ge=0)
    confidence: float = Field(default=0.5, ge=0, le=1)
    method: Literal['text','vision'] = 'vision'
    status: Literal['supported','needs_review'] = 'needs_review'
    warnings: list[str] = Field(default_factory=list, max_length=20)


class VisionLayout(Schema):
    layout_type: Literal['one_column','two_column','complex','unknown'] = 'unknown'
    blocks: list[LayoutBlock] = Field(default_factory=list, max_length=200)
    warnings: list[str] = Field(default_factory=list, max_length=30)


class EvidenceItem(Schema):
    field: str
    value: str
    source_text: str = ''
    page: int | None = None
    source_block_ids: list[int] = Field(default_factory=list)
    extraction_methods: list[str] = Field(default_factory=list)
    confidence: ConfidenceRecord = Field(default_factory=ConfidenceRecord)
    status: Literal['supported','needs_review','candidate_edited'] = 'needs_review'
    alternatives: list[dict] = Field(default_factory=list)


class ResumeDraft(Schema):
    values: CandidateDraft
    evidence: list[EvidenceItem] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    other_sections: list[dict] = Field(default_factory=list)


class AgentRun(Schema):
    name: str
    status: str
    model: str | None = None
    method: str = ''
    result: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None


class ReviewDecision(Schema):
    action: Literal['pending','confirmed','rejected','cancelled','needs_review'] = 'pending'
    revision: int = Field(default=1, ge=1)
    fields: list[str] = Field(default_factory=list)
    decided_at: str | None = None


class ConversionAttempt(Schema):
    attempt: int = Field(ge=1, le=2)
    stdout: str = Field(default='', max_length=8000)
    stderr: str = Field(default='', max_length=8000)
    exit_code: int | None = None
    timeout_seconds: float = Field(gt=0)
    timed_out: bool = False
    generated_file_exists: bool = False
    error: str | None = None


class ConversionAudit(Schema):
    status: Literal['completed', 'failed']
    attempts: list[ConversionAttempt] = Field(max_length=2)
    error: str | None = None


class ProcessingRun(Schema):
    schema_version: str = '1.0'
    pipeline_version: str = 'legacy'
    id: str
    document_id: str
    status: str
    stage: str
    quality: dict = Field(default_factory=dict)
    conversion: ConversionAudit | None = None
    pages: list[PageArtifact] = Field(default_factory=list)
    exact_text: list[ExtractedTextBlock] = Field(default_factory=list)
    ocr: list[OCRBlock] = Field(default_factory=list)
    layout: list[LayoutBlock] = Field(default_factory=list)
    agents: list[AgentRun] = Field(default_factory=list)
    warnings: list[ValidationWarning] = Field(default_factory=list)
    draft: ResumeDraft | None = None
    review: ReviewDecision = Field(default_factory=ReviewDecision)
    consent: dict = Field(default_factory=dict)
    duration_ms: int | None = None
    raw_evidence: list[dict] = Field(default_factory=list)
    canonical: dict | None = None
    canonical_initial: dict | None = None
    canonical_validation: dict | None = None
    repair_iterations: list[dict] = Field(default_factory=list)
    canonical_workflow: dict = Field(default_factory=dict)
    canonical_status: str | None = None
