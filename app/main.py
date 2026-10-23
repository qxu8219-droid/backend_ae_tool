"""Contract-only ASGI app; real workers, logs, sessions and playback are not wired.

Run/importing this module never starts IsaacSim, GPU work, or media processes.
Valid REST calls return 503, WS closes 1013; declared success models describe
future service behavior and must not be mistaken for implemented functionality.
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.routes import ContractError, router, ws_router
from app.schemas import ErrorDetail, ErrorEnvelope

app = FastAPI(title="Robot Log Replay Contracts", version="1.0.0", description=__doc__)
app.include_router(router)
app.include_router(ws_router)


def error_response(status: int, code: str, message: str, details=None):
    envelope = ErrorEnvelope(error=ErrorDetail(code=code, message=message, details=details or {}))
    return JSONResponse(status_code=status, content=envelope.model_dump(mode="json"))


@app.exception_handler(ContractError)
async def contract_error_handler(request: Request, exc: ContractError):
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    # Never echo raw input/context: malformed input may contain NaN or exception objects.
    issues = [{"location": [str(item) for item in error["loc"]], "type": str(error["type"]), "message": str(error["msg"])} for error in exc.errors()]
    return error_response(400, "INVALID_REQUEST", "Request validation failed.", {"issues": issues})


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    return error_response(exc.status_code, "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR", str(exc.detail))
