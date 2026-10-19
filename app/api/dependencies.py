from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import User


def get_db(request: Request):
    with request.app.state.session_factory() as db:
        yield db


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise AppError(401, "session_required", "Your workspace session has expired. Reconnect to continue.")
    return user
