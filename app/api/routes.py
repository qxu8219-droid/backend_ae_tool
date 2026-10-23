"""All planned REST/WS routes, with validation only and no simulated success.

Every valid REST operation returns 503 BACKEND_NOT_CONFIGURED until T14 binds
real services. Upload validates metadata without reading file bytes; log-content
422, size-limit 413, catalog 404 and playback conflicts 409 are future service
outcomes declared here. No worker, subprocess, IsaacSim, GPU or media starts.
"""
from __future__ import annotations

import json
from typing import Annotated, NoReturn

from fastapi import APIRouter, Body, File, Form, Query, Request, Response, UploadFile, WebSocket
from pydantic import ValidationError

from app.schemas import (
    ControlRequest, CreateSessionRequest, CurveQuery, CurveResponse, ErrorEnvelope,
    JointMetadata, LoadRequest, LogChannels, PlaybackSnapshot, PlayRequest,
    RESTPlaybackResponse, RobotList, SceneList, SeekRequest, SessionCreated,
    SessionStatus, SpeedRequest, StepRequest, UploadOptions, UploadResult, VideoInfo,
)


class ContractError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details=None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def unavailable() -> NoReturn:
    raise ContractError(503, "BACKEND_NOT_CONFIGURED", "This contract-only service has no configured backend runtime.")


def invalid(message: str, details=None) -> NoReturn:
    raise ContractError(400, "INVALID_REQUEST", message, details)


ERROR_RESPONSES = {
    400: {"model": ErrorEnvelope, "description": "Invalid request syntax, parameters, fields, or non-finite numbers."},
    404: {"model": ErrorEnvelope, "description": "Requested scene, robot, session, log, or channel does not exist."},
    409: {"model": ErrorEnvelope, "description": "State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED."},
    503: {"model": ErrorEnvelope, "description": "BACKEND_NOT_CONFIGURED in this contract release; worker unavailable or not ready at runtime."},
    "default": {"model": ErrorEnvelope, "description": "Other transport errors use the same error envelope."},
}
router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)
ws_router = APIRouter()


@router.get("/scenes", response_model=SceneList, status_code=200)
async def list_scenes():
    unavailable()


@router.get("/robots", response_model=RobotList, status_code=200)
async def list_robots():
    unavailable()


@router.get("/robots/{robot_id}/joints", response_model=JointMetadata, status_code=200)
async def get_joints(robot_id: str):
    unavailable()


@router.post("/sessions", response_model=SessionCreated, status_code=201)
async def create_session(body: CreateSessionRequest):
    """One session maximum. The runtime will asynchronously start IsaacSim 6.2.0/PhysX."""
    unavailable()


@router.get("/sessions/{sid}", response_model=SessionStatus, status_code=200)
async def get_session(sid: str):
    unavailable()


@router.delete("/sessions/{sid}", status_code=204, response_class=Response)
async def delete_session(sid: str):
    """Runtime destruction will be idempotent; no deletion is implemented yet."""
    unavailable()


def _reject_json_constant(value):
    raise ValueError("mapping_config must contain finite JSON numbers")


@router.post("/sessions/{sid}/logs", response_model=UploadResult, status_code=201, responses={
    413: {"model": ErrorEnvelope, "description": "Upload exceeds the configured runtime size limit."},
    422: {"model": ErrorEnvelope, "description": "Fatal log-content parse failure; error.details.issues contains ParseIssue records. Never request validation."},
})
async def upload_log(
    sid: str,
    request: Request,
    file: Annotated[UploadFile, File(description="Original log file, retained unchanged by the future parser.")],
    parser: Annotated[str | None, Form(min_length=1, max_length=128)] = None,
    mapping_config: Annotated[str | None, Form(max_length=65536, description="Inline JSON object, not a file path. Stored with the original file as an immutable mapping snapshot.")] = None,
):
    form = await request.form()
    if set(form) - {"file", "parser", "mapping_config"}:
        invalid("Unexpected multipart fields.")
    if any(len(form.getlist(name)) > 1 for name in ("file", "parser", "mapping_config")):
        invalid("Multipart fields must not be repeated.")
    # FastAPI treats empty optional form strings as omitted; the contract does not.
    if any(name in form and form[name] == "" for name in ("parser", "mapping_config")):
        invalid("Explicit parser and mapping_config fields must not be empty.")
    try:
        mapping = json.loads(mapping_config, parse_constant=_reject_json_constant) if mapping_config is not None else None
        if mapping_config is not None and not isinstance(mapping, dict):
            invalid("mapping_config must be an inline JSON object.")
        UploadOptions(parser=parser, mapping_config=mapping)
    except (ValueError, ValidationError):
        invalid("Invalid parser or mapping_config JSON object.")
    # Do not call file.read(): parsing and upload storage belong to the future runtime.
    unavailable()


@router.get("/sessions/{sid}/logs/{lid}/channels", response_model=LogChannels, status_code=200)
async def get_channels(sid: str, lid: str):
    unavailable()


@router.get("/sessions/{sid}/logs/{lid}/curve", response_model=CurveResponse, status_code=200)
async def get_curve(
    sid: str,
    lid: str,
    request: Request,
    channels: Annotated[str, Query(min_length=1, max_length=32768, description="Comma-separated channel names; trim whitespace and deduplicate in first-occurrence order, 1..128 unique names. Empty entries are invalid.")],
    from_s: Annotated[float, Query(ge=0, allow_inf_nan=False)] = 0.0,
    to_s: Annotated[float | None, Query(ge=0, allow_inf_nan=False)] = None,
    max_points: Annotated[int, Query(ge=2, le=10000, description="Per-channel point limit; min/max downsampling retains peaks.")] = 2000,
):
    allowed = {"channels", "from_s", "to_s", "max_points"}
    if set(request.query_params) - allowed:
        invalid("Unexpected curve query parameters.")
    if any(len(request.query_params.getlist(name)) > 1 for name in allowed):
        invalid("Curve parameters must not be repeated; channels uses CSV.")
    try:
        CurveQuery(channels=channels, from_s=from_s, to_s=to_s, max_points=max_points)
    except ValidationError:
        invalid("Invalid channels, time range, or per-channel point limit.")
    unavailable()


@router.post("/sessions/{sid}/playback/load", response_model=RESTPlaybackResponse, status_code=200)
async def load_log(sid: str, body: LoadRequest):
    unavailable()


@router.post("/sessions/{sid}/playback/play", response_model=RESTPlaybackResponse, status_code=200)
async def play(sid: str, body: Annotated[PlayRequest | None, Body()] = None):
    unavailable()


@router.post("/sessions/{sid}/playback/pause", response_model=RESTPlaybackResponse, status_code=200)
async def pause(sid: str, body: Annotated[ControlRequest | None, Body()] = None):
    unavailable()


@router.post("/sessions/{sid}/playback/stop", response_model=RESTPlaybackResponse, status_code=200)
async def stop(sid: str, body: Annotated[ControlRequest | None, Body()] = None):
    unavailable()


@router.post("/sessions/{sid}/playback/seek", response_model=RESTPlaybackResponse, status_code=200)
async def seek(sid: str, body: SeekRequest):
    """Exactly one position. Runtime will clamp negative and beyond-end positions."""
    unavailable()


@router.post("/sessions/{sid}/playback/step", response_model=RESTPlaybackResponse, status_code=200)
async def step(sid: str, body: StepRequest):
    unavailable()


@router.post("/sessions/{sid}/playback/speed", response_model=RESTPlaybackResponse, status_code=200)
async def set_speed(sid: str, body: SpeedRequest):
    unavailable()


@router.get("/sessions/{sid}/playback", response_model=PlaybackSnapshot, status_code=200)
async def get_playback(sid: str):
    """Server-observed target/applied/rendered state; no claim about browser display."""
    unavailable()


@router.get("/sessions/{sid}/video", response_model=VideoInfo, status_code=200)
async def get_video(sid: str):
    unavailable()


@ws_router.websocket("/ws/sessions/{sid}")
async def session_websocket(websocket: WebSocket, sid: str):
    await websocket.close(code=1013, reason="BACKEND_NOT_CONFIGURED")
