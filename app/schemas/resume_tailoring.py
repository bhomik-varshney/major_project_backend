"""Strict resume proposals contain source IDs and approved wording variants."""
from typing import Literal
from pydantic import Field,model_validator
from app.schemas.profile import Schema

SECTIONS=('summary','experience','projects','skills','education','certifications','achievements','publications')

class ResumeSelection(Schema):
    entry_ids:list[str]=Field(max_length=240)
    reasons:list[str]=Field(max_length=12)

class ResumeWriting(Schema):
    summary_id:str
    entry_ids:list[str]=Field(max_length=240)
    bullet_ids:list[str]=Field(max_length=240)
    section_order:list[str]=Field(max_length=8)

class ResumeReview(Schema):
    unsupported_ids:list[str]=Field(max_length=30)
    issues:list[str]=Field(max_length=12)

class ResumeEntry(Schema):
    id:str
    section:Literal['experience','projects','skills','education','certifications','achievements','publications']
    source:str
    title:str
    subtitle:str=''
    period:str=''
    location:str=''
    grade:str=''
    technologies:list[str]=Field(default_factory=list)
    links:list[dict]=Field(default_factory=list)
    bullets:list[dict]=Field(default_factory=list)
    relevance:float=Field(ge=0,le=1)
    skill_group:str=''

class ResumeDraft(Schema):
    name:str
    phone:str=''
    email:str=''
    location:str=''
    links:list[dict]=Field(default_factory=list)
    target_job:str
    summary:str
    summary_id:str
    summary_evidence:list[str]
    entries:list[ResumeEntry]
    section_order:list[str]

class ResumeStart(Schema):
    expected_application_revision:int=Field(ge=1)
    template:Literal['supplied-navy-v1']='supplied-navy-v1'
    input_mode:Literal['confirmed_profile','processed_pdf','latex_source','template_profile']='confirmed_profile'
    source_document_id:str|None=Field(default=None,max_length=36)
    use_ai:bool=True
    allow_external_processing:bool=False
    base_version_id:str|None=Field(default=None,max_length=36)
    regenerate_section:Literal['all','summary','experience','projects','skills','education','certifications','achievements','publications']='all'
    edits:dict=Field(default_factory=dict,max_length=4)

    @model_validator(mode='after')
    def valid_edits(self):
        if self.use_ai and not self.allow_external_processing:raise ValueError('CrewAI tailoring needs consent to send selected profile evidence and job context to Groq.')
        if set(self.edits)-{'summary','entry_ids','bullet_text','section_order'}:raise ValueError('Edit only summary wording, selected entries, supported bullets and section order.')
        if self.edits and not self.base_version_id:raise ValueError('Select a base resume version before editing.')
        return self

class ResumeAction(Schema):
    action:Literal['compile','approve','attach','cancel']
    expected_revision:int=Field(ge=1)
    expected_application_revision:int|None=Field(default=None,ge=1)
    reviewed:bool=False
