"""Synthetic, JSON-native fixtures for contract consumers and generated docs.

These are protocol examples, not evidence that a worker or renderer has run.
"""
from copy import deepcopy

from .models import CANONICAL_JOINTS


def examples() -> dict[str, dict]:
    measured = {"origin": "measured", "valid": True, "processing": ["original"],
                "source_indices": [0], "method": None}
    missing = {"origin": "missing", "valid": False, "processing": [],
               "source_indices": [1], "method": None}
    chassis = {"x": 1.25, "y": -0.5, "yaw": 0.2, "frame": "world"}
    target = {
        "schema_version": 1, "session_id": "example-session", "log_id": "example-log",
        "playback_epoch": 2, "control_seq": 3, "target_seq": 7,
        "log_time_s": 1.0, "duration_s": 2.0, "progress": 0.5,
        "source_sample_index": 1, "joints": [0.0] * 28,
        "quality": [deepcopy(measured) for _ in range(28)], "chassis": chassis,
        "mode": "kinematic", "joint_velocities": None, "joint_efforts": None,
    }
    target["joints"][1] = 7.25  # Cumulative wheel angle is intentionally greater than 2 pi.
    target["quality"][1]["source_indices"] = [1]
    applied = dict(target, source_joints=list(target["joints"]), applied_wall_s=1_750_000_000.0)
    rendered = dict(applied, capture_id=10, render_seq=10,
                    captured_wall_s=1_750_000_000.01, raw_identity_verified=True)
    snapshot = {key: deepcopy(value) for key, value in rendered.items()
                if key not in {"mode", "joint_velocities", "joint_efforts", "applied_wall_s", "raw_identity_verified"}}
    snapshot.update(state="playing", speed=1.0, limit_flags=0, limit_warn_flags=0)
    frame_identity = {key: snapshot[key] for key in (
        "schema_version", "session_id", "log_id", "playback_epoch", "control_seq",
        "target_seq", "capture_id", "render_seq",
    )}
    position = {"log_time_s": 1.0, "source_sample_index": 1, "target_seq": 7}
    playback = {"schema_version": 1, "session_id": "example-session", "log_id": "example-log",
                "playback_epoch": 2, "control_seq": 3, "state": "playing", "speed": 1.0,
                "duration_s": 2.0, "target_position": position,
                "applied_position": deepcopy(position), "rendered_identity": frame_identity}
    catalog = {"schema_version": 1, "session_id": "example-session",
               "identity_scheme": "capture32_crc32", "snapshots": [snapshot],
               "marker_pixels": [[32+32*(i%32),32+32*(i//32)] for i in range(64)],
               "encoder_poll_capture_id": 11}
    time_basis = {"unit": "s", "source_origin": 42.0, "relative_origin_s": 0.0}
    wheel_series = {"timestamps_s": [0.0, 1.0, 2.0], "values": [0.0, 7.25, 14.5],
                    "quality": [dict(measured, source_indices=[index]) for index in range(3)],
                    "source_field": "wheel_angle", "unit": "rad",
                    "angle_semantics": "angular_unwrapped", "wrap_period": None}
    arm_series = {"timestamps_s": [0.0, 1.0, 2.0], "values": [0.1, None, 0.3],
                  "quality": [measured, missing, dict(measured, source_indices=[2])],
                  "source_field": "left_arm_1", "unit": "rad", "angle_semantics": "bounded",
                  "wrap_period": None}
    replay = {"schema_version": 1, "log_id": "example-log", "robot_id": "r1pro_2026",
              "raw_file_ref": "uploads/example-session/synthetic.csv", "raw_file_sha256": "a" * 64,
              "mapping_snapshot_id": "example-mapping-v1",
              "mapping_snapshot": {"timestamp": {"unit": "s"}, "joints": {
                  "wheel_motor_joint1": {"field": "wheel_angle", "angle_semantics": "angular_unwrapped"},
                  "left_arm_joint1": {"field": "left_arm_1", "angle_semantics": "bounded"}}},
              "time_basis": time_basis, "canonical_order": list(CANONICAL_JOINTS),
              "sample_times_s": [0.0, 1.0, 2.0],
              "channels": {"wheel_motor_joint1": wheel_series, "left_arm_joint1": arm_series},
              "metadata": {"synthetic": True, "issues": [{"code": "MISSING_SAMPLE", "line": 3}]}}
    error = {"code": "WORKER_UNAVAILABLE", "message": "Synthetic worker failure example", "details": {}}
    result = {}

    def add(name, schema, payload):
        result[name] = {"schema": schema, "payload": deepcopy(payload)}

    for schema, payload in (
        ("ChannelQuality", measured), ("ChassisPose", chassis), ("PositionRef", position),
        ("FrameIdentity", frame_identity), ("PlaybackSnapshot", playback), ("TargetFrame", target),
        ("AppliedFrame", applied), ("RenderedFrame", rendered), ("FrameSnapshot", snapshot),
        ("SnapshotCatalog", catalog), ("ChannelSeries", wheel_series), ("TimeBasis", time_basis),
        ("ReplayData", replay), ("ProtocolError", error),
        ("TargetMessage", {"schema_version": 1, "type": "target", "frame": target}),
        ("PresentedFrame", {"schema_version": 1, "snapshot": snapshot,
                            "connection_id": "browser-connection-2", "connection_generation": 2,
                            "presented_wall_s": 1_750_000_000.11}),
    ):
        add(schema, schema, payload)
    add("missing-quality", "ChannelQuality", missing)

    rpc_base = {"schema_version": 1, "session_id": "example-session", "request_id": "example-request",
                "control_seq": 3, "playback_epoch": 2}
    for command in (
        {"cmd": "load_scene", "scene_id": "empty-ground"},
        {"cmd": "load_robot", "robot_id": "r1pro_2026"},
        {"cmd": "load_log", "log_id": "example-log"},
        {"cmd": "apply_keyframe", "frame": target},
        {"cmd": "pause_barrier", "target_seq": 7},
        {"cmd": "reset"}, {"cmd": "shutdown"}, {"cmd": "heartbeat"},
        {"cmd": "subscribe_ready", "channels": ["target", "telemetry"]},
    ):
        add(f"rpc-{command['cmd']}", "RPCRequest", dict(rpc_base, command=command))
    for stage, evidence in (("accepted", None), ("applied", applied), ("rendered", rendered)):
        add(f"rpc-response-{stage}", "RPCResponse",
            dict(rpc_base, stage=stage, ok=True, frame=evidence, error=None))
    add("rpc-response-error", "RPCResponse", dict(rpc_base, stage="accepted", ok=False, error=error, frame=None))
    for index, event in enumerate((
        {"type": "ready", "isaacsim_version": "6.2.0", "physics_engine": "physx"},
        {"type": "health", "render_fps": 20.0, "wall_time_s": 1_750_000_000.0},
        {"type": "applied", "frame": applied}, {"type": "rendered", "frame": rendered},
        {"type": "error", "error": error},
    ), start=1):
        add(f"worker-{event['type']}", "WorkerEvent",
            {"schema_version": 1, "session_id": "example-session", "seq": index, "event": event})
    return result
