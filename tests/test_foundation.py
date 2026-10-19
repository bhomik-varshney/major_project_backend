from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.main import create_app

PREFIX = "/api/v1"


def test_health(client):
    response = client.get(f"{PREFIX}/health")
    assert response.status_code == 200
    assert response.json()["database"] == "connected"
    assert response.json()["version"] == "0.1.0"
    assert response.headers["x-operation-id"]


def test_empty_workspace(client):
    assert client.get(f"{PREFIX}/profile").json()["error"]["code"] == "profile_not_found"
    result = client.get(f"{PREFIX}/dashboard/summary").json()
    assert result["profile_completion"] == 0
    assert result["recent_activity"] == []
    assert result["recommended_job_count"] is None
    assert result["uploaded_document_count"] == 0
    assert client.get(f"{PREFIX}/documents").json() == []


def test_profile_lifecycle_revision_and_verification(client, profile):
    created = client.post(f"{PREFIX}/profile", json=profile)
    assert created.status_code == 201
    assert created.json()["verification_state"] == "unverified"
    assert created.json()["revision"] == 1
    assert client.post(f"{PREFIX}/profile", json=profile).status_code == 409
    assert client.get(f"{PREFIX}/profile").json()["name"] == profile["name"]
    verified = client.post(f"{PREFIX}/profile/verify", json={"expected_revision": 1, "confirmed": True})
    assert verified.json()["verification_state"] == "verified"
    updated = client.put(f"{PREFIX}/profile", json={**profile, "name": "New Name", "expected_revision": 2})
    assert updated.status_code == 200
    assert updated.json()["revision"] == 3
    assert updated.json()["verification_state"] == "unverified"
    assert client.put(f"{PREFIX}/profile", json={**profile, "expected_revision": 2}).status_code == 409
    assert client.post(f"{PREFIX}/profile/verify", json={"expected_revision": 2, "confirmed": True}).status_code == 409
    assert client.get(f"{PREFIX}/profile").json()["name"] == "New Name"
    summary = client.get(f"{PREFIX}/dashboard/summary").json()
    assert summary["profile_completion"] == 100
    assert summary["profile_skill_count"] == 2
    assert summary["extracted_skill_count"] == 0
    assert len(summary["recent_activity"]) == 3


def test_invalid_profile_does_not_leak_inputs(client, profile):
    response = client.post(f"{PREFIX}/profile", json={**profile, "email": "private bad email", "extra": "secret"})
    assert response.status_code == 422
    assert "private bad email" not in response.text
    assert "secret" not in response.text
    assert response.json()["error"]["fields"]
    assert client.post(f"{PREFIX}/profile", json={**profile, "name": "   "}).status_code == 422
    assert client.post(f"{PREFIX}/profile", json={**profile, "skills": [""]}).status_code == 422
    assert client.put(f"{PREFIX}/profile", json=profile).status_code == 422


def test_skills_deduplicated(client, profile):
    response = client.post(f"{PREFIX}/profile", json={**profile, "skills": ["Python", "python", " SQL "]})
    assert response.json()["skills"] == ["Python", "SQL"]


def test_isolation_and_session_requirement(application, client, profile):
    client.post(f"{PREFIX}/profile", json=profile)
    document = client.post(f"{PREFIX}/documents/upload", files={"file": ("resume.txt", b"A student resume", "text/plain")}).json()
    with TestClient(application, headers={"X-Workspace-Client": "careeros-web"}) as other:
        assert other.get(f"{PREFIX}/profile").status_code == 401
        assert other.post(f"{PREFIX}/session").status_code == 200
        assert other.get(f"{PREFIX}/profile").status_code == 404
        assert other.get(f"{PREFIX}/documents").json() == []
        assert other.get(f"{PREFIX}/documents/{document['id']}").status_code == 404
        assert other.delete(f"{PREFIX}/documents/{document['id']}").status_code == 404
        assert other.get(f"{PREFIX}/dashboard/summary").json()["recent_activity"] == []
    assert client.get(f"{PREFIX}/documents/{document['id']}").status_code == 200


def test_document_storage_duplicate_and_deletion(client):
    payload = {"file": ("resume.txt", b"Student qualifications", "text/plain")}
    uploaded = client.post(f"{PREFIX}/documents/upload", files=payload)
    assert uploaded.status_code == 201
    data = uploaded.json()
    assert data["upload_status"] == "stored"
    assert data["extraction_status"] == "not_started"
    assert "content" not in data
    duplicate = client.post(f"{PREFIX}/documents/upload", files=payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == data["id"]
    assert len(client.get(f"{PREFIX}/documents").json()) == 1
    assert client.get(f"{PREFIX}/dashboard/summary").json()["uploaded_document_count"] == 1
    assert client.delete(f"{PREFIX}/documents/{data['id']}").status_code == 204
    assert client.get(f"{PREFIX}/documents/{data['id']}").status_code == 404
    assert client.get(f"{PREFIX}/dashboard/summary").json()["uploaded_document_count"] == 0


def test_document_validation(client):
    cases = [
        ("script.exe", b"executable", "application/octet-stream", 415),
        ("resume.txt", b"a" * 1025, "text/plain", 413),
        ("resume.txt", b"", "text/plain", 422),
        ("resume.txt", b"\x00not text", "text/plain", 422),
        ("resume.pdf", b"%PDF-broken", "application/pdf", 422),
        ("resume.docx", b"not a zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 422),
        ("resume.txt", b"text", "application/pdf", 415),
    ]
    for name, content, mime, code in cases:
        response = client.post(f"{PREFIX}/documents/upload", files={"file": (name, content, mime)})
        assert response.status_code == code, response.text
    assert client.get(f"{PREFIX}/documents").json() == []


def test_valid_pdf_and_docx(client, application):
    from pypdf import PdfWriter
    application.state.settings.max_document_bytes = 10000
    pdf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    assert client.post(f"{PREFIX}/documents/upload", files={"file": ("resume.pdf", pdf.getvalue(), "application/pdf")}).status_code == 201
    docx = BytesIO()
    with ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document><p>Student</p></document>")
    assert client.post(f"{PREFIX}/documents/upload", files={"file": ("resume.docx", docx.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}).status_code == 201


def test_document_limit(client):
    for i in range(3):
        assert client.post(f"{PREFIX}/documents/upload", files={"file": (f"{i}.txt", str(i).encode(), "text/plain")}).status_code == 201
    assert client.post(f"{PREFIX}/documents/upload", files={"file": ("extra.txt", b"extra", "text/plain")}).status_code == 409


def test_cors_and_origin_checks(client):
    response = client.options(f"{PREFIX}/profile", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "content-type,x-workspace-client"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert client.post(f"{PREFIX}/session", headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert client.get(f"{PREFIX}/profile", headers={"X-Workspace-Client": ""}).status_code == 403


def test_database_failure_response(application, client, profile):
    with patch.object(application.state.engine, "connect", side_effect=OperationalError("hidden db url", {}, Exception("secret"))):
        response = client.get(f"{PREFIX}/health")
        assert response.status_code == 503
        assert response.json()["database"] == "unavailable"
    with patch("sqlalchemy.orm.Session.commit", side_effect=OperationalError("private input", {}, Exception("secret"))):
        response = client.post(f"{PREFIX}/profile", json=profile)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_unavailable"
        assert "secret" not in response.text
    assert client.get(f"{PREFIX}/profile").status_code == 404


def test_persistence_across_restart(tmp_path, profile):
    settings = Settings(app_env="test", data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'durable.db'}")
    with TestClient(create_app(settings), headers={"X-Workspace-Client": "careeros-web"}) as first:
        first.post(f"{PREFIX}/session")
        first.post(f"{PREFIX}/profile", json=profile)
        cookies = dict(first.cookies)
    with TestClient(create_app(settings), headers={"X-Workspace-Client": "careeros-web"}, cookies=cookies) as second:
        assert second.get(f"{PREFIX}/profile").json()["name"] == profile["name"]


def test_local_mode_denies_remote_client(tmp_path):
    application = create_app(Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'local.db'}"))
    with TestClient(application, headers={"X-Workspace-Client": "careeros-web"}) as remote:
        assert remote.post(f"{PREFIX}/session").status_code == 403
