from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.profile import Schema

WorkMode = Literal['remote', 'hybrid', 'onsite']
EmploymentType = Literal['internship', 'full_time', 'contract', 'apprenticeship']
ExperienceLevel = Literal['student', 'fresher', 'graduate', 'entry_level', 'junior', 'limited_experience']
Currency = Literal['INR', 'USD', 'GBP', 'EUR', 'CAD', 'SGD']


class Salary(Schema):
    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)
    currency: Currency = 'INR'
    period: Literal['month', 'year'] = 'month'

    @model_validator(mode='after')
    def ordered(self):
        if self.minimum > self.maximum:
            raise ValueError('Invalid salary range')
        return self


class JobMetadata(Schema):
    dataset_version: str
    country: str
    category: str
    is_demo: Literal[True] = True
    company_size: Literal['small', 'medium', 'large'] | None = None
    edge_cases: list[str] = Field(default_factory=list)
    duplicate_group: str | None = None
    seed: int
    reference_date: date


class Job(Schema):
    id: str = Field(pattern=r'^demo-[a-z0-9-]+$', max_length=100)
    title: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=160)
    industry: str = Field(min_length=1, max_length=160)
    department: str = Field(max_length=160)
    location: str = Field(min_length=1, max_length=160)
    country: str = Field(min_length=1, max_length=80)
    remote: bool  # Backward-compatible: true means fully remote; false includes hybrid.
    work_mode: WorkMode
    employment_type: EmploymentType
    experience_level: ExperienceLevel
    experience_years_min: int = Field(ge=0, le=3)
    experience_years_max: int = Field(ge=0, le=3)
    description: str = Field(max_length=5000)  # Intentionally empty in labeled edge cases.
    responsibilities: list[str] = Field(max_length=12)
    required_skills: list[str] = Field(default_factory=list, max_length=30)
    preferred_skills: list[str] = Field(max_length=30)
    tools_and_technologies: list[str] = Field(max_length=30)
    education_requirements: list[str] = Field(max_length=10)
    language_requirements: list[str] = Field(max_length=10)
    normalized_skill_tokens: list[str] = Field(default_factory=list, max_length=60)
    category: str = Field(min_length=1, max_length=80)
    subcategory: str = ''
    title_tokens: list[str] = Field(default_factory=list)
    category_tokens: list[str] = Field(default_factory=list)
    searchable_text: str = ''
    embedding_metadata: dict = Field(default_factory=dict)
    salary: Salary | None = None
    currency: Currency
    source: Literal['seeded_mock']
    generated_data: Literal[True]
    application_url: str = Field(pattern=r'^https://example\.invalid/jobs/[a-z0-9-]+$')
    source_url: str = Field(pattern=r'^https://example\.invalid/jobs/[a-z0-9-]+$')
    posted_date: date
    closing_date: date | None = None
    metadata: JobMetadata

    @model_validator(mode='after')
    def consistent(self):
        from app.services.job_normalization import normalized_skills
        if self.closing_date and self.closing_date < self.posted_date:
            raise ValueError('Invalid job date range')
        if self.experience_years_min > self.experience_years_max:
            raise ValueError('Invalid experience range')
        if self.remote != (self.work_mode == 'remote'):
            raise ValueError('Conflicting work mode')
        if self.salary and self.salary.currency != self.currency:
            raise ValueError('Conflicting salary currency')
        if self.category != self.metadata.category or self.country != self.metadata.country:
            raise ValueError('Conflicting normalized metadata')
        expected_tokens=normalized_skills(self.required_skills + self.preferred_skills)
        if self.metadata.dataset_version=='2026-10-demo-v2':
            # Historical snapshots retain their original v2 token vocabulary.
            # New-provider records are v3-only and use conservative C/C++ tokens.
            legacy_tokens=sorted({'c++' if token=='c/c++' else token for token in expected_tokens})
            valid_tokens=self.normalized_skill_tokens in (expected_tokens,legacy_tokens)
        else:valid_tokens=self.normalized_skill_tokens==expected_tokens
        if not valid_tokens:
            raise ValueError('Skill tokens are not canonical')
        if self.source_url != f'https://example.invalid/jobs/{self.id}' or self.source_url != self.application_url:
            raise ValueError('Conflicting demo URL')
        return self


class JobFilters(Schema):
    role: str = Field(default='', max_length=120)
    category_scope: Literal['exact','family'] = 'family'
    location_scope: Literal['city','compatible','state','country'] = 'city'
    keyword: str = Field(default='', max_length=120)
    category: str = Field(default='', max_length=80)
    skills: list[str] = Field(default_factory=list, max_length=20)
    location: str = Field(default='', max_length=120)
    country: str = Field(default='', max_length=80)
    remote: bool | None = None
    work_mode: WorkMode | None = None
    employment_type: EmploymentType | None = None
    experience: ExperienceLevel | None = None
    company: str = Field(default='', max_length=160)
    industry: str = Field(default='', max_length=160)
    posted_from: date | None = None
    posted_to: date | None = None
    availability: Literal['all', 'active', 'expired', 'future'] = 'all'
    as_of: date = date(2026, 10, 19)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=6, ge=1, le=50)
    max_results: int = Field(default=10000, ge=1, le=10000)

    @model_validator(mode='before')
    @classmethod
    def empty_values(cls,data):
        if isinstance(data,dict):
            data=dict(data)
            for field in ('keyword','role','category','location','country','company','industry'):
                if data.get(field) is None:data[field]=''
                if field in data and isinstance(data[field],str):data[field]=data[field].strip()
            if data.get('skills') is None:data['skills']=[]
            for field in ('work_mode','employment_type','experience','remote'):
                if data.get(field)=='':data[field]=None
        return data

    @model_validator(mode='after')
    def date_range(self):
        if self.posted_from and self.posted_to and self.posted_from > self.posted_to:
            raise ValueError('Posted date range is reversed')
        return self


class JobPage(Schema):
    audit: dict = Field(default_factory=dict)
    match_context: dict[str,dict] = Field(default_factory=dict)
    items: list[Job]
    total: int
    matched_total: int
    page: int
    page_size: int
    source: Literal['seeded_mock'] = 'seeded_mock'
    dataset_version: str
    reference_date: date
    warnings: list[str] = Field(default_factory=list)
    filters: JobFilters


class JobOptions(Schema):
    categories: list[str]
    industries: list[str]
    countries: list[str]
    employment_types: list[str]
    experience_levels: list[str]
    work_modes: list[str]
    total: int
    dataset_version: str
    reference_date: date
