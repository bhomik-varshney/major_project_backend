from typing import Literal

from pydantic import Field, model_validator

from app.schemas.extraction import ProviderResume
from app.schemas.profile import Schema


class ResumeCrewInput(Schema):
    file_type: Literal['pdf','docx','txt']
    blocks: list[dict] = Field(max_length=10000)
    warnings: list[str] = Field(default_factory=list)
    compact_output: bool = False
    context: dict = Field(default_factory=dict)
    layout: list[dict] = Field(default_factory=list, max_length=200)


class QualityIssue(Schema):
    path: str = Field(max_length=160)
    code: str = Field(max_length=80)
    severity: Literal['warning','review_required'] = 'review_required'


class ResumeQualityReport(Schema):
    issues: list[QualityIssue] = Field(default_factory=list, max_length=200)
    warnings: list[str] = Field(default_factory=list, max_length=30)


class CompactQualityReport(ResumeQualityReport):
    # The AI review is a shortlist. The local validator still checks all fields.
    issues: list[QualityIssue] = Field(default_factory=list, max_length=8)
    warnings: list[str] = Field(default_factory=list, max_length=3)


class CrewTaskFailure(Schema):
    code: str
    message: str


class CrewExtractionResult(Schema):
    extraction: ProviderResume
    quality: ResumeQualityReport



class ResumeClaim(Schema):
    path: str = Field(max_length=120)
    value: str = Field(min_length=1,max_length=2000)
    block_id: int = Field(ge=1)
    confidence: Literal['high','medium','low','unknown'] = 'medium'


class ResumeClaims(Schema):
    claims: list[ResumeClaim] = Field(default_factory=list,max_length=120)
    warnings: list[str] = Field(default_factory=list,max_length=10)


class CompactCrewResult(Schema):
    extraction: ResumeClaims
    quality: ResumeQualityReport | None = None
    quality_error: CrewTaskFailure | None = None

    @model_validator(mode='after')
    def quality_outcome(self):
        if (self.quality is None)==(self.quality_error is None):
            raise ValueError('Exactly one quality outcome is required')
        return self
