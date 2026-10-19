from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def application(tmp_path: Path):
    return create_app(Settings(app_env="test", data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}", session_secret="test-only-" * 8,
        max_document_bytes=1024, max_documents_per_user=3,
        document_storage_path=tmp_path / "documents", groq_api_key=""))


@pytest.fixture
def client(application):
    with TestClient(application, headers={"X-Workspace-Client": "careeros-web"}) as connection:
        assert connection.post("/api/v1/session").status_code == 200
        yield connection


@pytest.fixture
def profile():
    return {"name": "Student Example", "email": "student@example.com", "location": "Aligarh",
        "skills": ["Python", "SQL"], "experience_level": "student",
        "education": [{"institution": "Example University", "qualification": "B.Tech"}],
        "projects": [{"name": "Career project", "description": "Built a profile service"}],
        "career_objective": "Find a backend internship", "preferred_roles": ["Backend Intern"],
        "preferred_locations": ["Remote"]}
