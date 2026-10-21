"""Application contracts: suggestions never constitute permission to act."""
from datetime import datetime
import re
from typing import Literal
from pydantic import Field, model_validator
from app.schemas.profile import Schema
from app.schemas.application_preview import ApplicationPreview

ApplicationStatus = Literal['job_selected','preparing','draft_ready','needs_user_review','approved_for_prefill','prefilling','prefill_complete','needs_submission_approval','submitted_by_user','tracking_active','interview','offer','accepted','rejected','withdrawn','closed','failed','cancelled','partially_completed','waiting_for_user','external_site_unavailable','unsupported_form','duplicate_application','recruiter_review','assessment']
FieldType = Literal['text','email','tel','textarea','select','radio','checkbox','date','file','multiselect','unsupported']
DemoScenario = Literal['standard','graduate','partial','upload_failure','authentication','unavailable','unsupported','schema_change']

class FormField(Schema):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,79}$')
    label: str = Field(min_length=1,max_length=160)
    type: FieldType = 'text'
    required: bool = False
    options: list[str] = Field(default_factory=list,max_length=100)
    semantic: str = Field(default='',max_length=80)
    sensitive: bool = False
    original_type: str = Field(default='',max_length=80)

    @model_validator(mode='before')
    @classmethod
    def unknown_type(cls,data):
        if isinstance(data,dict) and re.search(r'password|passwd|one.?time.?code|session.?token|api.?key|auth(?:orization)?.?token|two.?factor|security.?code', ' '.join(str(data.get(key,'')) for key in ('id','label','semantic')),re.I):
            return {**data,'original_type':str(data.get('type','text'))[:80],'type':'unsupported'}
        if isinstance(data,dict) and data.get('type','text') not in {'text','email','tel','textarea','select','radio','checkbox','date','file','multiselect','unsupported'}:
            data={**data,'original_type':str(data['type'])[:80],'type':'unsupported'}
        return data

class ApplicationForm(Schema):
    id: str = Field(min_length=1,max_length=100)
    version: str = Field(min_length=1,max_length=80)
    fields: list[FormField] = Field(min_length=1,max_length=80)
    resource_uri: str = 'ui://ascendra/application'
    provider: str = 'demo'
    is_demo: bool = True
    @model_validator(mode='after')
    def unique_fields(self):
        if len({f.id for f in self.fields}) != len(self.fields):raise ValueError('Duplicate form field IDs')
        return self

class ApplicationField(Schema):
    id: str
    value: str | bool | list[str] | None = None
    source: str = ''
    evidence: str = ''
    confidence: Literal['high','medium','unknown'] = 'unknown'
    classification: Literal['safe_to_prefill','user_confirmation_required','unavailable'] = 'unavailable'
    approved: bool = False
    edited_by_user: bool = False
    warning: str = ''
    auto_filled: bool = False
    confirmation_required: bool = True
    suggested_answer: bool = False

class ApplicationDraft(Schema):
    form: ApplicationForm
    fields: list[ApplicationField]
    documents: list[dict] = Field(default_factory=list,max_length=10)
    document_options: list[dict] = Field(default_factory=list,max_length=100)
    requirements: dict = Field(default_factory=dict)
    missing_inputs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    tasks: list[dict] = Field(default_factory=list)
    validation: dict = Field(default_factory=dict)
    ai_review: dict = Field(default_factory=lambda:{'status':'not_selected'})
    preview: ApplicationPreview | None = None
    presentation_version: str = ""
    connection: dict = Field(default_factory=lambda:{'status':'connected','form_source':'mcp'})

class ApplicationStart(Schema):
    job_id: str = Field(min_length=1,max_length=100)
    matching_run_id: str | None = Field(default=None,max_length=36)
    provider: Literal['demo','configured'] = 'demo'
    use_ai: bool = False
    allow_external_processing: bool = False
    demo_scenario: DemoScenario = 'standard'
    @model_validator(mode='after')
    def consent(self):
        if self.use_ai and not self.allow_external_processing:raise ValueError('Optional agent review requires explicit consent')
        return self

class FieldReview(Schema):
    value: str | bool | list[str] | None = None
    approved: bool = False

class DocumentChoice(Schema):
    document_id: str = Field(max_length=36)
    role: Literal['resume','cover_letter','supporting']
    approved: Literal[True]

class ApplicationAction(Schema):
    action: Literal['prepare','review','approve_prefill','prefill','approve_submission','record_submission','start_tracking','cancel','retry','pause','resume','retry_prefill','reconcile','open_external','continue_manually','generate_preview']
    expected_revision: int = Field(ge=1)
    fields: dict[str,FieldReview] = Field(default_factory=dict,max_length=80)
    documents: list[DocumentChoice] = Field(default_factory=list,max_length=10)
    confirmed: bool = False
    reference: str = Field(default='',max_length=160)

class TrackingInput(Schema):
    expected_revision: int = Field(ge=1)
    kind: Literal['note','deadline','interview','follow_up','status']
    text: str = Field(default='',max_length=2000)
    due_at: datetime | None = None
    status: Literal['recruiter_review','assessment','interview','offer','accepted','rejected','withdrawn','closed'] | None = None
    @model_validator(mode='after')
    def meaningful(self):
        if self.due_at and self.due_at.tzinfo is None:raise ValueError('Use a timestamp with a timezone')
        if self.kind=='status' and not self.status:raise ValueError('Choose a status')
        if self.kind!='status' and self.status:raise ValueError('Status belongs to a status event')
        if self.kind in {'deadline','interview','follow_up'} and not self.due_at:raise ValueError('Choose a date and time')
        if self.kind=='note' and not self.text:raise ValueError('Enter a note')
        return self

class ApplicationResponse(Schema):
    id: str
    revision: int
    job_id: str
    job: dict
    status: ApplicationStatus
    stage: str
    provider: str
    is_demo: bool
    profile_revision: int
    stale: bool
    draft: ApplicationDraft | None = None
    approval: dict = Field(default_factory=dict)
    receipt: dict = Field(default_factory=dict)
    tasks: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    milestones: dict = Field(default_factory=dict)
    recovery: dict = Field(default_factory=dict)
    match_context: dict = Field(default_factory=dict)
    tracking: dict = Field(default_factory=dict)
    operation_active: bool = False
    created_at: datetime
    updated_at: datetime
    lifecycle_status: str = "saved"

class AgentReview(Schema):
    flagged_field_ids: list[str] = Field(default_factory=list,max_length=80)
    recommended_document_ids: list[str] = Field(default_factory=list,max_length=10)
    issues: list[Literal['ambiguous_mapping','missing_input','document_review','sensitive_input','manual_review']] = Field(default_factory=list,max_length=10)

class AppToolInput(Schema):
    name: Literal['application_state','review_application']
    expected_revision: int = Field(ge=1)
    fields: dict[str,FieldReview] = Field(default_factory=dict,max_length=80)

class TaskCompletion(Schema):
    expected_revision: int = Field(ge=1)
