/* Generated from Python contracts. Do not edit. */

export type Event = WorkerReady | WorkerHealth | WorkerApplied | WorkerRendered | WorkerError;
export type IsaacsimVersion = "6.2.0";
export type PhysicsEngine = "physx";
export type Type = "ready";
export type RenderFps = number;
export type Type1 = "health";
export type WallTimeS = number;
export type AppliedWallS = number;
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
export type ControlSeq = number;
export type DurationS = number;
export type JointEfforts = null;
export type JointVelocities = null;
/**
 * @minItems 28
 * @maxItems 28
 */
export type Joints = [
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
export type LogId = string;
export type LogTimeS = number;
export type Mode = "kinematic";
export type PlaybackEpoch = number;
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
export type SchemaVersion = 1;
export type SessionId = string;
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
export type SourceSampleIndex = number | null;
export type Speed = number;
export type State = "ready" | "playing" | "paused" | "ended";
export type TargetSeq = number;
export type Type2 = "applied";
export type AppliedWallS1 = number;
export type CaptureId = number;
export type CapturedWallS = number;
export type ControlSeq1 = number;
export type DurationS1 = number;
export type JointEfforts1 = null;
export type JointVelocities1 = null;
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
export type LogId1 = string;
export type LogTimeS1 = number;
export type Mode1 = "kinematic";
export type PlaybackEpoch1 = number;
export type Progress1 = number;
/**
 * @minItems 28
 * @maxItems 28
 */
export type Quality2 = [
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
export type RawIdentityVerified = true;
export type RenderSeq = number;
export type SchemaVersion1 = 1;
export type SessionId1 = string;
/**
 * @minItems 28
 * @maxItems 28
 */
export type SourceJoints1 = [
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
export type State1 = "ready" | "playing" | "paused" | "ended";
export type TargetSeq1 = number;
export type Type3 = "rendered";
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
export type Type4 = "error";
export type SchemaVersion2 = 1;
export type Seq = number;
export type SessionId2 = string;

export interface WorkerEvent {
  event: Event;
  schema_version?: SchemaVersion2;
  seq: Seq;
  session_id: SessionId2;
}
export interface WorkerReady {
  isaacsim_version?: IsaacsimVersion;
  physics_engine?: PhysicsEngine;
  type: Type;
}
export interface WorkerHealth {
  render_fps: RenderFps;
  type: Type1;
  wall_time_s: WallTimeS;
}
export interface WorkerApplied {
  frame: AppliedFrame;
  type: Type2;
}
export interface AppliedFrame {
  applied_wall_s: AppliedWallS;
  chassis: ChassisPose | null;
  control_seq: ControlSeq;
  duration_s: DurationS;
  joint_efforts?: JointEfforts;
  joint_velocities?: JointVelocities;
  joints: Joints;
  log_id: LogId;
  log_time_s: LogTimeS;
  mode?: Mode;
  playback_epoch: PlaybackEpoch;
  progress: Progress;
  quality: Quality1;
  schema_version?: SchemaVersion;
  session_id: SessionId;
  source_joints: SourceJoints;
  source_sample_index?: SourceSampleIndex;
  speed?: Speed;
  state?: State;
  target_seq: TargetSeq;
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
export interface WorkerRendered {
  frame: RenderedFrame;
  type: Type3;
}
export interface RenderedFrame {
  applied_wall_s: AppliedWallS1;
  capture_id: CaptureId;
  captured_wall_s: CapturedWallS;
  chassis: ChassisPose | null;
  control_seq: ControlSeq1;
  duration_s: DurationS1;
  joint_efforts?: JointEfforts1;
  joint_velocities?: JointVelocities1;
  joints: Joints1;
  log_id: LogId1;
  log_time_s: LogTimeS1;
  mode?: Mode1;
  playback_epoch: PlaybackEpoch1;
  progress: Progress1;
  quality: Quality2;
  raw_identity_verified: RawIdentityVerified;
  render_seq: RenderSeq;
  schema_version?: SchemaVersion1;
  session_id: SessionId1;
  source_joints: SourceJoints1;
  source_sample_index?: SourceSampleIndex1;
  speed?: Speed1;
  state?: State1;
  target_seq: TargetSeq1;
}
export interface WorkerError {
  error: ProtocolError;
  type: Type4;
}
export interface ProtocolError {
  code: Code;
  details?: Details;
  message: Message;
}
export interface Details {
  [k: string]: JsonValue;
}
