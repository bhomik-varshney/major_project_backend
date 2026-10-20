from io import BytesIO
import json
from threading import Event
import time
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.connection import initialize_database, make_engine, session_factory
from app.db.models import Activity, Base, CandidateProfile, Document, DocumentExtraction, User, VerificationRecord
from app.schemas.extraction import AIProposal, CandidateDraft
from app.services.groq_extraction import EnhancementUnavailable, enhance
from app.services.resume_structure import anchored_ai_values, deterministic_extract

BASE = "/api/v1/documents"
RESUME = """Aarav Example
Email: aarav@example.com
Phone: +91 9876543210
Location: Aligarh, India
https://github.com/aarav-example
Summary:
Computer engineering student building useful backend services.
Skills:
Python, SQL, FastAPI, Python
Education:
B.Tech Computer Engineering | Example University | 2023-2027
Experience:
Backend Intern | Example Labs | Jun 2025-Aug 2025
Projects:
Career Portal | Built a student portal using Python
Certifications:
Python Fundamentals
Achievements:
University hackathon finalist
Preferred roles:
Backend Intern
"""


def upload(client, content=RESUME.encode(), name="resume.txt", mime="text/plain"):
    response = client.post(f"{BASE}/upload", files={"file": (name, content, mime)})
    assert response.status_code in {200, 201}, response.text
    return response.json()["id"]


def finish(client, doc_id, **options):
    response = client.post(f"{BASE}/{doc_id}/process", json=options)
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = client.get(f"{BASE}/{doc_id}/status").json()
        if result["status"] not in {"queued", "processing"}:
            return result
        time.sleep(0.01)
    pytest.fail("Local extraction did not finish")


def verify_payload(data, **overrides):
    return {"processing_id": data["id"], "expected_revision": data["revision"],
        "expected_profile_revision": data["current_profile_revision"],
        "fields": [key for key, value in data["values"].items() if value], "confirmed": True, **overrides}


def make_pdf(text):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter(); page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})})})
    stream = DecodedStreamObject()
    commands = ["BT /F1 11 Tf 40 760 Td"]
    for line in text.splitlines():
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"({safe}) Tj 0 -15 Td")
    commands.append("ET")
    stream.set_data("\n".join(commands).encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO(); writer.write(output)
    return output.getvalue()


def make_docx():
    from xml.sax.saxutils import escape
    output = BytesIO()
    with ZipFile(output, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        paragraphs = "".join(f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in RESUME.splitlines())
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + paragraphs + '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table evidence</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    return output.getvalue()


def test_text_extraction_and_no_automatic_verification(client):
    doc = upload(client)
    assert finish(client, doc)["status"] == "awaiting_review"
    data = client.get(f"{BASE}/{doc}/extracted-data").json()
    assert data["values"]["name"] == "Aarav Example"
    assert data["values"]["skills"] == ["Python", "SQL", "FastAPI"]
    assert data["values"]["phone"] == "+91 9876543210"
    assert data["values"]["education"][0]["institution"] == "Example University"
    assert data["evidence"]["skills.0"]["source"]["paragraph"]
    assert "Python" in data["evidence"]["skills.0"]["source"]["quote"]
    assert data["method"] == "deterministic_limited"
    assert client.get("/api/v1/profile").status_code == 404
    assert client.get("/api/v1/dashboard/summary").json()["extracted_skill_count"] == 0


@pytest.mark.parametrize("kind", ["pdf", "docx"])
def test_pdf_docx_extraction(client, application, kind):
    application.state.settings.max_document_bytes = 20000
    content = make_pdf(RESUME) if kind == "pdf" else make_docx()
    mime = "application/pdf" if kind == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    doc = upload(client, content, f"resume.{kind}", mime)
    result = finish(client, doc)
    assert result["status"] == "awaiting_review", result
    data = client.get(f"{BASE}/{doc}/extracted-data").json()
    assert data["values"]["email"] == "aarav@example.com"
    source = data["evidence"]["email"]["source"]
    assert source["page"] == 1 if kind == "pdf" else source["paragraph"] >= 1
    assert list(application.state.settings.document_storage_path.iterdir()) == []


def test_scanned_pdf_fails_honestly_and_original_retained(client, application):
    application.state.settings.max_document_bytes = 10000
    from pypdf import PdfWriter
    output = BytesIO(); writer = PdfWriter(); writer.add_blank_page(width=100, height=100); writer.write(output)
    doc = upload(client, output.getvalue(), "scan.pdf", "application/pdf")
    result = finish(client, doc)
    assert result["status"] == "failed"
    assert result["error_code"] == "no_readable_text"
    assert "OCR" in result["error_message"]
    assert client.get(f"{BASE}/{doc}").status_code == 200
    assert client.get(f"{BASE}/{doc}/extracted-data").status_code == 409
    assert finish(client, doc)["run_version"] == 2


def test_edit_verify_profile_provenance_and_summary(client):
    doc = upload(client); finish(client, doc)
    data = client.get(f"{BASE}/{doc}/extracted-data").json()
    values = {**data["values"], "name": "Aarav Corrected"}
    edited = client.put(f"{BASE}/{doc}/extracted-data", json={"processing_id": data["id"], "expected_revision": data["revision"], "values": values})
    assert edited.status_code == 200, edited.text
    data = edited.json()
    assert data["evidence"]["name"]["method"] == "user"
    assert data["evidence"]["name"]["source"] is None
    saved = client.post(f"{BASE}/{doc}/verify", json=verify_payload(data))
    assert saved.status_code == 200, saved.text
    profile = saved.json()
    assert profile["name"] == "Aarav Corrected"
    assert profile["verification_state"] == "verified"
    assert profile["sources"][0]["document_id"] == doc
    assert profile["verified_at"]
    assert client.get(f"{BASE}/{doc}/status").json()["status"] == "verified"
    assert client.get(f"{BASE}/{doc}").json()["verification_status"] == "verified"
    summary = client.get("/api/v1/dashboard/summary").json()
    assert summary["profile_skill_count"] == 3
    assert summary["extracted_skill_count"] == 3
    assert summary["recent_activity"][0]["kind"] == "extraction_verified"
    assert client.post(f"{BASE}/{doc}/verify", json=verify_payload(data)).status_code == 409


def test_confirm_preserves_unrelated_profile_and_requires_replacement(client, profile):
    client.post("/api/v1/profile", json={**profile, "name": "Already Verified", "skills": ["Git"]})
    client.post("/api/v1/profile/verify", json={"expected_revision": 1, "confirmed": True})
    doc = upload(client); finish(client, doc)
    data = client.get(f"{BASE}/{doc}/extracted-data").json()
    conflict = client.post(f"{BASE}/{doc}/verify", json=verify_payload(data, fields=["name"]))
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "overwrite_confirmation_required"
    assert client.get(f"{BASE}/{doc}/status").json()["status"] == "awaiting_review"
    saved = client.post(f"{BASE}/{doc}/verify", json=verify_payload(data, fields=["skills"]))
    assert saved.status_code == 200, saved.text
    assert saved.json()["name"] == "Already Verified"
    assert saved.json()["skills"] == ["Git", "Python", "SQL", "FastAPI"]
    assert saved.json()["verification_state"] == "verified"
    assert client.get("/api/v1/dashboard/summary").json()["extracted_skill_count"] == 3


def test_stale_profile_and_draft_revisions(client, profile):
    doc = upload(client); finish(client, doc)
    data = client.get(f"{BASE}/{doc}/extracted-data").json()
    client.post("/api/v1/profile", json=profile)
    assert client.post(f"{BASE}/{doc}/verify", json=verify_payload(data)).status_code == 409
    update = {"processing_id": data["id"], "expected_revision": data["revision"], "values": data["values"]}
    assert client.put(f"{BASE}/{doc}/extracted-data", json=update).status_code == 200
    assert client.put(f"{BASE}/{doc}/extracted-data", json=update).status_code == 409


def test_reject_and_correction_do_not_modify_profile(client):
    doc = upload(client); finish(client, doc)
    data = client.get(f"{BASE}/{doc}/extracted-data").json()
    correction = client.post(f"{BASE}/{doc}/request-correction", json={"processing_id": data["id"], "expected_revision": data["revision"], "action": "correction"})
    assert correction.json()["status"] == "needs_correction"
    rejected = client.post(f"{BASE}/{doc}/request-correction", json={"processing_id": data["id"], "expected_revision": correction.json()["revision"], "action": "reject"})
    assert rejected.json()["status"] == "rejected"
    assert client.get("/api/v1/profile").status_code == 404
    assert client.post(f"{BASE}/{doc}/verify", json=verify_payload(data, expected_revision=rejected.json()["revision"])).status_code == 409


def test_processing_failure_and_retry(client):
    doc = upload(client)
    with patch("app.services.document_processing.prepare_isolated", side_effect=RuntimeError("private document text")):
        result = finish(client, doc)
    assert result["status"] == "failed"
    assert "private document text" not in json.dumps(result)
    assert finish(client, doc)["status"] == "awaiting_review"


def test_cancellation_and_active_delete(client):
    from app.services.document_intelligence import prepare_isolated
    started, release = Event(), Event()
    def paused(*args):
        started.set(); release.wait(4)
        return prepare_isolated(*args)
    doc = upload(client)
    with patch("app.services.document_processing.prepare_isolated", side_effect=paused):
        result = client.post(f"{BASE}/{doc}/process", json={}).json()
        try:
            assert started.wait(2)
            assert client.delete(f"{BASE}/{doc}").status_code == 409
            cancelled = client.post(f"{BASE}/{doc}/request-correction", json={"processing_id": result["id"], "expected_revision": result["revision"], "action": "cancel"})
            assert cancelled.json()["status"] == "cancelled"
        finally:
            release.set()
    assert client.get(f"{BASE}/{doc}/status").json()["status"] == "cancelled"


def test_duplicate_upload_keeps_review_state(client):
    doc = upload(client); finish(client, doc)
    assert upload(client) == doc
    assert client.get(f"{BASE}/{doc}").json()["processing_status"] == "awaiting_review"
    original = client.get(f"{BASE}/{doc}/status").json()["id"]
    assert client.post(f"{BASE}/{doc}/process", json={}).json()["id"] == original


def test_owner_isolation_on_processing_routes(application, client):
    doc = upload(client); finish(client, doc)
    with TestClient(application, headers={"X-Workspace-Client": "careeros-web"}) as other:
        other.post("/api/v1/session")
        for suffix in ["status", "extracted-data"]:
            assert other.get(f"{BASE}/{doc}/{suffix}").status_code == 404
        assert other.post(f"{BASE}/{doc}/process", json={}).status_code == 404


def test_groq_requires_per_run_consent_and_missing_key_fallback(client):
    doc = upload(client)
    assert client.post(f"{BASE}/{doc}/process", json={"use_groq": True}).status_code == 422
    result = finish(client, doc, use_groq=True, allow_external_processing=True)
    assert result["method"] == "deterministic_limited"
    assert any("not configured" in warning for warning in result["warnings"])


def test_invalid_ai_json_falls_back_in_pipeline(client):
    doc = upload(client)
    with patch("app.services.document_processing.resume_intelligence.enhance", side_effect=EnhancementUnavailable("groq_invalid_output", "Invalid AI JSON; local extraction retained.")):
        result = finish(client, doc, use_groq=True, allow_external_processing=True)
    assert result["status"] == "awaiting_review"
    assert result["method"] == "deterministic_limited"


@pytest.mark.parametrize("mode,code,attempts", [("invalid", "groq_invalid_output", 1), ("timeout", "groq_timeout", 1), ("rate", "groq_rate_limit", 1), ("server", "groq_unavailable", 1)])
def test_groq_errors_are_bounded_and_redacted(mode, code, attempts):
    calls = []
    def handler(request):
        calls.append(request)
        if mode == "timeout": raise httpx.ReadTimeout("private provider detail")
        return httpx.Response(429 if mode == "rate" else 503 if mode == "server" else 200,
            json={"choices": [{"message": {"content": "not JSON secret"}}]})
    settings = Settings(groq_api_key="synthetic-test-secret")
    factory = lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw)
    with pytest.raises(EnhancementUnavailable) as exc:
        enhance([], settings, client_factory=factory)
    assert exc.value.code == code
    assert "secret" not in exc.value.message
    assert len(calls) == attempts


def test_ai_claims_must_be_source_supported():
    blocks = [{"block_id": 1, "page": 1, "paragraph": None, "text": "Aarav Example. Skills: Python"}]
    proposal = AIProposal.model_validate({"values": {"name": "Aarav Example", "skills": ["Python", "Kubernetes"], "location": "London"}, "claims": [
        {"path": "name", "block_id": 1, "quote": "Aarav Example", "confidence": "high"},
        {"path": "skills.0", "block_id": 1, "quote": "Skills: Python", "confidence": "high"},
        {"path": "skills.1", "block_id": 1, "quote": "Skills: Python", "confidence": "high"},
        {"path": "location", "block_id": 1, "quote": "London", "confidence": "low", "inferred": True},
    ]})
    values, evidence, rejected = anchored_ai_values(proposal, blocks)
    assert values["skills"] == ["Python"]
    assert values["location"] == ""
    assert rejected == 2


def test_delete_source_retains_approved_facts_but_removes_derivative_text(client, application):
    doc = upload(client); finish(client, doc)
    data = client.get(f"{BASE}/{doc}/extracted-data").json()
    assert client.post(f"{BASE}/{doc}/verify", json=verify_payload(data)).status_code == 200
    assert client.delete(f"{BASE}/{doc}").status_code == 204
    profile = client.get("/api/v1/profile").json()
    assert profile["name"] == "Aarav Example"
    assert profile["sources"][0]["source_available"] is False
    with application.state.session_factory() as db:
        assert not db.scalars(select(DocumentExtraction)).all()
        assert db.scalar(select(VerificationRecord)).source_evidence == {}


def test_additive_initialization_preserves_legacy_data(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    for model in [User, CandidateProfile, Document, Activity]: model.__table__.create(engine)
    factory = session_factory(engine)
    with factory() as db:
        user = User(); db.add(user); db.flush()
        profile = CandidateProfile(user_id=user.id, facts={"name": "Existing Student"}, verified=True)
        db.add(profile); db.commit(); profile_id = profile.id
    initialize_database(engine); initialize_database(engine)
    with factory() as db:
        assert db.get(CandidateProfile, profile_id).facts == {"name": "Existing Student"}
        assert db.get(CandidateProfile, profile_id).verified is True
    engine.dispose()


def test_verification_commit_failure_rolls_back_profile_receipt_and_status(client, application):
    doc = upload(client); finish(client, doc)
    data = client.get(f"{BASE}/{doc}/extracted-data").json()
    with patch.object(Session, "commit", side_effect=SQLAlchemyError("private database detail")):
        response = client.post(f"{BASE}/{doc}/verify", json=verify_payload(data))
    assert response.status_code == 503
    assert "private database detail" not in response.text
    assert client.get("/api/v1/profile").status_code == 404
    assert client.get(f"{BASE}/{doc}/status").json()["status"] == "awaiting_review"
    with application.state.session_factory() as db:
        assert not db.scalars(select(VerificationRecord)).all()
    assert client.post(f"{BASE}/{doc}/verify", json=verify_payload(data)).status_code == 200


def test_enhanced_values_exceeding_combined_limits_retain_local_draft(client):
    doc = upload(client)
    enhanced = CandidateDraft(skills=[f"Extra skill {i}" for i in range(100)]).model_dump()
    with patch("app.services.document_processing.resume_intelligence.enhance", return_value=(enhanced, {}, 0)):
        result = finish(client, doc, use_groq=True, allow_external_processing=True)
    assert result["status"] == "awaiting_review"
    assert result["method"] == "deterministic_limited"
    assert any("combined profile limits" in warning for warning in result["warnings"])
    assert client.get(f"{BASE}/{doc}/extracted-data").json()["values"]["skills"] == ["Python", "SQL", "FastAPI"]


def test_restart_recovers_interrupted_run_when_auto_initialization_disabled(tmp_path):
    from app.main import create_app
    settings = Settings(app_env="test", database_url=f"sqlite:///{tmp_path / 'restart.db'}",
        data_dir=tmp_path, auto_init_db=False, groq_api_key="")
    application = create_app(settings)
    initialize_database(application.state.engine)
    with application.state.session_factory() as db:
        user = User(); db.add(user); db.flush()
        document = Document(user_id=user.id, filename="resume.txt", content_type="text/plain", content=b"Student",
            size_bytes=7, sha256="test-hash")
        db.add(document); db.flush()
        run = DocumentExtraction(user_id=user.id, document_id=document.id, active_document=document.id, run_version=1)
        db.add(run); db.commit(); run_id = run.id
    with TestClient(application):
        with application.state.session_factory() as db:
            recovered = db.get(DocumentExtraction, run_id)
            assert recovered.status == "failed"
            assert recovered.error_code == "processing_interrupted"
            assert recovered.active_document is None
