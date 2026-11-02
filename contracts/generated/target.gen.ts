/* Generated from Python contracts. Do not edit. */

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
export type SourceSampleIndex = number | null;
export type Speed = number;
export type State = "ready" | "playing" | "paused" | "ended";
export type TargetSeq = number;
export type SchemaVersion1 = 1;
export type Type = "target";

export interface TargetMessage {
  frame: TargetFrame;
  schema_version?: SchemaVersion1;
  type?: Type;
}
export interface TargetFrame {
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
