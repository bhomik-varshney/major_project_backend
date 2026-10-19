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


class Experience(Schema):
    organization: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    period: str = Field(default="", max_length=80)


class Project(Schema):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    url: str = Field(default="", max_length=500)

    @field_validator("url")
    @classmethod
    def safe_link(cls, value):
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("Use an http or https project URL.")
        return value


class ProfileInput(Schema):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(default="", max_length=254)
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

    @field_validator("email")
    @classmethod
    def email_shape(cls, value):
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("skills", "preferred_roles", "preferred_locations", "certifications", "achievements")
    @classmethod
    def normalize_labels(cls, values):
        result, seen = [], set()
        for value in values:
            label = value.strip()
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
    evidence_origin: Literal["manual"] = "manual"
    created_at: datetime
    updated_at: datetime
