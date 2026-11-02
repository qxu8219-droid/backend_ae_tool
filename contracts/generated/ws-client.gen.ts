/* Generated from Python contracts. Do not edit. */

export type WsClientMessage = WSPing | WSSetTelemetryRate;
export type ConnectionId = string;
export type RequestId = string;
export type SchemaVersion = 1;
/**
 * Monotonic within this connection_id only; gaps require reconnect/snapshot recovery.
 */
export type Seq = number;
export type SessionId = string;
export type Type = "ping";
/**
 * UTC Unix seconds; system clock, not log time.
 */
export type WallTime = number;
export type ConnectionId1 = string;
export type Hz = 10 | 20 | 30;
export type RequestId1 = string;
export type SchemaVersion1 = 1;
/**
 * Monotonic within this connection_id only; gaps require reconnect/snapshot recovery.
 */
export type Seq1 = number;
export type SessionId1 = string;
export type Type1 = "set_telemetry_rate";
/**
 * UTC Unix seconds; system clock, not log time.
 */
export type WallTime1 = number;

export interface WSPing {
  connection_id: ConnectionId;
  data: PingData;
  schema_version?: SchemaVersion;
  seq: Seq;
  session_id: SessionId;
  type: Type;
  wall_time: WallTime;
}
export interface PingData {
  request_id: RequestId;
}
export interface WSSetTelemetryRate {
  connection_id: ConnectionId1;
  data: TelemetryRateData;
  schema_version?: SchemaVersion1;
  seq: Seq1;
  session_id: SessionId1;
  type: Type1;
  wall_time: WallTime1;
}
export interface TelemetryRateData {
  hz: Hz;
  request_id: RequestId1;
}
