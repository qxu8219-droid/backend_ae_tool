/* Generated from Python contracts. Do not edit. */

export type WsServerMessage =
  | WSConnectionSnapshot
  | WSSystem
  | WSPlaybackState
  | WSFrameSnapshot
  | WSAlert
  | WSHealthTelemetry
  | WSPong
  | WSTelemetryRateChanged;
export type ConnectionId = string;
/**
 * @minItems 28
 * @maxItems 28
 */
export type CanonicalOrder = [
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  string
];
/**
 * @minItems 28
 * @maxItems 28
 */
export type Joints = [
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor,
  JointDescriptor
];
export type LimitsProvenance = string;
export type Lower = number | null;
export type Name = string;
export type Type = "revolute" | "continuous" | "prismatic";
export type Unit = "rad" | "m";
export type Upper = number | null;
/**
 * Authoritative versioned r1pro_2026_common.json path; limits retain per-joint provenance.
 */
export type LimitsSource = string;
export type RobotId = string;
export type LogTimeS = number;
export type SourceSampleIndex = number | null;
export type TargetSeq = number;
export type ControlSeq = number;
export type DurationS = number;
export type LogId = string | null;
export type PlaybackEpoch = number;
export type CaptureId = number;
export type ControlSeq1 = number;
export type LogId1 = string;
export type PlaybackEpoch1 = number;
export type RenderSeq = number;
export type SchemaVersion = 1;
export type SessionId = string;
export type TargetSeq1 = number;
export type SchemaVersion1 = 1;
export type SessionId1 = string;
export type Speed = number;
export type State = "idle" | "ready" | "playing" | "paused" | "ended" | "error";
export type GapAction = "reconnect_and_wait_for_pair";
export type IdentityScheme = "capture32_crc32";
export type PlaybackEpoch2 = number;
export type Presentation = "verified_pair_single_canvas";
export type SnapshotCatalogCapacity = 16;
export type State1 = "awaiting_pair" | "paired" | "stalled" | "resync_required";
export type TelemetryHz = 10 | 20 | 30;
export type CatalogSchemaVersion = 1;
export type Decoder = "webcodecs_h264";
export type Credential = string | null;
/**
 * @minItems 1
 */
export type Urls = [string, ...string[]];
export type Username = string | null;
export type IceServers = IceServer[];
export type IdentityScheme1 = "capture32_crc32";
export type Presentation1 = "verified_pair_single_canvas";
export type Publishing = boolean;
export type ReceiverTransform = "rtcrtp_script_transform_or_encoded_streams";
export type RenderFps = number | null;
export type SeiUuidHex = string | null;
export type SnapshotTransport = "h264_sei_snapshot_catalog";
export type StreamPath = string | null;
export type Transport = "whep";
export type WhepUrl = string | null;
export type Code = string;
export type JsonValue =
  | string
  | boolean
  | number
  | JsonValue[]
  | {
      [k: string]: JsonValue;
    }
  | null;
export type Message = string;
export type Message1 = string | null;
export type Phase =
  "booting" | "loading_scene" | "loading_robot" | "rtsp_publishing" | "ready" | "stopping" | "stopped" | "error";
export type Pid = number | null;
export type RestartPolicy = "recreate_session_reload_from_start";
export type SchemaVersion2 = 1;
/**
 * Monotonic within this connection_id only; gaps require reconnect/snapshot recovery.
 */
export type Seq = number;
export type SessionId2 = string;
export type Type1 = "connection_snapshot";
/**
 * UTC Unix seconds; system clock, not log time.
 */
export type WallTime = number;
export type ConnectionId1 = string;
export type Event =
  | "worker_phase_changed"
  | "worker_exited"
  | "session_error"
  | "mediamtx_unreachable"
  | "mediamtx_recovered"
  | "sync_resync_required"
  | "session_stopped";
export type Message2 = string | null;
export type SchemaVersion3 = 1;
/**
 * Monotonic within this connection_id only; gaps require reconnect/snapshot recovery.
 */
export type Seq1 = number;
export type SessionId3 = string;
export type Type2 = "system";
/**
 * UTC Unix seconds; system clock, not log time.
 */
export type WallTime1 = number;
export type ConnectionId2 = string;
export type Reason = string;
export type RequestId = string | null;
export type SchemaVersion4 = 1;
/**
 * Monotonic within this connection_id only; gaps require reconnect/snapshot recovery.
 */
export type Seq2 = number;
export type SessionId4 = string;
export type Type3 = "playback_state";
/**
 * UTC Unix seconds; system clock, not log time.
 */
export type WallTime2 = number;
export type ConnectionId3 = string;
export type CaptureId1 = number;
export type CapturedWallS = number;
export type Frame = "world";
export type Quality = [ChannelQuality, ChannelQuality, ChannelQuality] | null;
export type Method = string | null;
export type Origin = "measured" | "commanded" | "derived" | "missing";
export type Processing = (
  "original" | "unit_converted" | "interpolated" | "forward_filled" | "defaulted" | "derived"
)[];
export type SourceIndices = number[];
export type Valid = boolean;
export type X = number;
export type Y = number;
export type Yaw = number;
export type ControlSeq2 = number;
export type DurationS1 = number;
/**
 * @minItems 28
 * @maxItems 28
 */
export type Joints1 = [
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number
];
export type LimitFlags = number;
export type LimitWarnFlags = number;
export type LogId2 = string;
export type LogTimeS1 = number;
export type PlaybackEpoch3 = number;
export type Progress = number;
/**
 * @minItems 28
 * @maxItems 28
 */
export type Quality1 = [
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality,
  ChannelQuality
];
export type RenderSeq1 = number;
export type SchemaVersion5 = 1;
export type SessionId5 = string;
/**
 * @minItems 28
 * @maxItems 28
 */
export type SourceJoints = [
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number
];
export type SourceSampleIndex1 = number | null;
export type Speed1 = number;
export type State2 = "ready" | "playing" | "paused" | "ended";
export type TargetSeq2 = number;
export type SchemaVersion6 = 1;
/**
 * Monotonic within this connection_id only; gaps require reconnect/snapshot recovery.
 */
export type Seq3 = number;
export type SessionId6 = string;
export type Type4 = "frame_snapshot";
/**
 * UTC Unix seconds; system clock, not log time.
 */
export type WallTime3 = number;
export type ConnectionId4 = string;
export type AlertId = string;
export type Code1 = string;
export type JointIndex = number | null;
export type LogId3 = string | null;
export type LogTimeS2 = number | null;
export type Message3 = string;
export type PlaybackEpoch4 = number | null;
export type Severity = "error" | "warning" | "info";
export type Source = string;
export type SchemaVersion7 = 1;
/**
 * Monotonic within this connection_id only; gaps require reconnect/snapshot recovery.
 */
export type Seq4 = number;
export type SessionId7 = string;
export type Type5 = "alert";
/**
 * UTC Unix seconds; system clock, not log time.
 */
export type WallTime4 = number;
export type ConnectionId5 = string;
export type CpuPercent = number | null;
export type GpuMemoryBytes = number | null;
export type GpuPercent = number | null;
export type MemoryBytes = number | null;
export type RenderFps1 = number | null;
export type WorkerAlive = boolean;
export type SchemaVersion8 = 1;
/**
 * Monotonic within this connection_id only; gaps require reconnect/snapshot recovery.
 */
export type Seq5 = number;
export type SessionId8 = string;
export type Type6 = "health_telemetry";
/**
 * UTC Unix seconds; system clock, not log time.
 */
export type WallTime5 = number;
export type ConnectionId6 = string;
export type RequestId1 = string;
export type SchemaVersion9 = 1;
/**
 * Monotonic within this connection_id only; gaps require reconnect/snapshot recovery.
 */
export type Seq6 = number;
export type SessionId9 = string;
export type Type7 = "pong";
/**
 * UTC Unix seconds; system clock, not log time.
 */
export type WallTime6 = number;
export type ConnectionId7 = string;
export type Hz = 10 | 20 | 30;
export type RequestId2 = string;
export type SchemaVersion10 = 1;
/**
 * Monotonic within this connection_id only; gaps require reconnect/snapshot recovery.
 */
export type Seq7 = number;
export type SessionId10 = string;
export type Type8 = "telemetry_rate_changed";
/**
 * UTC Unix seconds; system clock, not log time.
 */
export type WallTime7 = number;

export interface WSConnectionSnapshot {
  connection_id: ConnectionId;
  data: ConnectionSnapshotData;
  schema_version?: SchemaVersion2;
  seq: Seq;
  session_id: SessionId2;
  type: Type1;
  wall_time: WallTime;
}
export interface ConnectionSnapshotData {
  joint_meta: JointMetadata;
  playback: PlaybackSnapshot;
  sync: SyncStatus;
  telemetry_hz?: TelemetryHz;
  video: VideoInfo;
  worker: WorkerStatus;
}
export interface JointMetadata {
  canonical_order: CanonicalOrder;
  joints: Joints;
  limits_source: LimitsSource;
  robot_id: RobotId;
}
export interface JointDescriptor {
  limits_provenance: LimitsProvenance;
  lower: Lower;
  name: Name;
  type: Type;
  unit: Unit;
  upper: Upper;
}
export interface PlaybackSnapshot {
  applied_position?: PositionRef | null;
  control_seq: ControlSeq;
  duration_s?: DurationS;
  log_id?: LogId;
  playback_epoch: PlaybackEpoch;
  rendered_identity?: FrameIdentity | null;
  schema_version?: SchemaVersion1;
  session_id: SessionId1;
  speed?: Speed;
  state: State;
  target_position?: PositionRef | null;
}
export interface PositionRef {
  log_time_s: LogTimeS;
  source_sample_index?: SourceSampleIndex;
  target_seq: TargetSeq;
}
export interface FrameIdentity {
  capture_id: CaptureId;
  control_seq: ControlSeq1;
  log_id: LogId1;
  playback_epoch: PlaybackEpoch1;
  render_seq: RenderSeq;
  schema_version?: SchemaVersion;
  session_id: SessionId;
  target_seq: TargetSeq1;
}
export interface SyncStatus {
  gap_action?: GapAction;
  identity_scheme?: IdentityScheme;
  last_rendered_identity?: FrameIdentity | null;
  playback_epoch: PlaybackEpoch2;
  presentation?: Presentation;
  snapshot_catalog_capacity?: SnapshotCatalogCapacity;
  state: State1;
}
export interface VideoInfo {
  catalog_schema_version?: CatalogSchemaVersion;
  decoder?: Decoder;
  ice_servers?: IceServers;
  identity_scheme?: IdentityScheme1;
  presentation?: Presentation1;
  publishing: Publishing;
  receiver_transform?: ReceiverTransform;
  render_fps?: RenderFps;
  sei_uuid_hex?: SeiUuidHex;
  snapshot_transport?: SnapshotTransport;
  stream_path: StreamPath;
  transport?: Transport;
  whep_url: WhepUrl;
}
export interface IceServer {
  credential?: Credential;
  urls: Urls;
  username?: Username;
}
export interface WorkerStatus {
  error?: ErrorDetail | null;
  message?: Message1;
  phase: Phase;
  pid?: Pid;
  restart_policy?: RestartPolicy;
}
export interface ErrorDetail {
  code: Code;
  details?: Details;
  message: Message;
}
export interface Details {
  [k: string]: JsonValue;
}
export interface WSSystem {
  connection_id: ConnectionId1;
  data: SystemData;
  schema_version?: SchemaVersion3;
  seq: Seq1;
  session_id: SessionId3;
  type: Type2;
  wall_time: WallTime1;
}
export interface SystemData {
  error?: ErrorDetail | null;
  event: Event;
  message?: Message2;
  worker?: WorkerStatus | null;
}
export interface WSPlaybackState {
  connection_id: ConnectionId2;
  data: PlaybackStateData;
  schema_version?: SchemaVersion4;
  seq: Seq2;
  session_id: SessionId4;
  type: Type3;
  wall_time: WallTime2;
}
export interface PlaybackStateData {
  playback: PlaybackSnapshot;
  reason: Reason;
  request_id?: RequestId;
}
export interface WSFrameSnapshot {
  connection_id: ConnectionId3;
  data: FrameSnapshot;
  schema_version?: SchemaVersion6;
  seq: Seq3;
  session_id: SessionId6;
  type: Type4;
  wall_time: WallTime3;
}
export interface FrameSnapshot {
  capture_id: CaptureId1;
  captured_wall_s: CapturedWallS;
  chassis: ChassisPose | null;
  control_seq: ControlSeq2;
  duration_s: DurationS1;
  joints: Joints1;
  limit_flags: LimitFlags;
  limit_warn_flags: LimitWarnFlags;
  log_id: LogId2;
  log_time_s: LogTimeS1;
  playback_epoch: PlaybackEpoch3;
  progress: Progress;
  quality: Quality1;
  render_seq: RenderSeq1;
  schema_version?: SchemaVersion5;
  session_id: SessionId5;
  source_joints: SourceJoints;
  source_sample_index?: SourceSampleIndex1;
  speed: Speed1;
  state: State2;
  target_seq: TargetSeq2;
}
export interface ChassisPose {
  frame?: Frame;
  quality?: Quality;
  x: X;
  y: Y;
  yaw: Yaw;
}
export interface ChannelQuality {
  method?: Method;
  origin: Origin;
  processing?: Processing;
  source_indices?: SourceIndices;
  valid: Valid;
}
export interface WSAlert {
  connection_id: ConnectionId4;
  data: AlertData;
  schema_version?: SchemaVersion7;
  seq: Seq4;
  session_id: SessionId7;
  type: Type5;
  wall_time: WallTime4;
}
export interface AlertData {
  alert_id: AlertId;
  code: Code1;
  details?: Details1;
  joint_index?: JointIndex;
  log_id?: LogId3;
  log_time_s?: LogTimeS2;
  message: Message3;
  playback_epoch?: PlaybackEpoch4;
  severity: Severity;
  source: Source;
}
export interface Details1 {
  [k: string]: JsonValue;
}
export interface WSHealthTelemetry {
  connection_id: ConnectionId5;
  data: HealthTelemetryData;
  schema_version?: SchemaVersion8;
  seq: Seq5;
  session_id: SessionId8;
  type: Type6;
  wall_time: WallTime5;
}
/**
 * Wall-clock resources only; contains no current-pose or log-progress fields.
 */
export interface HealthTelemetryData {
  cpu_percent?: CpuPercent;
  gpu_memory_bytes?: GpuMemoryBytes;
  gpu_percent?: GpuPercent;
  memory_bytes?: MemoryBytes;
  render_fps?: RenderFps1;
  worker_alive: WorkerAlive;
}
export interface WSPong {
  connection_id: ConnectionId6;
  data: PingData;
  schema_version?: SchemaVersion9;
  seq: Seq6;
  session_id: SessionId9;
  type: Type7;
  wall_time: WallTime6;
}
export interface PingData {
  request_id: RequestId1;
}
export interface WSTelemetryRateChanged {
  connection_id: ConnectionId7;
  data: TelemetryRateData;
  schema_version?: SchemaVersion10;
  seq: Seq7;
  session_id: SessionId10;
  type: Type8;
  wall_time: WallTime7;
}
export interface TelemetryRateData {
  hz: Hz;
  request_id: RequestId2;
}
