from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def error_response(request: Request, status: int, code: str, message: str, fields=None):
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message,
        "operation_id": getattr(request.state, "operation_id", "unknown"),
        "fields": fields or [],
    }})
