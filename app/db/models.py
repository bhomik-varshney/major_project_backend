from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    verified: Mapped[bool] = mapped_column(default=False)
    facts: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("user_id", "sha256", name="uq_document_owner_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(180))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    upload_status: Mapped[str] = mapped_column(String(30), default="stored")
    extraction_status: Mapped[str] = mapped_column(String(30), default="not_started")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentExtraction(Base):
    __tablename__ = "document_extractions"
    __table_args__ = (UniqueConstraint("document_id", "run_version", name="uq_extraction_run"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("candidate_profiles.id"), nullable=True)
    # A nullable unique lease prevents concurrent processing of one document.
    active_document: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    run_version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="queued")
    stage: Mapped[str] = mapped_column(String(40), default="queued")
    method: Mapped[str] = mapped_column(String(40), default="deterministic_limited")
    allow_external_processing: Mapped[bool] = mapped_column(default=False)
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    text_blocks: Mapped[list] = mapped_column(JSON, default=list, deferred=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agent_run: Mapped['ResumeAgentRun | None'] = relationship(uselist=False, cascade='all, delete-orphan', passive_deletes=True)


class VerificationRecord(Base):
    __tablename__ = "verification_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("candidate_profiles.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    extraction_id: Mapped[str | None] = mapped_column(ForeignKey("document_extractions.id", ondelete="SET NULL"), unique=True, nullable=True)
    source_document_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(180), nullable=True)
    profile_revision: Mapped[int] = mapped_column(Integer)
    confirmed_values: Mapped[dict] = mapped_column(JSON)
    selected_values: Mapped[dict] = mapped_column(JSON, default=dict)
    applied_fields: Mapped[list] = mapped_column(JSON, default=list)
    source_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ResumeAgentRun(Base):
    __tablename__ = 'resume_agent_runs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    extraction_id: Mapped[str] = mapped_column(ForeignKey('document_extractions.id', ondelete='CASCADE'), unique=True, index=True)
    agent_name: Mapped[str] = mapped_column(String(80), default='Resume Intelligence Agent')
    agent_version: Mapped[str] = mapped_column(String(20), default='1.0')
    engine: Mapped[str] = mapped_column(String(30), default='pending')
    status: Mapped[str] = mapped_column(String(30), default='queued')
    task: Mapped[str] = mapped_column(String(40), default='document_preparation')
    tasks: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResumeProcessingAudit(Base):
    __tablename__ = 'resume_processing_audits'
    extraction_id: Mapped[str] = mapped_column(ForeignKey('document_extractions.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict, deferred=True)


class ResumePageArtifact(Base):
    __tablename__ = 'resume_page_artifacts'
    __table_args__ = (UniqueConstraint('extraction_id', 'page', name='uq_resume_page'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    extraction_id: Mapped[str] = mapped_column(ForeignKey('document_extractions.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    page: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)


class MatchingRun(Base):
    __tablename__ = 'matching_runs'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'),index=True)
    profile_revision: Mapped[int] = mapped_column(Integer)
    candidate_hash: Mapped[str] = mapped_column(String(64))
    active_user: Mapped[str | None] = mapped_column(String(36),unique=True,nullable=True)
    status: Mapped[str] = mapped_column(String(40),default='queued')
    stage: Mapped[str] = mapped_column(String(40),default='queued')
    candidate_snapshot: Mapped[dict] = mapped_column(JSON,default=dict,deferred=True)
    request: Mapped[dict] = mapped_column(JSON)
    config: Mapped[dict] = mapped_column(JSON)
    dataset_version: Mapped[str] = mapped_column(String(80))
    dataset_reference_date: Mapped[str] = mapped_column(String(10))
    total_jobs: Mapped[int] = mapped_column(Integer,default=0)
    eligible_jobs: Mapped[int] = mapped_column(Integer,default=0)
    result_count: Mapped[int] = mapped_column(Integer,default=0)
    provider_metadata: Mapped[dict] = mapped_column(JSON,default=dict)
    tasks: Mapped[list] = mapped_column(JSON,default=list)
    warnings: Mapped[list] = mapped_column(JSON,default=list)
    validation: Mapped[dict] = mapped_column(JSON,default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80),nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer,nullable=True)


class MatchingResult(Base):
    __tablename__ = 'matching_results'
    __table_args__ = (UniqueConstraint('run_id','job_id',name='uq_match_run_opportunity'),)
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey('matching_runs.id',ondelete='CASCADE'),index=True)
    job_id: Mapped[str] = mapped_column(String(100))
    rank: Mapped[int] = mapped_column(Integer)
    hybrid_score: Mapped[float] = mapped_column()
    baseline_score: Mapped[float] = mapped_column()
    normalized_job: Mapped[dict] = mapped_column(JSON,deferred=True)
    data: Mapped[dict] = mapped_column(JSON,deferred=True)


class EmbeddingCache(Base):
    __tablename__ = 'embedding_cache'
    key: Mapped[str] = mapped_column(String(64),primary_key=True)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(160))
    vector: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)


class SavedOpportunity(Base):
    __tablename__ = 'saved_opportunities'
    __table_args__ = (UniqueConstraint('user_id','job_id',name='uq_saved_opportunity'),)
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'),index=True)
    job_id: Mapped[str] = mapped_column(String(100))
    job_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)
