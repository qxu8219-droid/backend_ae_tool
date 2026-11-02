"""Robot replay ASGI factory with explicit runtime ownership.

Importing the default app starts no worker or media process. Without a replay
service, business calls return 503 and WS closes 1013. The configured factory
binds real services while keeping accepted, rendered and presented distinct.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import ContractError, router, ws_router
from app.schemas import ErrorDetail, ErrorEnvelope
from app.api.upload_limit import UploadLimitMiddleware, PayloadTooLarge
from app.adapters.base import AdapterError
from app.analysis.models import AnalysisError
from app.core.session_manager import SessionError
from app.replay.timeline_base import TimelineError
from app.replay.base import SamplingError

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
    if isinstance(exc.__cause__, PayloadTooLarge):
        return error_response(413, 'REQUEST_TOO_LARGE', str(exc.__cause__))
    return error_response(exc.status_code, "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR", str(exc.detail))


async def domain_error_handler(request, exc):
    details = dict(getattr(exc, 'details', {}))
    if isinstance(exc, AdapterError):
        details['issues'] = [issue.model_dump(mode='json') for issue in exc.issues]
    return error_response(getattr(exc, 'status_code', 422), exc.code, exc.message, details)


async def body_limit_handler(request, exc):
    return error_response(413, 'REQUEST_TOO_LARGE', str(exc))


async def unexpected_error_handler(request, exc):
    import logging
    logging.getLogger(__name__).exception('Backend request failed', exc_info=exc)
    return error_response(500, 'INTERNAL_ERROR', 'Unexpected backend error')


def create_app(*, session_manager=None, replay_service=None):
    if session_manager is not None and replay_service is not None:
        raise ValueError('Configure the replay service or a standalone manager, not both')
    @asynccontextmanager
    async def lifespan(application):
        owner = replay_service or session_manager
        if owner is None:
            yield
        else:
            async with owner:
                yield

    application = FastAPI(title="Robot Log Replay Contracts", version="1.0.0", description=__doc__, lifespan=lifespan)
    application.state.replay_service = replay_service
    application.state.session_manager = replay_service.manager if replay_service is not None else session_manager
    application.include_router(router)
    application.include_router(ws_router)
    application.add_exception_handler(ContractError, contract_error_handler)
    application.add_exception_handler(RequestValidationError, request_validation_handler)
    application.add_exception_handler(HTTPException, http_error_handler)
    for error in (AdapterError, AnalysisError, SessionError, TimelineError, SamplingError):
        application.add_exception_handler(error, domain_error_handler)
    application.add_exception_handler(PayloadTooLarge, body_limit_handler)
    application.add_exception_handler(Exception, unexpected_error_handler)
    if replay_service is not None:
        application.add_middleware(UploadLimitMiddleware, file_bytes=replay_service.logs.limits.max_file_bytes)
        application.add_middleware(CORSMiddleware, allow_origins=list(replay_service.allowed_origins),
                                   allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type'])
    return application


app = create_app()
