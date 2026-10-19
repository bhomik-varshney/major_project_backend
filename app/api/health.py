from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.workspace import HealthResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request, response: Response):
    try:
        with request.app.state.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return HealthResponse(status="ok", version=request.app.state.settings.version, database="connected")
    except SQLAlchemyError:
        response.status_code = 503
        return HealthResponse(status="degraded", version=request.app.state.settings.version, database="unavailable")
