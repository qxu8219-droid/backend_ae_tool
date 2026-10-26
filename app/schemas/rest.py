"""REST data contracts shared by configured runtime and unconfigured contract app.

All arrays use replay_protocol.CANONICAL_JOINTS. Limits are supplied from the
versioned robot common config, preserving each entry's provenance. HTTP 400 is
request validation, while 422 is reserved for a fatal log-content parse.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, ConfigDict, Field, model_validator
from replay_protocol import (
    CANONICAL_JOINTS, ChannelQuality, ContractModel, FiniteNumber, Identifier,
    JsonValue, NonnegativeNumber, PlaybackSnapshot, SafeInt, SchemaVersion, Speed,
)

SignedSafeInt = Annotated[int, Field(strict=True, ge=-(2**53 - 1), le=2**53 - 1)]
JointIndex = Annotated[SafeInt, Field(le=27)]


class ErrorDetail(ContractModel):
    code: Identifier
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ErrorEnvelope(ContractModel):
    error: ErrorDetail


class Scene(ContractModel):
    id: Identifier
    name: Identifier
    source: Identifier
    description: str
    thumbnail_url: str | None = None


class SceneList(ContractModel):
    scenes: list[Scene]


class USDVariant(ContractModel):
    isaacsim_version: Literal["6.2.0"]
    usd_path: Identifier
    physics_engine: Literal["physx"] = "physx"


class Robot(ContractModel):
    id: Identifier
    name: Identifier
    dof: Literal[28] = 28
    usd_variants: list[USDVariant] = Field(min_length=1)


class RobotList(ContractModel):
    robots: list[Robot]


class JointDescriptor(ContractModel):
    name: Identifier
    type: Literal["revolute", "continuous", "prismatic"]
    unit: Literal["rad", "m"]
    lower: FiniteNumber | None
    upper: FiniteNumber | None
    limits_provenance: Identifier

    @model_validator(mode="after")
    def validate_unit_and_limits(self):
        if self.unit != ("m" if self.type == "prismatic" else "rad"):
            raise ValueError("joint unit must follow joint type")
        if self.type == "continuous":
            if self.lower is not None or self.upper is not None:
                raise ValueError("continuous joints have no hard position limits")
        elif self.lower is None or self.upper is None or self.lower > self.upper:
            raise ValueError("bounded joints require ordered finite lower and upper limits")
        return self


class JointMetadata(ContractModel):
    robot_id: Identifier
    canonical_order: list[Identifier] = Field(min_length=28, max_length=28)
    joints: list[JointDescriptor] = Field(min_length=28, max_length=28)
    limits_source: Identifier = Field(description="Authoritative versioned r1pro_2026_common.json path; limits retain per-joint provenance.")

    @model_validator(mode="after")
    def validate_canonical_order(self):
        if tuple(self.canonical_order) != CANONICAL_JOINTS:
            raise ValueError("canonical_order must equal the shared 28-joint order")
        if [joint.name for joint in self.joints] != self.canonical_order:
            raise ValueError("joint metadata must be in canonical order")
        return self


WorkerPhase = Literal["booting", "loading_scene", "loading_robot", "rtsp_publishing", "ready", "stopping", "stopped", "error"]


class WorkerStatus(ContractModel):
    phase: WorkerPhase
    pid: SafeInt | None = None
    message: str | None = None
    error: ErrorDetail | None = None
    restart_policy: Literal["recreate_session_reload_from_start"] = "recreate_session_reload_from_start"


class IceServer(ContractModel):
    urls: list[Identifier] = Field(min_length=1)
    username: str | None = None
    credential: str | None = None


class VideoInfo(ContractModel):
    transport: Literal["whep"] = "whep"
    publishing: bool
    whep_url: str | None
    stream_path: str | None
    ice_servers: list[IceServer] = Field(default_factory=list)
    identity_scheme: Literal["capture32_crc32"] = "capture32_crc32"
    snapshot_transport: Literal["h264_sei_snapshot_catalog"] = "h264_sei_snapshot_catalog"
    presentation: Literal["verified_pair_single_canvas"] = "verified_pair_single_canvas"
    sei_uuid_hex: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")] | None = None
    catalog_schema_version: SchemaVersion = 1
    decoder: Literal["webcodecs_h264"] = "webcodecs_h264"
    receiver_transform: Literal["rtcrtp_script_transform_or_encoded_streams"] = "rtcrtp_script_transform_or_encoded_streams"
    render_fps: NonnegativeNumber | None = None

    @model_validator(mode="after")
    def publishing_requires_endpoint(self):
        if self.publishing and (not self.whep_url or not self.stream_path or not self.sei_uuid_hex):
            raise ValueError("a publishing video requires whep_url, stream_path and SEI UUID")
        return self


class CreateSessionRequest(ContractModel):
    scene_id: Identifier
    robot_id: Identifier
    isaacsim_version: Literal["6.2.0"] = "6.2.0"
    mode: Literal["kinematic"] = "kinematic"
    physics_engine: Literal["physx"] = "physx"


class SessionCreated(ContractModel):
    session_id: Identifier
    status: Literal["starting"] = "starting"
    ws_url: Identifier


class SessionStatus(ContractModel):
    session_id: Identifier
    scene_id: Identifier
    robot_id: Identifier
    isaacsim_version: Literal["6.2.0"]
    mode: Literal["kinematic"] = "kinematic"
    physics_engine: Literal["physx"] = "physx"
    status: Literal["starting", "ready", "stopping", "stopped", "error"]
    ws_url: Identifier
    worker: WorkerStatus
    video: VideoInfo
    render_fps: NonnegativeNumber | None


class ParseIssue(ContractModel):
    severity: Literal["error", "warning", "info"]
    code: Identifier
    line: Annotated[SafeInt, Field(ge=1)] | None = None
    channel: Identifier | None = None
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ChassisAvailability(ContractModel):
    available: bool
    frame: Literal["world"] | None = None
    source_channels: list[Identifier] = Field(default_factory=list)


class UploadStats(ContractModel):
    sample_count: Annotated[SafeInt, Field(ge=1)]
    duration_s: NonnegativeNumber
    nominal_rate_hz: Annotated[FiniteNumber, Field(gt=0)] | None
    joints_mapped: list[Identifier]
    joints_missing: list[Identifier]
    chassis: ChassisAvailability

    @model_validator(mode="after")
    def joint_coverage_is_a_partition(self):
        combined = self.joints_mapped + self.joints_missing
        if len(combined) != 28 or set(combined) != set(CANONICAL_JOINTS):
            raise ValueError("mapped and missing joints must partition the canonical 28 joints")
        return self


class ChannelDescriptor(ContractModel):
    name: Identifier
    label: str
    kind: Literal["joint", "chassis", "custom", "virtual"]
    unit: Identifier
    source_field: str | None
    source_unit: str | None = None
    joint_index: JointIndex | None = None
    angle_semantics: Literal["bounded", "angular_wrapped", "angular_unwrapped", "linear"] = "linear"
    wrap_period: Annotated[FiniteNumber, Field(gt=0)] | None = None
    quality: ChannelQuality
    lower: FiniteNumber | None = None
    upper: FiniteNumber | None = None

    @model_validator(mode="after")
    def coherent_angle_semantics(self):
        if (self.angle_semantics == "angular_wrapped") != (self.wrap_period is not None):
            raise ValueError("only wrapped angular channels must declare a positive wrap period")
        return self


class UploadOptions(ContractModel):
    """Inline mapping object only; no user-supplied path or executable adapter code."""
    parser: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")] | None = None
    mapping_config: dict[str, JsonValue] | None = None


class UploadResult(ContractModel):
    log_id: Identifier
    parser: Identifier
    stats: UploadStats
    issues: list[ParseIssue]
    mapping_snapshot_id: Identifier
    mapping_snapshot: dict[str, JsonValue]
    raw_file_ref: Identifier
    raw_file_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    channels: list[ChannelDescriptor]


class LogChannels(ContractModel):
    log_id: Identifier
    channels: list[ChannelDescriptor]


def _csv_channels(value):
    if not isinstance(value, str):
        raise ValueError("channels must be a comma-separated string")
    items = [part.strip() for part in value.split(",")]
    if not items or any(not part for part in items):
        raise ValueError("channels must not contain empty entries")
    return list(dict.fromkeys(items))


class CurveQuery(ContractModel):
    channels: Annotated[list[Identifier], BeforeValidator(_csv_channels, json_schema_input_type=str), Field(min_length=1, max_length=128)]
    from_s: NonnegativeNumber = 0.0
    to_s: NonnegativeNumber | None = None
    max_points: Annotated[SafeInt, Field(ge=2, le=10000)] = 2000

    @model_validator(mode="after")
    def ordered_range(self):
        if self.to_s is not None and self.from_s > self.to_s:
            raise ValueError("from_s must be less than or equal to to_s")
        return self


class LimitInterval(ContractModel):
    channel: Identifier
    from_s: NonnegativeNumber
    to_s: NonnegativeNumber
    severity: Literal["warning", "error"]
    code: Identifier

    @model_validator(mode="after")
    def ordered_range(self):
        if self.from_s > self.to_s:
            raise ValueError("limit interval must have ordered times")
        return self


class CurvePoint(ContractModel):
    time_s: NonnegativeNumber
    value: FiniteNumber | None
    quality: ChannelQuality

    @model_validator(mode="after")
    def null_sample_is_not_valid(self):
        if self.value is None and self.quality.valid:
            raise ValueError("null curve values cannot claim valid source data")
        return self


class CurveSeries(ContractModel):
    channel: Identifier
    unit: Identifier
    points: list[CurvePoint] = Field(max_length=10000)
    limit_intervals: list[LimitInterval] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered_points(self):
        if any(a.time_s >= b.time_s for a, b in zip(self.points, self.points[1:])):
            raise ValueError("curve points must have strictly increasing source times")
        if any(interval.channel != self.channel for interval in self.limit_intervals):
            raise ValueError("limit interval must identify its series channel")
        return self


class CurveResponse(ContractModel):
    log_id: Identifier
    from_s: NonnegativeNumber
    to_s: NonnegativeNumber
    max_points: Annotated[SafeInt, Field(ge=2, le=10000)]
    downsampling: Literal["min_max"] = "min_max"
    series: list[CurveSeries] = Field(max_length=128)

    @model_validator(mode="after")
    def respect_query_budget(self):
        if self.from_s > self.to_s:
            raise ValueError("curve response range must be ordered")
        if len({series.channel for series in self.series}) != len(self.series):
            raise ValueError("curve series channels must be unique")
        if any(len(series.points) > self.max_points for series in self.series):
            raise ValueError("per-channel point budget exceeded")
        if any(not self.from_s <= point.time_s <= self.to_s for series in self.series for point in series.points):
            raise ValueError("curve points must lie within the requested range")
        return self


class ControlRequest(ContractModel):
    request_id: Identifier | None = Field(default=None, description="Opaque correlation ID; server creates one when omitted. Never compare IDs for ordering.")


class LoadRequest(ControlRequest):
    log_id: Identifier


class PlayRequest(ControlRequest):
    speed: Speed | None = None


class SeekRequest(ControlRequest):
    model_config = ConfigDict(json_schema_extra={"oneOf": [
        {"required": ["time_s"], "properties": {"time_s": {"type": "number"}, "frame_index": False}},
        {"required": ["frame_index"], "properties": {"frame_index": {"type": "integer"}, "time_s": False}},
    ]})
    time_s: FiniteNumber | None = None
    frame_index: SignedSafeInt | None = None

    @model_validator(mode="after")
    def exactly_one_position(self):
        if len(self.model_fields_set & {"time_s", "frame_index"}) != 1 or (self.time_s is None) == (self.frame_index is None):
            raise ValueError("exactly one of time_s or frame_index is required")
        return self


class StepRequest(ControlRequest):
    direction: Literal["forward", "backward"]


class SpeedRequest(ControlRequest):
    speed: Speed


class RESTPlaybackResponse(ContractModel):
    """Accepted control; server state is not evidence of browser presentation."""
    request_id: Identifier
    control_seq: SafeInt
    playback_epoch: SafeInt
    ack_stage: Literal["accepted"] = "accepted"
    playback: PlaybackSnapshot

    @model_validator(mode="after")
    def matching_control_identity(self):
        if self.control_seq != self.playback.control_seq or self.playback_epoch != self.playback.playback_epoch:
            raise ValueError("response and playback control identities must match")
        return self


REST_MODELS = {model.__name__: model for model in (
    ErrorDetail, ErrorEnvelope, Scene, SceneList, USDVariant, Robot, RobotList,
    JointDescriptor, JointMetadata, WorkerStatus, IceServer, VideoInfo,
    CreateSessionRequest, SessionCreated, SessionStatus, ParseIssue,
    ChassisAvailability, UploadStats, ChannelDescriptor, UploadOptions,
    UploadResult, LogChannels, CurveQuery, LimitInterval, CurvePoint,
    CurveSeries, CurveResponse, ControlRequest, LoadRequest, PlayRequest,
    SeekRequest, StepRequest, SpeedRequest, RESTPlaybackResponse, PlaybackSnapshot,
)}
__all__ = [*REST_MODELS, "REST_MODELS", "JointIndex"]
