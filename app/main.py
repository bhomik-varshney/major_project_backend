from contextlib import asynccontextmanager
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.api import dashboard, documents, extraction, health, profiles, jobs, matching, skill_gaps, applications, cover_letters
from app.core.config import Settings, get_settings
from app.core.errors import AppError, error_response
from app.core.logging import configure_logging
from app.core.security import require_local_client, session_secret
from app.db.connection import initialize_database, make_engine, session_factory
from app.services.document_processing import DocumentProcessor
from app.providers.jobs import SeededJobProvider
from app.services.matching import MatchingProcessor
from app.services.skill_gaps import SkillGapProcessor
from app.services.applications import ApplicationProcessor
from app.services.cover_letters import CoverLetterProcessor

logger = logging.getLogger("careeros")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        try:
            if settings.auto_init_db:
                initialize_database(engine)
            application.state.processor.recover_interrupted()
            application.state.matching_processor.recover_interrupted()
            application.state.skill_gap_processor.recover_interrupted()
            application.state.application_processor.recover_interrupted()
            application.state.cover_letter_processor.recover_interrupted()
        except SQLAlchemyError:
            logger.error("database_initialization_failed")
        yield
        application.state.matching_processor.shutdown()
        application.state.skill_gap_processor.shutdown()
        application.state.application_processor.shutdown()
        application.state.cover_letter_processor.shutdown()
        application.state.processor.shutdown()
        engine.dispose()

    application = FastAPI(title="CareerOS API", version=settings.version, lifespan=lifespan)
    application.state.job_provider = SeededJobProvider()
    application.state.settings = settings
    application.state.engine = engine
    application.state.session_factory = session_factory(engine)
    application.state.processor = DocumentProcessor(application.state.session_factory, settings)
    application.state.matching_processor = MatchingProcessor(application.state.session_factory, settings, application.state.processor.key_manager)
    application.state.skill_gap_processor = SkillGapProcessor(application.state.session_factory, settings, application.state.processor.key_manager)
    application.state.application_processor = ApplicationProcessor(application.state.session_factory, settings, application.state.processor.key_manager)
    application.state.cover_letter_processor = CoverLetterProcessor(application.state.session_factory, settings, application.state.processor.key_manager)
    application.add_middleware(SessionMiddleware, secret_key=session_secret(settings),
        session_cookie="careeros_session", max_age=30 * 24 * 3600, same_site="lax", https_only=False)

    @application.middleware("http")
    async def operation_context(request, call_next):
        request.state.operation_id = str(uuid4())
        started = perf_counter()
        content_length = request.headers.get("content-length", "0")
        try:
            too_large = int(content_length) > settings.max_document_bytes + 1024 * 1024
        except ValueError:
            return error_response(request, 400, "request_invalid", "Invalid request length.")
        if too_large:
            return error_response(request, 413, "document_size", "This upload exceeds the request limit.")
        response = await call_next(request)
        response.headers["X-Operation-ID"] = request.state.operation_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        logger.info("operation=%s method=%s status=%s elapsed_ms=%.1f",
            request.state.operation_id, request.method, response.status_code, (perf_counter() - started) * 1000)
        return response

    application.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", "X-Workspace-Client"],
        expose_headers=["X-Operation-ID"])

    @application.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        return error_response(request, exc.status, exc.code, exc.message)

    @application.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError):
        # Omit submitted values/context from validation errors.
        fields = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return error_response(request, 422, "validation_error", "Check the highlighted fields and try again.", fields)

    @application.exception_handler(SQLAlchemyError)
    async def handle_database(request: Request, exc: SQLAlchemyError):
        logger.error("operation=%s database_failure type=%s", request.state.operation_id, type(exc).__name__)
        return error_response(request, 503, "database_unavailable", "Your changes could not be saved. The database is unavailable; try again shortly.")

    @application.exception_handler(HTTPException)
    async def handle_http(request: Request, exc: HTTPException):
        return error_response(request, exc.status_code, "request_error", "This request could not be completed.")

    @application.exception_handler(Exception)
    async def handle_unknown(request: Request, exc: Exception):
        logger.error("operation=%s internal_failure type=%s", getattr(request.state, "operation_id", "unknown"), type(exc).__name__)
        return error_response(request, 500, "internal_error", "Something went wrong. Try again or keep the operation ID for support.")

    application.include_router(health.router, prefix="/api/v1")
    private = APIRouter(prefix="/api/v1", dependencies=[Depends(require_local_client)])
    for router in (profiles.router, documents.router, extraction.router, dashboard.router, jobs.router, matching.router, skill_gaps.router, applications.router, cover_letters.router):
        private.include_router(router)
    application.include_router(private)
    return application


app = create_app()
