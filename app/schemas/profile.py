import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Education(Schema):
    institution: str = Field(min_length=1, max_length=160)
    qualification: str = Field(min_length=1, max_length=160)
    period: str = Field(default="", max_length=80)
    location: str = Field(default="", max_length=160)
    start_year: str = Field(default="", max_length=20)
    end_year: str = Field(default="", max_length=20)
    expected_graduation: str = Field(default="", max_length=80)
    grade: str = Field(default="", max_length=80)
    specialization: str = Field(default="", max_length=160)


class Experience(Schema):
    organization: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    period: str = Field(default="", max_length=80)
    location: str = Field(default="", max_length=160)
    start_date: str = Field(default="", max_length=80)
    end_date: str = Field(default="", max_length=80)
    current_status: str = Field(default="", max_length=30)
    responsibilities: list[str] = Field(default_factory=list, max_length=30)
    achievements: list[str] = Field(default_factory=list, max_length=30)
    technologies: list[str] = Field(default_factory=list, max_length=40)


class Project(Schema):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    url: str = Field(default="", max_length=500)
    technologies: list[str] = Field(default_factory=list, max_length=40)
    role: str = Field(default="", max_length=160)
    period: str = Field(default="", max_length=80)
    links: list[str] = Field(default_factory=list, max_length=10)

    tools: list[str] = Field(default_factory=list,max_length=40)
    outcomes: list[str] = Field(default_factory=list,max_length=30)
    repository: str = Field(default="",max_length=500)
    deployment_url: str = Field(default="",max_length=500)

    @field_validator("url", "repository", "deployment_url")
    @classmethod
    def safe_link(cls, value):
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("Use an http or https project URL.")
        return value


class SkillSubmission(Schema):
    name: str = Field(min_length=1, max_length=160)
    source: str = "manual"
    verified: bool = False


class AdditionalResumeSection(Schema):
    heading: str = Field(min_length=1,max_length=160)
    content: str = Field(min_length=1,max_length=6000)


class ProfileInput(Schema):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=60)
    location: str = Field(default="", max_length=160)
    career_objective: str = Field(default="", max_length=2000)
    education: list[Education] = Field(default_factory=list, max_length=20)
    skills: list[str] = Field(default_factory=list, max_length=100)
    experience: list[Experience] = Field(default_factory=list, max_length=20)
    projects: list[Project] = Field(default_factory=list, max_length=30)
    preferred_roles: list[str] = Field(default_factory=list, max_length=20)
    preferred_locations: list[str] = Field(default_factory=list, max_length=20)
    experience_level: Literal["", "student", "graduate", "early_career", "experienced"] = ""
    certifications: list[str] = Field(default_factory=list, max_length=30)
    achievements: list[str] = Field(default_factory=list, max_length=30)
    additional_sections: list[AdditionalResumeSection] = Field(default_factory=list,max_length=30)
    publications: list[str] = Field(default_factory=list, max_length=30)
    links: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("skills", mode="before", json_schema_input_type=list[str | SkillSubmission])
    @classmethod
    def compatible_skills(cls, values):
        # Accept richer skill input without trusting client origin/verification claims.
        if isinstance(values, list):
            return [value.get("name", "") if isinstance(value, dict) else value for value in values]
        return values

    @field_validator("links")
    @classmethod
    def safe_links(cls, values):
        result = []
        for value in values:
            if len(value) > 500 or not value.startswith(("https://", "http://")):
                raise ValueError("Use http or https links of at most 500 characters.")
            if value not in result:
                result.append(value)
        return result

    @field_validator("email")
    @classmethod
    def email_shape(cls, value):
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("skills", "preferred_roles", "preferred_locations", "certifications", "achievements", "publications")
    @classmethod
    def normalize_labels(cls, values):
        result, seen = [], set()
        for value in values:
            label = " ".join(value.split())
            if not label or len(label) > 160:
                raise ValueError("Use non-empty entries of at most 160 characters.")
            if label.casefold() not in seen:
                result.append(label)
                seen.add(label.casefold())
        return result


class ProfileUpdate(ProfileInput):
    expected_revision: int = Field(ge=1)


class VerificationInput(Schema):
    expected_revision: int = Field(ge=1)
    confirmed: Literal[True]


class ProfileResponse(ProfileInput):
    id: str
    revision: int
    verification_state: Literal["unverified", "verified"]
    evidence_origin: Literal["manual", "document", "mixed"] = "manual"
    verified_at: datetime | None = None
    verified_fields: list[str] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    skill_details: list[dict] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
