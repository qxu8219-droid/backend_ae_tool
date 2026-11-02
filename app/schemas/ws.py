"""WS contracts used by the configured per-connection runtime.

seq is monotonic per connection and restarts with a new connection_id. A gap
requires reconnect and a fresh connection_snapshot; old connection messages must
be discarded. SnapshotCatalog metadata embedded in each video access unit plus
full capture32/CRC identity chooses the frame for the single presentation canvas.
WS frame_snapshot is supplemental matching data, never permission to display the
latest telemetry against old video. Ordinary 10/20/30Hz health traffic cannot
replace or rate-limit synchronization snapshots. Connection snapshots deliberately
carry no latest pose, so reconnect waits for a verified video/snapshot pair.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, TypeAdapter, model_validator
from replay_protocol import (
    ContractModel, FrameIdentity, FrameSnapshot, Identifier, JsonValue,
    NonnegativeNumber, PlaybackSnapshot, SafeInt, SchemaVersion,
)
from .rest import ErrorDetail, JointIndex, JointMetadata, VideoInfo, WorkerStatus


def _integer_rate(value):
    if type(value) is not int:
        raise ValueError("telemetry hz must be an integer")
    return value


TelemetryRate = Annotated[Literal[10, 20, 30], BeforeValidator(_integer_rate)]


class SyncStatus(ContractModel):
    state: Literal["awaiting_pair", "paired", "stalled", "resync_required"]
    playback_epoch: SafeInt
    identity_scheme: Literal["capture32_crc32"] = "capture32_crc32"
    presentation: Literal["verified_pair_single_canvas"] = "verified_pair_single_canvas"
    last_rendered_identity: FrameIdentity | None = None
    snapshot_catalog_capacity: Literal[16] = 16
    gap_action: Literal["reconnect_and_wait_for_pair"] = "reconnect_and_wait_for_pair"


class ConnectionSnapshotData(ContractModel):
    joint_meta: JointMetadata
    playback: PlaybackSnapshot
    worker: WorkerStatus
    video: VideoInfo
    sync: SyncStatus
    telemetry_hz: TelemetryRate = 20

    @model_validator(mode="after")
    def consistent_epoch(self):
        if self.sync.playback_epoch != self.playback.playback_epoch:
            raise ValueError("connection snapshot playback and sync epochs must match")
        identity = self.sync.last_rendered_identity
        if identity is not None and (
            identity.session_id != self.playback.session_id
            or identity.log_id != self.playback.log_id
            or identity.playback_epoch != self.playback.playback_epoch
            or identity.control_seq > self.playback.control_seq
        ):
            raise ValueError("render identity must belong to this session/log/epoch and not exceed the current control barrier")
        return self


class SystemData(ContractModel):
    event: Literal["worker_phase_changed", "worker_exited", "session_error", "mediamtx_unreachable", "mediamtx_recovered", "sync_resync_required", "session_stopped"]
    worker: WorkerStatus | None = None
    error: ErrorDetail | None = None
    message: str | None = None


class PlaybackStateData(ContractModel):
    playback: PlaybackSnapshot
    reason: Identifier
    request_id: Identifier | None = None


class AlertData(ContractModel):
    alert_id: Identifier
    severity: Literal["error", "warning", "info"]
    source: Identifier
    code: Identifier
    message: str
    log_id: Identifier | None = None
    playback_epoch: SafeInt | None = None
    log_time_s: NonnegativeNumber | None = None
    joint_index: JointIndex | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def complete_log_location(self):
        located = [self.log_id is not None, self.playback_epoch is not None, self.log_time_s is not None]
        if any(located) and not all(located):
            raise ValueError("log alerts require log_id, playback_epoch and log_time_s together")
        if self.joint_index is not None and not all(located):
            raise ValueError("a joint alert requires a complete log location")
        return self


class HealthTelemetryData(ContractModel):
    """Wall-clock resources only; contains no current-pose or log-progress fields."""
    worker_alive: bool
    render_fps: NonnegativeNumber | None = None
    cpu_percent: Annotated[NonnegativeNumber, Field(le=100)] | None = None
    memory_bytes: SafeInt | None = None
    gpu_percent: Annotated[NonnegativeNumber, Field(le=100)] | None = None
    gpu_memory_bytes: SafeInt | None = None


class PingData(ContractModel):
    request_id: Identifier


class TelemetryRateData(ContractModel):
    request_id: Identifier
    hz: TelemetryRate


class WSEnvelope(ContractModel):
    schema_version: SchemaVersion = 1
    session_id: Identifier
    connection_id: Identifier
    seq: SafeInt = Field(description="Monotonic within this connection_id only; gaps require reconnect/snapshot recovery.")
    wall_time: NonnegativeNumber = Field(description="UTC Unix seconds; system clock, not log time.")

    @model_validator(mode="after")
    def consistent_session(self):
        data = getattr(self, "data", None)
        nested = getattr(data, "playback", data)
        session = getattr(nested, "session_id", self.session_id)
        if session != self.session_id:
            raise ValueError("WS envelope session must match nested frame/playback")
        return self


class WSConnectionSnapshot(WSEnvelope):
    type: Literal["connection_snapshot"]
    data: ConnectionSnapshotData


class WSSystem(WSEnvelope):
    type: Literal["system"]
    data: SystemData


class WSPlaybackState(WSEnvelope):
    type: Literal["playback_state"]
    data: PlaybackStateData


class WSFrameSnapshot(WSEnvelope):
    type: Literal["frame_snapshot"]
    data: FrameSnapshot


class WSAlert(WSEnvelope):
    type: Literal["alert"]
    data: AlertData


class WSHealthTelemetry(WSEnvelope):
    type: Literal["health_telemetry"]
    data: HealthTelemetryData


class WSPong(WSEnvelope):
    type: Literal["pong"]
    data: PingData


class WSTelemetryRateChanged(WSEnvelope):
    type: Literal["telemetry_rate_changed"]
    data: TelemetryRateData


class WSPing(WSEnvelope):
    type: Literal["ping"]
    data: PingData


class WSSetTelemetryRate(WSEnvelope):
    type: Literal["set_telemetry_rate"]
    data: TelemetryRateData


WSServerMessage = Annotated[WSConnectionSnapshot | WSSystem | WSPlaybackState | WSFrameSnapshot | WSAlert | WSHealthTelemetry | WSPong | WSTelemetryRateChanged, Field(discriminator="type")]
WSClientMessage = Annotated[WSPing | WSSetTelemetryRate, Field(discriminator="type")]
WS_SERVER_ADAPTER = TypeAdapter(WSServerMessage)
WS_CLIENT_ADAPTER = TypeAdapter(WSClientMessage)
WS_MODELS = {model.__name__: model for model in (
    SyncStatus, ConnectionSnapshotData, SystemData, PlaybackStateData, AlertData,
    HealthTelemetryData, PingData, TelemetryRateData, WSConnectionSnapshot,
    WSSystem, WSPlaybackState, WSFrameSnapshot, WSAlert, WSHealthTelemetry,
    WSPong, WSTelemetryRateChanged, WSPing, WSSetTelemetryRate,
)}
__all__ = [*WS_MODELS, "WSServerMessage", "WSClientMessage", "WS_SERVER_ADAPTER", "WS_CLIENT_ADAPTER", "WS_MODELS"]
