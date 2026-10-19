from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
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

    @property
    def origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def validate_origins(self):
        from urllib.parse import urlparse
        for origin in self.origins:
            if urlparse(origin).hostname not in {"localhost", "127.0.0.1", "[::1]", "::1"}:
                raise ValueError("This milestone supports loopback workspace origins only.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
