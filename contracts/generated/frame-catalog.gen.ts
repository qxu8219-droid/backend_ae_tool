/* Generated from Python contracts. Do not edit. */

export type EncoderPollCaptureId = number | null;
export type IdentityScheme = "capture32_crc32";
export type MarkerBits = 64;
/**
 * @minItems 64
 * @maxItems 64
 */
export type MarkerPixels = [
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown],
  [unknown, unknown]
];
export type SchemaVersion = 1;
export type SessionId = string;
/**
 * @minItems 1
 * @maxItems 16
 */
export type Snapshots =
  | [FrameSnapshot]
  | [FrameSnapshot, FrameSnapshot]
  | [FrameSnapshot, FrameSnapshot, FrameSnapshot]
  | [FrameSnapshot, FrameSnapshot, FrameSnapshot, FrameSnapshot]
  | [FrameSnapshot, FrameSnapshot, FrameSnapshot, FrameSnapshot, FrameSnapshot]
  | [FrameSnapshot, FrameSnapshot, FrameSnapshot, FrameSnapshot, FrameSnapshot, FrameSnapshot]
  | [FrameSnapshot, FrameSnapshot, FrameSnapshot, FrameSnapshot, FrameSnapshot, FrameSnapshot, FrameSnapshot]
  | [
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot
    ]
  | [
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot
    ]
  | [
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot
    ]
  | [
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot
    ]
  | [
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot
    ]
  | [
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot
    ]
  | [
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot
    ]
  | [
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot
    ]
  | [
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot,
      FrameSnapshot
    ];
export type CaptureId = number;
export type CapturedWallS = number;
export type Frame = "world";
export type X = number;
export type Y = number;
export type Yaw = number;
export type ControlSeq = number;
export type DurationS = number;
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
export type LimitFlags = number;
export type LimitWarnFlags = number;
export type LogId = string;
export type LogTimeS = number;
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
export type RenderSeq = number;
export type SchemaVersion1 = 1;
export type SessionId1 = string;
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

export interface FrameCatalog {
  encoder_poll_capture_id?: EncoderPollCaptureId;
  identity_scheme?: IdentityScheme;
  marker_bits?: MarkerBits;
  marker_pixels: MarkerPixels;
  schema_version?: SchemaVersion;
  session_id: SessionId;
  snapshots: Snapshots;
}
export interface FrameSnapshot {
  capture_id: CaptureId;
  captured_wall_s: CapturedWallS;
  chassis: ChassisPose | null;
  control_seq: ControlSeq;
  duration_s: DurationS;
  joints: Joints;
  limit_flags: LimitFlags;
  limit_warn_flags: LimitWarnFlags;
  log_id: LogId;
  log_time_s: LogTimeS;
  playback_epoch: PlaybackEpoch;
  progress: Progress;
  quality: Quality;
  render_seq: RenderSeq;
  schema_version?: SchemaVersion1;
  session_id: SessionId1;
  source_joints: SourceJoints;
  source_sample_index?: SourceSampleIndex;
  speed: Speed;
  state: State;
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
