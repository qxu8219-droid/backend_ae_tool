/* Generated from Python contracts. Do not edit. */

export type Command =
  LoadScene | LoadRobot | LoadLog | ApplyKeyframe | PauseBarrier | Reset | Shutdown | Heartbeat | SubscribeReady;
export type Cmd = "load_scene";
export type SceneId = string;
export type Cmd1 = "load_robot";
export type RobotId = string;
export type Cmd2 = "load_log";
export type LogId = string;
export type Cmd3 = "apply_keyframe";
export type Frame = "world";
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
export type LogId1 = string;
export type LogTimeS = number;
export type Mode = "kinematic";
export type PlaybackEpoch = number;
export type Progress = number;
/**
 * @minItems 28
 * @maxItems 28
 */
export type Quality = [
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
export type Method = string | null;
export type Origin = "measured" | "commanded" | "derived" | "missing";
export type Processing = (
  "original" | "unit_converted" | "interpolated" | "forward_filled" | "defaulted" | "derived"
)[];
export type SourceIndices = number[];
export type Valid = boolean;
export type SchemaVersion = 1;
export type SessionId = string;
export type SourceSampleIndex = number | null;
export type Speed = number;
export type State = "ready" | "playing" | "paused" | "ended";
export type TargetSeq = number;
export type Cmd4 = "pause_barrier";
export type TargetSeq1 = number;
export type Cmd5 = "reset";
export type Cmd6 = "shutdown";
export type Cmd7 = "heartbeat";
/**
 * @minItems 1
 * @maxItems 2
 */
export type Channels = ["target" | "telemetry"] | ["target" | "telemetry", "target" | "telemetry"];
export type Cmd8 = "subscribe_ready";
export type ControlSeq1 = number;
export type PlaybackEpoch1 = number;
export type RequestId = string;
export type SchemaVersion1 = 1;
export type SessionId1 = string;

export interface RpcRequest {
  command: Command;
  control_seq: ControlSeq1;
  playback_epoch: PlaybackEpoch1;
  request_id: RequestId;
  schema_version?: SchemaVersion1;
  session_id: SessionId1;
}
export interface LoadScene {
  cmd: Cmd;
  scene_id: SceneId;
}
export interface LoadRobot {
  cmd: Cmd1;
  robot_id: RobotId;
}
export interface LoadLog {
  cmd: Cmd2;
  log_id: LogId;
}
export interface ApplyKeyframe {
  cmd: Cmd3;
  frame: TargetFrame;
}
export interface TargetFrame {
  chassis: ChassisPose | null;
  control_seq: ControlSeq;
  duration_s: DurationS;
  joint_efforts?: JointEfforts;
  joint_velocities?: JointVelocities;
  joints: Joints;
  log_id: LogId1;
  log_time_s: LogTimeS;
  mode?: Mode;
  playback_epoch: PlaybackEpoch;
  progress: Progress;
  quality: Quality;
  schema_version?: SchemaVersion;
  session_id: SessionId;
  source_sample_index?: SourceSampleIndex;
  speed?: Speed;
  state?: State;
  target_seq: TargetSeq;
}
export interface ChassisPose {
  frame?: Frame;
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
export interface PauseBarrier {
  cmd: Cmd4;
  target_seq: TargetSeq1;
}
export interface Reset {
  cmd: Cmd5;
}
export interface Shutdown {
  cmd: Cmd6;
}
export interface Heartbeat {
  cmd: Cmd7;
}
export interface SubscribeReady {
  channels: Channels;
  cmd: Cmd8;
}
