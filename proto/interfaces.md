# T04 shared protocol interfaces

Package: `robot-replay-protocol`, import `replay_protocol`; Python 3.12+, Pydantic 2.14+. No app, FastAPI, NumPy or IsaacSim dependency.

All models extend `ContractModel` (unknown fields rejected, frozen assignments, finite JSON). Numeric types reject booleans. `SafeInt` is a nonnegative JS-safe integer; `FiniteNumber` accepts JSON integer/float values, not strings. Protocol versions are literal `1`.

Frame vectors, quality processing/source-index vectors and snapshot catalogs use immutable Python tuples while retaining JSON array schemas. `ReplayData` source arrays are collection models, not live rendered evidence. `replay_protocol.examples.examples()` returns independent JSON-native `{name: {schema, payload}}` fixtures for every public model, every RPC command/response stage and every worker event; fixtures are synthetic and are not P0 evidence.

- `ChannelQuality`: `origin` measured/commanded/derived/missing, `valid: bool`, `processing: list[original|unit_converted|interpolated|forward_filled|defaulted|derived]`, `source_indices: list[SafeInt]`, `method: str|null`.
- `ChassisPose`: `x/y/yaw: FiniteNumber`, `frame: 'world'`.
- `PositionRef`: `log_time_s >= 0`, `source_sample_index: SafeInt|null`, `target_seq: SafeInt`.
- `FrameIdentity`: `session_id/log_id/playback_epoch/control_seq/target_seq/capture_id/render_seq`; capture 1..uint32 max, render_seq equals capture_id.
- `PlaybackSnapshot`: `schema_version=1/session_id/log_id|null/playback_epoch/control_seq/state/speed/duration_s/target_position|null/applied_position|null/rendered_identity|null`; no presented field. States idle/ready/playing/paused/ended/error. Speed [0.1,10].
- `TargetFrame`: schema/session/log/epoch/control/target identity, `log_time_s/duration_s/progress/source_sample_index|null/joints[28]/quality[28]/chassis|null`; frozen target context `state: ready|playing|paused|ended` (default paused), `speed` (default 1, range 0.1..10); `mode='kinematic'`, reserved `joint_velocities=null/joint_efforts=null` (physics unsupported).
- `AppliedFrame`: TargetFrame fields plus `source_joints[28]/applied_wall_s`; joints are actual readback, source_joints are requested normalized log values.
- `RenderedFrame`: AppliedFrame plus `capture_id/render_seq/captured_wall_s/raw_identity_verified=true`.
- `FrameSnapshot`: schema/session/log/epoch/control/target/capture/render identity, `state/speed/log_time_s/duration_s/progress/source_sample_index|null/joints[28]/source_joints[28]/quality[28]/chassis|null/limit_flags/limit_warn_flags/captured_wall_s`.
- `PresentedFrame`: `schema_version=1/snapshot: FrameSnapshot/connection_id/connection_generation/presented_wall_s`; browser-local evidence, never RPC acknowledgement.
- `SnapshotCatalog`: `schema_version=1/session_id/identity_scheme='capture32_crc32'/marker_bits=64/marker_pixels[64][2]/snapshots[1..16]/encoder_poll_capture_id|null`; marker coordinates are unique nonnegative integers, snapshots have the same session and unique capture IDs. The decoder checks coordinates against actual VideoFrame dimensions. Encoder poll is diagnostic only.
- `ReplayData`: schema/log/robot, `raw_file_ref/raw_file_sha256/mapping_snapshot_id/mapping_snapshot`, `time_basis`, exact `canonical_order`, strictly increasing `sample_times_s`, `channels: dict[str,ChannelSeries]`, `metadata`. ChannelSeries has own timestamps, nullable source values, per-sample quality, source field/unit, angle semantics (bounded/angular_wrapped/angular_unwrapped/linear), nullable wrap period. Original samples remain distinct from replay grids; no resampler here.
- `RPCRequest`: schema/session/request_id/control_seq/playback_epoch/`command` discriminated by `cmd`: load_scene(scene_id), load_robot(robot_id), load_log(log_id), apply_keyframe(frame), pause_barrier(target_seq), reset, shutdown, heartbeat, subscribe_ready(channels).
- `RPCResponse`: schema/session/request_id/control_seq/playback_epoch/stage(accepted/applied/rendered)/ok/error|null/frame|null. Stage-specific frame types, error required when ok=false. Never presented.
- `WorkerEvent`: schema/session/seq/`event` discriminated by type: ready, health, applied, rendered, error.
- `TargetMessage`: `schema_version=1/type='target'/frame: TargetFrame`.

Public types: `PlaybackState`, `JsonValue`, `SafeInt`, `FiniteNumber`, `ChannelSeries`, `PositionRef`, `FrameIdentity`. Public schema registries: `PROTOCOL_MODELS`; adapters `RPC_ADAPTER`, `RPC_RESPONSE_ADAPTER`, `WORKER_EVENT_ADAPTER`, `TARGET_ADAPTER` and `PROTOCOL_ADAPTERS`.

Sequences are scoped by session/epoch; consumers enforce monotonic arrival and barriers. Models reject mismatched nested identities. Every capture ID is unique over its session lifetime and never reset on seek; this is a stateful producer invariant. Browser connection generations are local and independent of playback epochs. RPC stages describe observed server/worker evidence only.

T11 freezes state/speed when producing each target, propagates them through actual readback/render and validates receipts against that target. Never derive delayed-frame context from current server state. The optional input defaults support older producers, but older strict readers reject the added output fields: deploy backend, worker protocol and generated frontend contracts together.
