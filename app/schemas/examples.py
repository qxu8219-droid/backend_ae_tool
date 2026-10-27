"""Deterministic contract examples, not data returned by the unconfigured service.

Joint limits below are copied from the versioned common config. Synthetic log
samples are explicitly examples and never represent uploaded or measured runs.
"""
from replay_protocol import CANONICAL_JOINTS

# (type, lower, upper), in CANONICAL_JOINTS order; SI units, vendor-URDF bounds.
_LIMITS = [
    ("revolute", -3.1416, 3.1416), ("continuous", None, None),
    ("revolute", -3.1416, 3.1416), ("continuous", None, None),
    ("revolute", -3.1416, 3.1416), ("continuous", None, None),
    ("revolute", -1.1170467075, 1.8151467075),
    ("revolute", -2.7750467075, 2.5132467075),
    ("revolute", -1.8151467075, 1.5533467075),
    ("revolute", -3.0368467075, 3.0368467075),
    ("revolute", -4.4331467075, 1.2915467075),
    ("revolute", -0.1570467075, 3.1241467075),
    ("revolute", -2.3387427075, 2.3387427075),
    ("revolute", -2.0769467075, 0.3316467075),
    ("revolute", -2.3387427075, 2.3387427075),
    ("revolute", -1.0297447075, 1.0297447075),
    ("revolute", -1.5533467075, 1.5533467075),
    ("prismatic", 0.0, 0.05), ("prismatic", -0.05, 0.0),
    ("revolute", -4.4331467075, 1.2915467075),
    ("revolute", -3.1241467075, 0.1570467075),
    ("revolute", -2.3387427075, 2.3387427075),
    ("revolute", -2.0769467075, 0.3316467075),
    ("revolute", -2.3387427075, 2.3387427075),
    ("revolute", -1.0297447075, 1.0297447075),
    ("revolute", -1.5533467075, 1.5533467075),
    ("prismatic", 0.0, 0.05), ("prismatic", -0.05, 0.0),
]


def examples():
    """Return fresh JSON-compatible samples for schema export and downstream mocks."""
    quality = {"origin": "measured", "valid": True, "processing": ["original"], "source_indices": [0], "method": None}
    playback = {"schema_version": 1, "session_id": "example-session", "log_id": "example-log", "playback_epoch": 1, "control_seq": 2, "state": "paused", "speed": 1.0, "duration_s": 1.0, "target_position": {"log_time_s": 0.5, "source_sample_index": 0, "target_seq": 3}}
    frame = {"schema_version": 1, "session_id": "example-session", "log_id": "example-log", "playback_epoch": 1, "control_seq": 2, "target_seq": 3, "capture_id": 4, "render_seq": 4, "state": "paused", "speed": 1.0, "log_time_s": 0.5, "duration_s": 1.0, "progress": 0.5, "source_sample_index": 0, "joints": [0.0] * 28, "source_joints": [0.0] * 28, "quality": [dict(quality) for _ in range(28)], "chassis": {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "world"}, "limit_flags": 0, "limit_warn_flags": 0, "captured_wall_s": 1000.0}
    joint_meta = {"robot_id": "r1pro_2026", "canonical_order": list(CANONICAL_JOINTS), "limits_source": "assets/robots/r1pro_2026/isaacsim_6_x/reproduction/configs/r1pro_2026_common.json", "joints": [{"name": name, "type": kind, "unit": "m" if kind == "prismatic" else "rad", "lower": lower, "upper": upper, "limits_provenance": "simulation assumption (wheel missing URDF ratings)" if kind == "continuous" else "vendor URDF"} for name, (kind, lower, upper) in zip(CANONICAL_JOINTS, _LIMITS, strict=True)]}
    video = {"transport": "whep", "publishing": True, "whep_url": "http://localhost:8889/example-session/whep", "stream_path": "example-session", "ice_servers": [], "identity_scheme": "capture32_crc32", "snapshot_transport": "h264_sei_snapshot_catalog", "presentation": "verified_pair_single_canvas", "render_fps": 30.0,
             "sei_uuid_hex": "83249d41b6545e6e8d7779430f88bb0d"}
    worker = {"phase": "ready", "pid": 12345, "restart_policy": "recreate_session_reload_from_start"}
    server_data = {
        "connection_snapshot": {"joint_meta": joint_meta, "playback": playback, "worker": worker, "video": video, "sync": {"state": "awaiting_pair", "playback_epoch": 1}, "telemetry_hz": 20},
        "system": {"event": "worker_phase_changed", "worker": worker},
        "playback_state": {"playback": playback, "reason": "pause", "request_id": "example-control"},
        "frame_snapshot": frame,
        "alert": {"alert_id": "example-alert", "severity": "warning", "source": "limits", "code": "NEAR_LIMIT", "message": "Example joint approaches its bound", "log_id": "example-log", "playback_epoch": 1, "log_time_s": 0.5, "joint_index": 0},
        "health_telemetry": {"worker_alive": True, "render_fps": 30.0, "cpu_percent": 12.0, "memory_bytes": 1048576},
        "pong": {"request_id": "example-ping"},
        "telemetry_rate_changed": {"request_id": "example-rate", "hz": 20},
    }
    result = {}
    for seq, (kind, data) in enumerate(server_data.items(), start=1):
        result["ws_" + kind] = {"schema": "ws-server", "payload": {"schema_version": 1, "type": kind, "session_id": "example-session", "connection_id": "example-connection", "seq": seq, "wall_time": 1000.0 + seq / 10, "data": data}}
    for seq, (kind, data) in enumerate({"ping": {"request_id": "example-ping"}, "set_telemetry_rate": {"request_id": "example-rate", "hz": 20}}.items(), start=1):
        result["ws_" + kind] = {"schema": "ws-client", "payload": {"schema_version": 1, "type": kind, "session_id": "example-session", "connection_id": "example-connection", "seq": seq, "wall_time": 1000.0 + seq / 10, "data": data}}
    result["create_session"] = {"schema": "CreateSessionRequest", "payload": {"scene_id": "empty", "robot_id": "r1pro_2026", "isaacsim_version": "6.2.0", "mode": "kinematic", "physics_engine": "physx"}}
    result["playback_ack"] = {"schema": "RESTPlaybackResponse", "payload": {"request_id": "example-control", "control_seq": 2, "playback_epoch": 1, "ack_stage": "accepted", "playback": playback}}
    result["joint_metadata"] = {"schema": "JointMetadata", "payload": joint_meta}
    result["upload_result"] = {"schema": "UploadResult", "payload": {"log_id": "example-log", "parser": "csv_v1", "stats": {"sample_count": 2, "duration_s": 1.0, "nominal_rate_hz": 1.0, "joints_mapped": [CANONICAL_JOINTS[0]], "joints_missing": list(CANONICAL_JOINTS[1:]), "chassis": {"available": False}}, "issues": [{"severity": "warning", "code": "MISSING_JOINT", "line": None, "message": "Example has only one measured joint channel"}], "mapping_snapshot_id": "example-mapping", "mapping_snapshot": {"schema_version": 1, "timestamp": {"field": "time", "unit": "s"}, "joints": {"unit": "rad", "map": {CANONICAL_JOINTS[0]: "steer1"}}}, "raw_file_ref": "uploads/example-log/source.csv", "raw_file_sha256": "a" * 64, "channels": [{"name": CANONICAL_JOINTS[0], "label": "Steer 1", "kind": "joint", "unit": "rad", "source_field": "steer1", "joint_index": 0, "angle_semantics": "bounded", "quality": quality}]}}
    result["log_annotations"] = {"schema": "LogAnnotations", "payload": {
        "log_id": "example-log", "source_id": "example-source", "source_sha256": "a" * 64,
        "alignment": "source_frames", "provisional": True, "assumptions": ["Example provisional calibration"],
        "reference": {}, "segments": [{"id": "example-stage", "start_s": 0., "end_s": 1.,
            "start_frame": 0, "end_frame": 2, "label": "Example stage", "review_status": "pending_review",
            "boundary_status": "pending_review", "training_approved": False, "details": {}}],
        "events": [{"id": "example-event", "time_s": None, "kind": "request", "message": "Historical diagnostic", "details": {}}]}}
    return result
