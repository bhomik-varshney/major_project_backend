"""Strict presentation contracts, separate from profile storage and write packets."""
from typing import Literal
from pydantic import Field
from app.schemas.profile import Schema

PreviewStatus=Literal['ready','safe_to_prefill','generated_draft','user_confirmation_required','missing','unsupported','sensitive','conflict','rejected','prefilling','completed','failed']

class LinkClassification(Schema):
    original_url: str
    url: str
    platform: str
    kind: Literal['professional_profile','repository','deployment','portfolio','publication','additional']
    label: str
    primary: bool=False
    source: str
    evidence: str

class RepeatedProfileEntry(Schema):
    title: str
    subtitle: str=''
    details: dict[str,str]=Field(default_factory=dict)
    bullets: list[str]=Field(default_factory=list)
    links: list[LinkClassification]=Field(default_factory=list)
    source: str=''

class PreparedAnswer(Schema):
    question_type: str
    concise: str=''
    detailed: str=''
    evidence: list[str]=Field(default_factory=list)
    source_paths: list[str]=Field(default_factory=list)
    confidence: float=Field(default=0,ge=0,le=1)
    generated_draft: bool=True
    requires_confirmation: bool=True
    reason: str=''

class ApplicationQuestion(Schema):
    field_id: str
    label: str
    answer: PreparedAnswer

class MissingField(Schema):
    field_id: str
    label: str
    required: bool=False
    reason: str

class MCPFieldMapping(Schema):
    field_id: str
    label: str
    source_label: str
    status: PreviewStatus
    required: bool=False

class PreviewField(Schema):
    field_id: str
    label: str
    input_type: str
    display_value: str|bool|list[str]|None=None
    status: PreviewStatus='missing'
    source_label: str='User input needed'
    confidence: float=Field(default=0,ge=0,le=1)
    requires_confirmation: bool=True
    approved: bool=False
    required: bool=False
    auto_filled: bool=False
    sensitive: bool=False
    reason: str=''
    evidence: list[str]=Field(default_factory=list)
    answer: PreparedAnswer|None=None

class ApplicationSection(Schema):
    id: str
    label: str
    fields: list[PreviewField]=Field(default_factory=list)
    entries: list[RepeatedProfileEntry]=Field(default_factory=list)
    links: list[LinkClassification]=Field(default_factory=list)

class PreviewDocument(Schema):
    label: str
    role: str
    content_type: str
    size_bytes: int=Field(ge=0)
    approved: bool=False

class ApplicationPreview(Schema):
    version: Literal['1.0']='1.0'
    title: str
    company: str
    source: str
    application_url: str=''
    sections: list[ApplicationSection]
    documents: list[PreviewDocument]=Field(default_factory=list)
    missing_fields: list[MissingField]=Field(default_factory=list)
    mappings: list[MCPFieldMapping]=Field(default_factory=list)
    engine: Literal['local_mcp','local_fallback','legacy_local']='local_mcp'
    is_mock: bool=True
    content_hash: str=''
