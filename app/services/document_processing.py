from concurrent.futures import ThreadPoolExecutor
import logging
from threading import BoundedSemaphore

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import AppError
from app.db.models import Document, DocumentExtraction, now
from app.services import groq_extraction
from app.services.resume_structure import deterministic_extract, merge_enhancement
from app.services.resume_text import extract_text

ACTIVE = ("queued", "processing")
logger = logging.getLogger("careeros.processing")


class ProcessingStopped(Exception):
    pass


class DocumentProcessor:
    """One bounded local worker. Durable states, no autonomous restart/retry."""

    def __init__(self, factory, settings):
        self.factory, self.settings = factory, settings
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="resume")
        self.capacity = BoundedSemaphore(4)

    def recover_interrupted(self):
        with self.factory() as db:
            db.execute(update(DocumentExtraction).where(DocumentExtraction.status.in_(ACTIVE)).values(
                status="failed", stage="failed", active_document=None, error_code="processing_interrupted",
                error_message="Processing was interrupted by a restart. Retry when ready.", updated_at=now(), completed_at=now()))
            db.commit()

    def reserve(self):
        if not self.capacity.acquire(blocking=False):
            raise AppError(429, "processing_busy", "The local processing queue is full. Try again after another document finishes.")

    def submit(self, extraction_id, use_groq):
        try:
            self.executor.submit(self._run, extraction_id, use_groq)
        except RuntimeError:
            self.capacity.release()
            raise AppError(503, "processing_unavailable", "The local worker is shutting down. Retry after restarting.") from None

    def stage(self, extraction_id, stage, **values):
        with self.factory() as db:
            changed = db.execute(update(DocumentExtraction).where(DocumentExtraction.id == extraction_id,
                DocumentExtraction.status.in_(ACTIVE)).values(status="processing", stage=stage, updated_at=now(), **values))
            if changed.rowcount != 1:
                raise ProcessingStopped()
            db.commit()

    def _run(self, extraction_id, use_groq):
        try:
            self.stage(extraction_id, "extracting_text")
            with self.factory() as db:
                extraction = db.get(DocumentExtraction, extraction_id)
                document = db.get(Document, extraction.document_id) if extraction else None
                if not document:
                    return
                content, filename, doc_id = document.content, document.filename, document.id
            blocks, page_count, warnings = extract_text(content, filename, doc_id, self.settings)
            self.stage(extraction_id, "identifying_fields", text_blocks=blocks, page_count=page_count)
            values, evidence, local_warnings = deterministic_extract(blocks)
            warnings += local_warnings
            method = "deterministic_limited"
            if use_groq:
                self.stage(extraction_id, "enhancing_fields")
                try:
                    enhanced, ai_evidence, rejected = groq_extraction.enhance(blocks, self.settings)
                    values, evidence = merge_enhancement(values, evidence, enhanced, ai_evidence)
                    warnings.extend(getattr(ai_evidence, "warnings", []))
                    warnings.extend(proof["warning"] for proof in ai_evidence.values() if proof.get("warning"))
                    method = "groq_enhanced"
                    if rejected:
                        warnings.append("Unsupported or inferred AI fields were withheld. Only source-linked suggestions are shown.")
                except groq_extraction.EnhancementUnavailable as exc:
                    warnings.append(exc.message)
                except ValidationError:
                    warnings.append("Groq suggestions exceeded the combined profile limits. Your limited local extraction is available.")
            self.stage(extraction_id, "preparing_review")
            with self.factory() as db:
                result = db.execute(update(DocumentExtraction).where(DocumentExtraction.id == extraction_id,
                    DocumentExtraction.status.in_(ACTIVE)).values(status="awaiting_review", stage="completed",
                    active_document=None, method=method, values=values, evidence=evidence,
                    warnings=list(dict.fromkeys(warnings)), updated_at=now(), completed_at=now()))
                db.commit()
        except ProcessingStopped:
            pass
        except Exception as exc:
            code = exc.code if isinstance(exc, AppError) else "processing_failed"
            message = exc.message if isinstance(exc, AppError) else "This document could not be processed. Retry or enter your profile manually."
            try:
                with self.factory() as db:
                    db.execute(update(DocumentExtraction).where(DocumentExtraction.id == extraction_id,
                        DocumentExtraction.status.in_(ACTIVE)).values(status="failed", stage="failed", active_document=None,
                        error_code=code, error_message=message, updated_at=now(), completed_at=now()))
                    db.commit()
            except SQLAlchemyError:
                logger.error("processing_persistence_failure operation=%s", extraction_id)
        finally:
            self.capacity.release()

    def shutdown(self):
        self.executor.shutdown(wait=True, cancel_futures=False)
