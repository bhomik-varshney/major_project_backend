from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["development", "test"] = "development"
    version: str = "0.1.0"
    database_url: str = "sqlite:///./.data/careeros.db"
    data_dir: Path = Path(".data")
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080"
    session_secret: str = Field(default="", repr=False)
    max_document_bytes: int = Field(default=5 * 1024 * 1024, ge=1, le=20 * 1024 * 1024)
    max_documents_per_user: int = Field(default=20, ge=1, le=100)
    auto_init_db: bool = True
    document_storage_path: Path = Path(".data/documents")
    max_document_size_mb: float | None = Field(default=None, gt=0, le=20)
    max_extraction_pages: int = Field(default=50, ge=1, le=100)
    max_extraction_chars: int = Field(default=60000, ge=1000, le=100000)
    groq_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    groq_api_key_1: SecretStr = Field(default=SecretStr(""), repr=False)
    groq_api_key_2: SecretStr = Field(default=SecretStr(""), repr=False)
    groq_api_key_3: SecretStr = Field(default=SecretStr(""), repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    groq_text_model: str = ''
    groq_vision_model_primary: str = 'qwen/qwen3.8-27b'
    groq_vision_model_fallback: str = ''
    render_max_pages: int = Field(default=12, ge=1, le=20)
    vision_max_pages: int = Field(default=3, ge=1, le=5)
    render_max_dimension: int = Field(default=2200, ge=800, le=2400)
    resume_max_repair_cycles: int = Field(default=3,ge=0,le=3)
    resume_repair_timeout_seconds: float = Field(default=45,ge=1,le=90)
    preparation_timeout_seconds: float = Field(default=50, ge=1, le=90)
    ocr_enabled: bool = True
    libreoffice_path: str = ''
    groq_source_batch_chars: int = Field(default=9000,ge=1000,le=12000)
    groq_max_batches: int = Field(default=8,ge=1,le=12)
    groq_max_output_tokens: int = Field(default=3500, ge=512, le=8000)
    groq_timeout_seconds: float = Field(default=30, gt=0, le=30)
    groq_key_cooldown_seconds: float = Field(default=5, ge=0, le=120)
    crew_timeout_seconds: float = Field(default=75, ge=1, le=120)
    matching_timeout_seconds: float = Field(default=120,ge=5,le=180)
    matching_minimum_score: float = Field(default=30,ge=0,le=100)
    matching_critical_penalty: float = Field(default=15,ge=0,le=30)
    matching_unknown_ceiling_base: float = Field(default=50,ge=0,le=100)
    matching_embedding_timeout_seconds: float = Field(default=30,ge=1,le=60)
    matching_ai_top_k: int = Field(default=8,ge=1,le=12)
    matching_weights: dict[str,float] = Field(default_factory=lambda:dict(semantic=.20,required_skills=.30,preferred_skills=.08,title_category=.12,project_experience=.10,education=.04,experience=.06,location=.05,interests=.05))
    matching_embedding_provider: Literal['none','ollama'] = 'none'
    matching_embedding_model: str = 'nomic-embed-text'
    matching_embedding_url: str = 'http://127.0.0.1:11434/api/embed'

    @property
    def text_model(self):
        return self.groq_text_model or self.groq_model

    @property
    def groq_keys(self) -> tuple[SecretStr, ...]:
        candidates = (self.groq_api_key_1 if self.groq_api_key_1.get_secret_value() else self.groq_api_key,
            self.groq_api_key_2, self.groq_api_key_3)
        unique = []
        for secret in candidates:
            if secret.get_secret_value() and secret.get_secret_value() not in [key.get_secret_value() for key in unique]:
                unique.append(secret)
        return tuple(unique)

    @property
    def origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def validate_origins(self):
        from urllib.parse import urlparse
        expected={'semantic','required_skills','preferred_skills','title_category','project_experience','education','experience','location','interests'}
        if set(self.matching_weights)!=expected or any(not 0<=weight<=1 for weight in self.matching_weights.values()) or abs(sum(self.matching_weights.values())-1)>1e-6:
            raise ValueError('Matching weights must contain the documented components and sum to one')
        embedding_url=urlparse(self.matching_embedding_url)
        if embedding_url.scheme!='http' or embedding_url.hostname not in {'127.0.0.1','localhost','::1'} or embedding_url.username or embedding_url.password or embedding_url.query:
            raise ValueError('The local embedding endpoint must use loopback HTTP without credentials or query parameters')
        for origin in self.origins:
            if urlparse(origin).hostname not in {"localhost", "127.0.0.1", "[::1]", "::1"}:
                raise ValueError("This milestone supports loopback workspace origins only.")
        if self.max_document_size_mb is not None:
            self.max_document_bytes = int(self.max_document_size_mb * 1024 * 1024)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
