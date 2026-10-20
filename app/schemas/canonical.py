"""Strict connected resume graph. Evidence and model claims have separate authority."""
from typing import Literal
from pydantic import Field, model_validator
from app.schemas.profile import Schema
from app.schemas.intelligence import ConfidenceRecord, Region

Bucket = Literal['identity','summary','objective','education','experience','internships','projects','skills','tools','programming_languages','frameworks','databases','cloud','certifications','courses','achievements','awards','publications','research','volunteer','leadership','extracurricular','languages','interests','links','references','other','unclassified']

class RawEvidence(Schema):
    evidence_id: str
    source_block_id: int = Field(ge=1)
    source_text: str
    page: int | None = None
    paragraph: int | None = None
    order: int = Field(ge=0)
    coordinates: Region | None = None
    extraction_method: Literal['text','ocr']
    confidence: ConfidenceRecord
    metadata: dict = Field(default_factory=dict)

class CanonicalValue(Schema):
    fact_id: str
    profile_path: str | None = None
    value: str | None
    proposed_value: str | None = None
    normalized_value: str | None = None
    original_text: str
    page: int | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    source_block_ids: list[int] = Field(default_factory=list)
    extraction_methods: list[str] = Field(default_factory=list)
    confidence: ConfidenceRecord
    status: Literal['supported','needs_review','conflict','unsupported']
    warnings: list[str] = Field(default_factory=list)
    related_entities: list[str] = Field(default_factory=list)

class CanonicalEntity(Schema):
    entity_id: str
    bucket: Bucket
    fields: dict[str,CanonicalValue]
    evidence_ids: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    status: Literal['supported','needs_review','conflict'] = 'needs_review'

class CanonicalSection(Schema):
    section_id: str
    bucket: Bucket
    heading: str
    original_heading: str
    evidence_ids: list[str]
    source_block_ids: list[int]
    page: int | None = None
    column: str
    detection_method: Literal['heading','layout_supported','unclassified','contact_context']

class CanonicalRelation(Schema):
    source_id: str
    target_id: str
    relation: Literal['has_field','contains','associated_with']
    evidence_ids: list[str]
    status: Literal['supported','needs_review'] = 'supported'

class SourceAlternative(Schema):
    value: str
    extraction_method: str
    evidence_ids: list[str] = Field(default_factory=list)
    source_block_ids: list[int] = Field(default_factory=list)
    page: int | None = None
    confidence: ConfidenceRecord = Field(default_factory=ConfidenceRecord)

class ResumeConflict(Schema):
    conflict_id: str
    field: str
    code: str
    alternatives: list[SourceAlternative]
    resolution: Literal['unresolved','native_preferred','user_required'] = 'unresolved'
    selected_value: str | None = None
    message: str

class CanonicalResume(Schema):
    schema_version: Literal['canonical-1'] = 'canonical-1'
    run_id: str
    entities: list[CanonicalEntity]
    sections: list[CanonicalSection]
    relations: list[CanonicalRelation]
    conflicts: list[ResumeConflict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def graph_integrity(self):
        entities={e.entity_id for e in self.entities};sections={s.section_id for s in self.sections}
        facts=[f.fact_id for e in self.entities for f in e.fields.values()]
        if len(entities)!=len(self.entities) or len(sections)!=len(self.sections) or len(set(facts))!=len(facts):raise ValueError('Duplicate graph identifiers')
        known=entities|sections|set(facts)
        for e in self.entities:
            if not set(e.section_ids)<=sections:raise ValueError('Unknown section relation')
            for f in e.fields.values():
                if not set(f.related_entities)<=entities:raise ValueError('Unknown related entity')
        if any(r.source_id not in known or r.target_id not in known for r in self.relations):raise ValueError('Dangling graph relation')
        return self

class CanonicalIssue(Schema):
    code: str
    field: str = ''
    severity: Literal['warning','needs_review','failure']
    message: str
    repairable: bool = False

class CanonicalValidation(Schema):
    schema_valid: bool = True
    checked_fields: int
    supported_fields: int
    unsupported_fields: int
    critical_passed: bool
    issues: list[CanonicalIssue]
    checks: dict[str,int]

class RepairIteration(Schema):
    iteration: int = Field(ge=0)
    detected_problems: list[CanonicalIssue]
    repair_action: str
    affected_fields: list[str]
    validation_result: CanonicalValidation
    model: str | None = None
    provider: str = 'local'
    elapsed_ms: int = Field(ge=0)
    outcome: Literal['validated','needs_review','no_progress','unavailable','skipped']
    error_code: str | None = None
    changes: list[dict] = Field(default_factory=list)

class CanonicalRepairInput(Schema):
    file_type: str
    blocks: list[dict]
    issues: list[dict]
    allowed_paths: list[str]
    context: dict
