from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.schemas.profile import Education, Experience, ProfileInput, Project, Schema, AdditionalResumeSection
from app.schemas.agent_run import AgentRunResponse

ProfileField = Literal["name", "email", "phone", "location", "career_objective", "education", "skills", "experience", "projects", "preferred_roles", "preferred_locations", "experience_level", "certifications", "achievements", "publications", "links", "additional_sections"]


class EducationDraft(Education):
    institution: str = Field(default="", max_length=170)
    qualification: str = Field(default="", max_length=170)


class ExperienceDraft(Experience):
    organization: str = Field(default="", max_length=170)
    role: str = Field(default="", max_length=170)


class ProjectDraft(Project):
    name: str = Field(default="", max_length=170)


class AdditionalSectionDraft(AdditionalResumeSection):
    heading: str = Field(default="",max_length=160)
    content: str = Field(default="",max_length=6000)


class CandidateDraft(ProfileInput):
    name: str = Field(default="", max_length=120)
    additional_sections: list[AdditionalSectionDraft] = Field(default_factory=list,max_length=30)
    education: list[EducationDraft] = Field(default_factory=list, max_length=20)
    experience: list[ExperienceDraft] = Field(default_factory=list, max_length=20)
    projects: list[ProjectDraft] = Field(default_factory=list, max_length=30)


class TextPosition(Schema):
    x: float
    y: float
    font_size: float


class SourceReference(Schema):
    block_id: int = Field(ge=1)
    page: int | None = Field(default=None, ge=1)
    paragraph: int | None = Field(default=None, ge=1)
    quote: str = Field(max_length=2000)
    line: int | None = None
    position: TextPosition | None = None


class FieldEvidence(Schema):
    canonical_fact_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    source: SourceReference | None = None
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"
    method: Literal["deterministic", "groq", "user"] = "deterministic"
    warning: str | None = Field(default=None, max_length=300)
    extraction_methods: list[str] = Field(default_factory=list)
    layout_block_ids: list[str] = Field(default_factory=list)
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    edited: bool = False
    parser_version: str = "legacy"
    classification: Literal["directly_found", "normalized", "ambiguous", "not_found"] = "directly_found"
    source_section: str = ""
    original_text: str = ""
    heading: str = ""
    statement_type: str = ""
    group_id: str = ""
    references: list[SourceReference] = Field(default_factory=list)
    edited_by_user: bool = False
    verified: bool = False
    review_status: Literal["pending", "confirmed", "not_applied", "not_detected"] = "pending"


class ProcessInput(Schema):
    use_groq: bool = False
    allow_external_processing: bool = False
    restart: bool = False
    use_vision: bool = False
    allow_image_processing: bool = False


class DraftUpdate(Schema):
    processing_id: str
    expected_revision: int = Field(ge=1)
    values: CandidateDraft


class ExtractionVerification(Schema):
    processing_id: str
    expected_revision: int = Field(ge=1)
    expected_profile_revision: int = Field(ge=0)
    fields: list[ProfileField] = Field(min_length=1, max_length=17)
    replace_fields: list[ProfileField] = Field(default_factory=list, max_length=17)
    confirmed: Literal[True]


class CorrectionInput(Schema):
    processing_id: str
    expected_revision: int = Field(ge=1)
    action: Literal["correction", "reject", "cancel"]


class ExtractionStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    document_id: str
    run_version: int
    revision: int
    status: str
    stage: str
    method: str
    warnings: list[str]
    error_code: str | None
    error_message: str | None
    page_count: int | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    verified_at: datetime | None
    agent_run: AgentRunResponse | None = None


class ExtractedDataResponse(ExtractionStatus):
    values: CandidateDraft
    evidence: dict[str, FieldEvidence]
    current_profile_revision: int
    current_profile_values: dict
    extraction_available: bool = True
    applied_fields: list[str] = Field(default_factory=list)
    structured: "StructuredResume" = Field(default_factory=lambda: StructuredResume())
    skill_details: list[dict] = Field(default_factory=list)


class AIClaim(Schema):
    path: str = Field(max_length=120)
    block_id: int = Field(ge=1)
    quote: str = Field(max_length=2000)
    confidence: Literal["high", "medium", "low", "unknown"]
    warning: str | None = Field(default=None, max_length=300)
    inferred: bool = False
    line: int | None = None
    classification: Literal["directly_found", "normalized", "ambiguous", "not_found"] = "directly_found"


class AIProposal(Schema):
    values: CandidateDraft
    claims: list[AIClaim] = Field(max_length=500)


class ResumeFact(Schema):
    value: str = Field(default="", max_length=2000)
    evidence: SourceReference | None = None
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"
    classification: Literal["directly_found", "normalized", "ambiguous", "not_found"] = "not_found"
    method: Literal["deterministic", "groq", "user"] = "groq"
    review_status: Literal["pending", "confirmed", "not_applied", "not_detected"] = "pending"
    edited_by_user: bool = False
    verified: bool = False
    original_text: str = ""
    source_section: str = ""
    heading: str = ""
    statement_type: str = ""
    warning: str | None = None


def fact_record(name, model, fact=ResumeFact):
    return create_model(name, __base__=Schema, **{
        key: (list[fact], Field(default_factory=list)) if field.annotation == list[str]
        else (fact, Field(default_factory=fact))
        for key, field in model.model_fields.items()
    })


ResumeEducation = fact_record("ResumeEducation", EducationDraft)
ResumeExperience = fact_record("ResumeExperience", ExperienceDraft)
ResumeProject = fact_record("ResumeProject", ProjectDraft)
PersonalInfo = create_model("PersonalInfo", __base__=Schema, **{
    key: (ResumeFact, Field(default_factory=ResumeFact)) for key in ("name", "email", "phone", "location")
})


class ProfileStatement(Schema):
    text: ResumeFact = Field(default_factory=ResumeFact)
    heading: str = ""
    type: Literal["summary", "objective", "profile_statement", "not_found"] = "not_found"


class ExtractionMetadata(Schema):
    parser_version: str = "2"
    method: str = "groq"
    reading_order_uncertain: bool = False


class StructuredResume(Schema):
    personal_info: PersonalInfo = Field(default_factory=PersonalInfo)
    profile_statement: ProfileStatement = Field(default_factory=ProfileStatement)
    education: list[ResumeEducation] = Field(default_factory=list, max_length=20)
    skills: list[ResumeFact] = Field(default_factory=list, max_length=100)
    experience: list[ResumeExperience] = Field(default_factory=list, max_length=20)
    projects: list[ResumeProject] = Field(default_factory=list, max_length=30)
    certifications: list[ResumeFact] = Field(default_factory=list, max_length=30)
    achievements: list[ResumeFact] = Field(default_factory=list, max_length=30)
    publications: list[ResumeFact] = Field(default_factory=list, max_length=30)
    links: list[ResumeFact] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list)
    extraction_metadata: ExtractionMetadata = Field(default_factory=ExtractionMetadata)


class ProviderSource(Schema):
    block_id: int = Field(ge=1)
    quote: str = Field(max_length=2000)


class ProviderFact(Schema):
    """Confirmation, edit/origin and position metadata are established server-side."""
    value: str = Field(default="", max_length=2000)
    evidence: ProviderSource | None = None
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"
    classification: Literal["directly_found", "normalized", "ambiguous", "not_found"] = "not_found"
    method: Literal["groq"] = "groq"
    review_status: Literal["pending", "not_detected"] = "pending"
    warning: str | None = None


ProviderEducation = fact_record("ProviderEducation", EducationDraft, ProviderFact)
ProviderExperience = fact_record("ProviderExperience", ExperienceDraft, ProviderFact)
ProviderProject = fact_record("ProviderProject", ProjectDraft, ProviderFact)
ProviderPersonalInfo = create_model("ProviderPersonalInfo", __base__=Schema, **{
    key: (ProviderFact, Field(default_factory=ProviderFact)) for key in ("name", "email", "phone", "location")
})


class ProviderStatement(Schema):
    text: ProviderFact = Field(default_factory=ProviderFact)
    heading: str = ""
    type: Literal["summary", "objective", "profile_statement", "not_found"] = "not_found"


class ProviderResume(Schema):
    personal_info: ProviderPersonalInfo = Field(default_factory=ProviderPersonalInfo)
    profile_statement: ProviderStatement = Field(default_factory=ProviderStatement)
    education: list[ProviderEducation] = Field(default_factory=list, max_length=20)
    skills: list[ProviderFact] = Field(default_factory=list, max_length=100)
    experience: list[ProviderExperience] = Field(default_factory=list, max_length=20)
    projects: list[ProviderProject] = Field(default_factory=list, max_length=30)
    certifications: list[ProviderFact] = Field(default_factory=list, max_length=30)
    achievements: list[ProviderFact] = Field(default_factory=list, max_length=30)
    publications: list[ProviderFact] = Field(default_factory=list, max_length=30)
    links: list[ProviderFact] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list)
    extraction_metadata: ExtractionMetadata = Field(default_factory=ExtractionMetadata)


ExtractedDataResponse.model_rebuild()
