"""Contract-only ASGI app; real workers, logs, sessions and playback are not wired.

Run/importing this module never starts IsaacSim, GPU work, or media processes.
Valid REST calls return 503, WS closes 1013; declared success models describe
future service behavior and must not be mistaken for implemented functionality.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.routes import ContractError, router, ws_router
from app.schemas import ErrorDetail, ErrorEnvelope

def error_response(status: int, code: str, message: str, details=None):
    envelope = ErrorEnvelope(error=ErrorDetail(code=code, message=message, details=details or {}))
    return JSONResponse(status_code=status, content=envelope.model_dump(mode="json"))


async def contract_error_handler(request: Request, exc: ContractError):
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def request_validation_handler(request: Request, exc: RequestValidationError):
    # Never echo raw input/context: malformed input may contain NaN or exception objects.
    issues = [{"location": [str(item) for item in error["loc"]], "type": str(error["type"]), "message": str(error["msg"])} for error in exc.errors()]
    return error_response(400, "INVALID_REQUEST", "Request validation failed.", {"issues": issues})


async def http_error_handler(request: Request, exc: HTTPException):
    return error_response(exc.status_code, "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR", str(exc.detail))


def create_app(*, session_manager=None):
    @asynccontextmanager
    async def lifespan(application):
        application.state.session_manager = session_manager
        if session_manager is None:
            yield
        else:
            async with session_manager:
                yield

    application = FastAPI(title="Robot Log Replay Contracts", version="1.0.0", description=__doc__, lifespan=lifespan)
    application.include_router(router)
    application.include_router(ws_router)
    application.add_exception_handler(ContractError, contract_error_handler)
    application.add_exception_handler(RequestValidationError, request_validation_handler)
    application.add_exception_handler(HTTPException, http_error_handler)
    return application


app = create_app()
