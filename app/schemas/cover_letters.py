"""Cover letters are immutable versions; approval binds the rendered bytes."""
from datetime import date
from typing import Literal
from pydantic import Field, model_validator
from app.schemas.profile import Schema

SECTION_KEYS = ('opening', 'motivation', 'experience', 'skills_alignment', 'closing')

class Evidence(Schema):
    id: str
    source: str
    label: str
    text: str
    relevance: float = Field(ge=0, le=1)

class RelevanceSelection(Schema):
    evidence_ids: list[str] = Field(max_length=10)
    warnings: list[str] = Field(max_length=8)

class Sentence(Schema):
    id: str
    section: Literal['opening', 'motivation', 'experience', 'skills_alignment', 'closing']
    text: str
    evidence_ids: list[str]

class WritingSelection(Schema):
    sentence_ids: list[str] = Field(min_length=4, max_length=20)
    warnings: list[str] = Field(max_length=8)

class ContentReview(Schema):
    unsupported_sentence_ids: list[str] = Field(max_length=20)
    issues: list[str] = Field(max_length=8)

class LetterContent(Schema):
    date: date
    recipient_name: str = ''
    company_name: str = ''
    job_title: str
    salutation: str = 'Dear Hiring Manager,'
    opening: str
    motivation: str
    experience: str
    skills_alignment: str
    closing: str
    signature_name: str
    signature_phone: str = ''
    signature_email: str = ''
    claims: list[Sentence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: Literal['high', 'medium', 'low'] = 'medium'

class CoverStart(Schema):
    expected_application_revision: int = Field(ge=1)
    tone: Literal['formal', 'concise', 'warm'] = 'formal'
    length: Literal['standard', 'short', 'expanded'] = 'standard'
    document_date: date
    use_ai: bool = True
    allow_external_processing: bool = False
    include_signature: bool = True
    signature_id: str | None = Field(default=None, max_length=36)
    signature_approved: bool = False
    signature_width_mm: int = Field(default=38, ge=20, le=50)
    base_version_id: str | None = Field(default=None, max_length=36)
    regenerate_section: Literal['all', 'opening', 'motivation', 'experience', 'skills_alignment', 'closing'] = 'all'
    edits: dict[str, str] = Field(default_factory=dict, max_length=5)
    personal_sentence: str = Field(default='', max_length=350)
    user_text_confirmed: bool = False

    @model_validator(mode='after')
    def permissions(self):
        if self.use_ai and not self.allow_external_processing:
            raise ValueError('CrewAI generation requires consent to send selected profile evidence and job context to Groq.')
        if set(self.edits) - set(SECTION_KEYS) or any(len(v)>4000 for v in self.edits.values()):
            raise ValueError('Edit only cover letter paragraphs, each at most 4000 characters.')
        if (self.edits or self.personal_sentence) and not self.user_text_confirmed:
            raise ValueError('Confirm that your supplied text is accurate before creating a new version.')
        return self

class CoverAction(Schema):
    action: Literal['render', 'approve', 'attach', 'cancel']
    expected_revision: int = Field(ge=1)
    expected_application_revision: int | None = Field(default=None, ge=1)
    reviewed: bool = False
    signature_id: str | None = Field(default=None, max_length=36)
    include_signature: bool = True
    signature_approved: bool = False
    signature_width_mm: int = Field(default=38, ge=20, le=50)
