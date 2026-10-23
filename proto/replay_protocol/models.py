"""Validation at REST/WS/IPC boundaries, without transport or replay behavior."""
from __future__ import annotations

import math
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictInt,
    StrictStr, TypeAdapter, model_validator,
)


def _integer(value):
    if type(value) is not int:
        raise ValueError("an integer is required; booleans are not numbers")
    return value


def _verified(value):
    if value is not True:
        raise ValueError("verified render identity requires the boolean true")
    return value


SafeInt = Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]
FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]
NonnegativeNumber = Annotated[FiniteNumber, Field(ge=0)]
Speed = Annotated[FiniteNumber, Field(ge=0.1, le=10.0)]
CaptureId = Annotated[int, Field(strict=True, ge=1, le=2**32 - 1)]
BitMask28 = Annotated[int, Field(strict=True, ge=0, lt=2**28)]
SchemaVersion = Annotated[Literal[1], BeforeValidator(_integer)]
Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=256)]
type JsonValue = Union[StrictStr, StrictBool, StrictInt, FiniteNumber, None,
                       list[JsonValue], dict[str, JsonValue]]

CANONICAL_JOINTS = (
    *(name for i in range(1, 4) for name in (f"steer_motor_joint{i}", f"wheel_motor_joint{i}")),
    *(f"torso_joint{i}" for i in range(1, 5)),
    *(f"left_arm_joint{i}" for i in range(1, 8)),
    "left_gripper_finger_joint1", "left_gripper_finger_joint2",
    *(f"right_arm_joint{i}" for i in range(1, 8)),
    "right_gripper_finger_joint1", "right_gripper_finger_joint2",
)
PlaybackState = Literal["idle", "ready", "playing", "paused", "ended", "error"]
Processing = Literal["original", "unit_converted", "interpolated", "forward_filled", "defaulted", "derived"]
AngleSemantics = Literal["bounded", "angular_wrapped", "angular_unwrapped", "linear"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              allow_inf_nan=False, validate_default=True,
                              revalidate_instances="always")


class ChannelQuality(ContractModel):
    origin: Literal["measured", "commanded", "derived", "missing"]
    valid: StrictBool
    processing: Annotated[tuple[Processing, ...], Field(strict=False)] = ()
    source_indices: Annotated[tuple[SafeInt, ...], Field(strict=False)] = ()
    method: Identifier | None = None

    @model_validator(mode="after")
    def coherent_quality(self):
        if self.origin == "missing" and self.valid:
            raise ValueError("missing source cannot be valid measured data")
        if self.origin == "derived" and not self.method:
            raise ValueError("derived values require an identified method")
        if "original" in self.processing and any(
            item in self.processing for item in ("interpolated", "forward_filled", "defaulted", "derived")
        ):
            raise ValueError("processed values cannot also claim original samples")
        if len(self.processing) != len(set(self.processing)):
            raise ValueError("processing operations must be unique")
        return self


class ChassisPose(ContractModel):
    x: FiniteNumber
    y: FiniteNumber
    yaw: FiniteNumber
    frame: Literal["world"] = "world"


class SessionIdentity(ContractModel):
    schema_version: SchemaVersion = 1
    session_id: Identifier


class TargetIdentity(SessionIdentity):
    log_id: Identifier
    playback_epoch: SafeInt
    control_seq: SafeInt
    target_seq: SafeInt


class FrameIdentity(TargetIdentity):
    capture_id: CaptureId
    render_seq: CaptureId

    @model_validator(mode="after")
    def capture_is_render_identity(self):
        if self.render_seq != self.capture_id:
            raise ValueError("v1 render_seq is explicitly identical to capture_id")
        return self


class PositionRef(ContractModel):
    log_time_s: NonnegativeNumber
    source_sample_index: SafeInt | None = None
    target_seq: SafeInt


class PlaybackSnapshot(SessionIdentity):
    log_id: Identifier | None = None
    playback_epoch: SafeInt
    control_seq: SafeInt
    state: PlaybackState
    speed: Speed = 1.0
    duration_s: NonnegativeNumber = 0.0
    target_position: PositionRef | None = None
    applied_position: PositionRef | None = None
    rendered_identity: FrameIdentity | None = None

    @model_validator(mode="after")
    def coherent_positions(self):
        if self.log_id is None and any((self.target_position, self.applied_position, self.rendered_identity)):
            raise ValueError("positions require a loaded log")
        if self.state not in ("idle", "error") and self.log_id is None:
            raise ValueError("active playback requires a log")
        for position in (self.target_position, self.applied_position):
            if position is not None and position.log_time_s > self.duration_s:
                raise ValueError("position exceeds duration")
        if self.rendered_identity is not None:
            for key in ("session_id", "log_id", "playback_epoch"):
                if getattr(self.rendered_identity, key) != getattr(self, key):
                    raise ValueError("rendered identity belongs to another session/log/epoch")
            if self.rendered_identity.control_seq > self.control_seq:
                raise ValueError("rendered identity cannot precede acceptance of its control")
        return self


JointValues = Annotated[tuple[FiniteNumber, ...], Field(min_length=28, max_length=28, strict=False)]
JointQualities = Annotated[tuple[ChannelQuality, ...], Field(min_length=28, max_length=28, strict=False)]


class SamplePosition(ContractModel):
    log_time_s: NonnegativeNumber
    duration_s: NonnegativeNumber
    progress: Annotated[FiniteNumber, Field(ge=0, le=1)]
    source_sample_index: SafeInt | None = None

    @model_validator(mode="after")
    def coherent_progress(self):
        if self.log_time_s > self.duration_s:
            raise ValueError("log time exceeds duration")
        expected = self.log_time_s / self.duration_s if self.duration_s else 0.0
        if not math.isclose(self.progress, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("progress must equal log_time_s / duration_s (zero for zero duration)")
        return self


class TargetFrame(TargetIdentity, SamplePosition):
    joints: JointValues
    quality: JointQualities
    chassis: ChassisPose | None
    mode: Literal["kinematic"] = "kinematic"
    joint_velocities: None = None
    joint_efforts: None = None


class AppliedFrame(TargetFrame):
    source_joints: JointValues
    applied_wall_s: NonnegativeNumber


class RenderedFrame(AppliedFrame, FrameIdentity):
    captured_wall_s: NonnegativeNumber
    raw_identity_verified: Annotated[Literal[True], BeforeValidator(_verified)]

    @model_validator(mode="after")
    def capture_follows_application(self):
        if self.captured_wall_s < self.applied_wall_s:
            raise ValueError("capture cannot precede application")
        return self


class FrameSnapshot(FrameIdentity, SamplePosition):
    state: Literal["ready", "playing", "paused", "ended"]
    speed: Speed
    joints: JointValues
    source_joints: JointValues
    quality: JointQualities
    chassis: ChassisPose | None
    limit_flags: BitMask28
    limit_warn_flags: BitMask28
    captured_wall_s: NonnegativeNumber


class PresentedFrame(ContractModel):
    schema_version: SchemaVersion = 1
    snapshot: FrameSnapshot
    connection_id: Identifier
    connection_generation: SafeInt
    presented_wall_s: NonnegativeNumber


MarkerPixel = Annotated[tuple[SafeInt, SafeInt], Field(strict=False)]
MarkerPixels = Annotated[tuple[MarkerPixel, ...], Field(min_length=64, max_length=64, strict=False)]


class SnapshotCatalog(SessionIdentity):
    identity_scheme: Literal["capture32_crc32"] = "capture32_crc32"
    marker_bits: Literal[64] = 64
    marker_pixels: MarkerPixels
    snapshots: Annotated[tuple[FrameSnapshot, ...], Field(min_length=1, max_length=16, strict=False)]
    encoder_poll_capture_id: CaptureId | None = None

    @model_validator(mode="after")
    def unique_session_candidates(self):
        if len(set(self.marker_pixels)) != 64:
            raise ValueError('image identity requires 64 distinct pixel coordinates')
        ids = set()
        for snapshot in self.snapshots:
            if snapshot.session_id != self.session_id:
                raise ValueError("catalog candidates must belong to its session")
            if snapshot.capture_id in ids:
                raise ValueError("catalog capture IDs must be unique")
            ids.add(snapshot.capture_id)
        return self


def _check_times(times):
    if any(current <= previous for previous, current in zip(times, times[1:])):
        raise ValueError("normalized sample times must be strictly increasing")


class ChannelSeries(ContractModel):
    timestamps_s: Annotated[list[NonnegativeNumber], Field(min_length=1)]
    values: list[FiniteNumber | None]
    quality: list[ChannelQuality]
    source_field: Identifier | None = None
    unit: Identifier
    angle_semantics: AngleSemantics
    wrap_period: Annotated[FiniteNumber, Field(gt=0)] | None = None

    @model_validator(mode="after")
    def coherent_series(self):
        _check_times(self.timestamps_s)
        if not len(self.timestamps_s) == len(self.values) == len(self.quality):
            raise ValueError("timestamps, values and quality must have equal lengths")
        if any(value is None and quality.valid for value, quality in zip(self.values, self.quality)):
            raise ValueError("null samples cannot claim valid source data")
        if self.angle_semantics == "angular_wrapped" and self.wrap_period is None:
            raise ValueError("wrapped angular sources require a wrap period")
        if self.angle_semantics != "angular_wrapped" and self.wrap_period is not None:
            raise ValueError("a wrap period is only meaningful for wrapped angular sources")
        return self


class TimeBasis(ContractModel):
    unit: Literal["s"] = "s"
    source_origin: FiniteNumber
    relative_origin_s: NonnegativeNumber = 0.0


class ReplayData(ContractModel):
    schema_version: SchemaVersion = 1
    log_id: Identifier
    robot_id: Identifier
    raw_file_ref: Identifier
    raw_file_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mapping_snapshot_id: Identifier
    mapping_snapshot: dict[str, JsonValue]
    time_basis: TimeBasis
    canonical_order: Annotated[list[Identifier], Field(min_length=28, max_length=28)]
    sample_times_s: Annotated[list[NonnegativeNumber], Field(min_length=1)]
    channels: dict[Identifier, ChannelSeries]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def canonical_original_samples(self):
        if tuple(self.canonical_order) != CANONICAL_JOINTS:
            raise ValueError("canonical joint names and order must exactly match the protocol")
        _check_times(self.sample_times_s)
        if not self.channels:
            raise ValueError("a replay requires at least one source channel")
        if self.sample_times_s[0] != self.time_basis.relative_origin_s:
            raise ValueError("sample time origin must match time_basis")
        for channel in self.channels.values():
            if channel.timestamps_s[0] < self.sample_times_s[0] or channel.timestamps_s[-1] > self.sample_times_s[-1]:
                raise ValueError("channel samples must lie within the replay time domain")
        return self


class LoadScene(ContractModel):
    cmd: Literal["load_scene"]
    scene_id: Identifier


class LoadRobot(ContractModel):
    cmd: Literal["load_robot"]
    robot_id: Identifier


class LoadLog(ContractModel):
    cmd: Literal["load_log"]
    log_id: Identifier


class ApplyKeyframe(ContractModel):
    cmd: Literal["apply_keyframe"]
    frame: TargetFrame


class PauseBarrier(ContractModel):
    cmd: Literal["pause_barrier"]
    target_seq: SafeInt


class Reset(ContractModel):
    cmd: Literal["reset"]


class Shutdown(ContractModel):
    cmd: Literal["shutdown"]


class Heartbeat(ContractModel):
    cmd: Literal["heartbeat"]


class SubscribeReady(ContractModel):
    cmd: Literal["subscribe_ready"]
    channels: Annotated[list[Literal["target", "telemetry"]], Field(min_length=1, max_length=2)]


RPCCommand = Annotated[Union[LoadScene, LoadRobot, LoadLog, ApplyKeyframe, PauseBarrier,
                             Reset, Shutdown, Heartbeat, SubscribeReady], Field(discriminator="cmd")]


class RPCRequest(SessionIdentity):
    request_id: Identifier
    control_seq: SafeInt
    playback_epoch: SafeInt
    command: RPCCommand

    @model_validator(mode="after")
    def nested_frame_identity(self):
        if isinstance(self.command, ApplyKeyframe):
            _same_identity(self, self.command.frame)
        return self


class ProtocolError(ContractModel):
    code: Identifier
    message: Identifier
    details: dict[str, JsonValue] = Field(default_factory=dict)


def _same_identity(envelope, frame):
    for name in ("session_id", "control_seq", "playback_epoch"):
        if getattr(envelope, name) != getattr(frame, name):
            raise ValueError(f"nested {name} does not match its envelope")


class RPCResponse(SessionIdentity):
    request_id: Identifier
    control_seq: SafeInt
    playback_epoch: SafeInt
    stage: Literal["accepted", "applied", "rendered"]
    ok: StrictBool
    error: ProtocolError | None = None
    frame: RenderedFrame | AppliedFrame | None = None

    @model_validator(mode="after")
    def evidence_matches_stage(self):
        if self.ok == (self.error is not None):
            raise ValueError("failed responses require error; successful responses forbid error")
        if not self.ok:
            if self.frame is not None:
                raise ValueError("failed responses cannot claim successful frame evidence")
            return self
        if self.stage == "accepted" and self.frame is not None:
            raise ValueError("accepted responses do not claim applied or rendered evidence")
        if self.stage == "applied" and type(self.frame) is not AppliedFrame:
            raise ValueError("applied responses require AppliedFrame evidence")
        if self.stage == "rendered" and not isinstance(self.frame, RenderedFrame):
            raise ValueError("rendered responses require RenderedFrame evidence")
        if self.frame is not None:
            _same_identity(self, self.frame)
        return self


class WorkerReady(ContractModel):
    type: Literal["ready"]
    isaacsim_version: Literal["6.2.0"] = "6.2.0"
    physics_engine: Literal["physx"] = "physx"


class WorkerHealth(ContractModel):
    type: Literal["health"]
    render_fps: NonnegativeNumber
    wall_time_s: NonnegativeNumber


class WorkerApplied(ContractModel):
    type: Literal["applied"]
    frame: AppliedFrame


class WorkerRendered(ContractModel):
    type: Literal["rendered"]
    frame: RenderedFrame


class WorkerError(ContractModel):
    type: Literal["error"]
    error: ProtocolError


WorkerPayload = Annotated[Union[WorkerReady, WorkerHealth, WorkerApplied, WorkerRendered,
                               WorkerError], Field(discriminator="type")]


class WorkerEvent(SessionIdentity):
    seq: SafeInt
    event: WorkerPayload

    @model_validator(mode="after")
    def session_matches_payload(self):
        if isinstance(self.event, (WorkerApplied, WorkerRendered)) and self.event.frame.session_id != self.session_id:
            raise ValueError("worker event frame belongs to another session")
        return self


class TargetMessage(ContractModel):
    schema_version: SchemaVersion = 1
    type: Literal["target"] = "target"
    frame: TargetFrame


RPC_ADAPTER = TypeAdapter(RPCRequest)
RPC_RESPONSE_ADAPTER = TypeAdapter(RPCResponse)
WORKER_EVENT_ADAPTER = TypeAdapter(WorkerEvent)
TARGET_ADAPTER = TypeAdapter(TargetMessage)
PROTOCOL_ADAPTERS = {
    "RPCRequest": RPC_ADAPTER, "RPCResponse": RPC_RESPONSE_ADAPTER,
    "WorkerEvent": WORKER_EVENT_ADAPTER, "TargetMessage": TARGET_ADAPTER,
    "RPCCommand": TypeAdapter(RPCCommand), "WorkerPayload": TypeAdapter(WorkerPayload),
}
PROTOCOL_MODELS = {model.__name__: model for model in (
    ChannelQuality, ChassisPose, PositionRef, FrameIdentity, PlaybackSnapshot,
    TargetFrame, AppliedFrame, RenderedFrame, FrameSnapshot, PresentedFrame,
    SnapshotCatalog, ChannelSeries, TimeBasis, ReplayData, RPCRequest, RPCResponse,
    WorkerEvent, TargetMessage, ProtocolError,
)}
__all__ = [
    "ContractModel", "SafeInt", "FiniteNumber", "NonnegativeNumber", "Speed", "CaptureId",
    "BitMask28", "SchemaVersion", "Identifier", "JsonValue", "CANONICAL_JOINTS",
    "PlaybackState", "Processing", "AngleSemantics", "JointValues", "JointQualities",
    "RPCCommand", "WorkerPayload", "RPC_ADAPTER", "RPC_RESPONSE_ADAPTER",
    "WORKER_EVENT_ADAPTER", "TARGET_ADAPTER", "PROTOCOL_MODELS", "PROTOCOL_ADAPTERS",
    *PROTOCOL_MODELS,
]
