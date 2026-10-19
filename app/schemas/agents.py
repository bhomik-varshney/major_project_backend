from typing import Literal

from pydantic import Field

from app.schemas.extraction import ProviderResume
from app.schemas.profile import Schema


class ResumeCrewInput(Schema):
    file_type: Literal['pdf','docx','txt']
    blocks: list[dict] = Field(max_length=10000)
    warnings: list[str] = Field(default_factory=list)


class QualityIssue(Schema):
    path: str = Field(max_length=160)
    code: str = Field(max_length=80)
    severity: Literal['warning','review_required'] = 'review_required'


class ResumeQualityReport(Schema):
    issues: list[QualityIssue] = Field(default_factory=list, max_length=200)
    warnings: list[str] = Field(default_factory=list, max_length=30)


class CrewExtractionResult(Schema):
    extraction: ProviderResume
    quality: ResumeQualityReport

