import ipaddress
import secrets
from pathlib import Path

from fastapi import Request

from app.core.config import Settings
from app.core.errors import AppError


def session_secret(settings: Settings) -> str:
    if settings.session_secret:
        if len(settings.session_secret) < 32:
            raise ValueError("SESSION_SECRET must have at least 32 characters.")
        return settings.session_secret
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = Path(settings.data_dir) / "session-secret"
    try:
        with open(path, "x", opener=lambda p, flags: __import__("os").open(p, flags, 0o600)) as file:
            file.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    return path.read_text().strip()


def require_local_client(request: Request) -> None:
    settings = request.app.state.settings
    host = request.client.host if request.client else ""
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if settings.app_env != "test" and not is_loopback:
        raise AppError(403, "local_only", "This workspace is available on this computer only.")
    origin = request.headers.get("origin")
    if origin and origin not in settings.origins:
        raise AppError(403, "origin_denied", "This workspace origin is not permitted.")
    if request.headers.get("x-workspace-client") != "careeros-web":
        raise AppError(403, "client_required", "Open the CareerOS workspace to continue.")
