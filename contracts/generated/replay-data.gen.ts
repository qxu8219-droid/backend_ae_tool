/* Generated from Python contracts. Do not edit. */

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
export type AngleSemantics = "bounded" | "angular_wrapped" | "angular_unwrapped" | "linear";
export type Method = string | null;
export type Origin = "measured" | "commanded" | "derived" | "missing";
export type Processing = (
  "original" | "unit_converted" | "interpolated" | "forward_filled" | "defaulted" | "derived"
)[];
export type SourceIndices = number[];
export type Valid = boolean;
export type Quality = ChannelQuality[];
export type SourceField = string | null;
/**
 * @minItems 1
 */
export type TimestampsS = [number, ...number[]];
export type Unit = string;
export type Values = (number | null)[];
export type WrapPeriod = number | null;
export type LogId = string;
export type JsonValue =
  | string
  | boolean
  | number
  | JsonValue[]
  | {
      [k: string]: JsonValue;
    }
  | null;
export type MappingSnapshotId = string;
export type RawFileRef = string;
export type RawFileSha256 = string;
export type RobotId = string;
/**
 * @minItems 1
 */
export type SampleTimesS = [number, ...number[]];
export type SchemaVersion = 1;
export type RelativeOriginS = number;
export type SourceOrigin = number;
export type Unit1 = "s";

export interface ReplayData {
  canonical_order: CanonicalOrder;
  channels: Channels;
  log_id: LogId;
  mapping_snapshot: MappingSnapshot;
  mapping_snapshot_id: MappingSnapshotId;
  metadata?: Metadata;
  raw_file_ref: RawFileRef;
  raw_file_sha256: RawFileSha256;
  robot_id: RobotId;
  sample_times_s: SampleTimesS;
  schema_version?: SchemaVersion;
  time_basis: TimeBasis;
}
export interface Channels {
  [k: string]: ChannelSeries;
}
export interface ChannelSeries {
  angle_semantics: AngleSemantics;
  quality: Quality;
  source_field?: SourceField;
  timestamps_s: TimestampsS;
  unit: Unit;
  values: Values;
  wrap_period?: WrapPeriod;
}
export interface ChannelQuality {
  method?: Method;
  origin: Origin;
  processing?: Processing;
  source_indices?: SourceIndices;
  valid: Valid;
}
export interface MappingSnapshot {
  [k: string]: JsonValue;
}
export interface Metadata {
  [k: string]: JsonValue;
}
export interface TimeBasis {
  relative_origin_s?: RelativeOriginS;
  source_origin: SourceOrigin;
  unit?: Unit1;
}
