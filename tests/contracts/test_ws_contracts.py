"""WebSocket message schemas and unavailable-runtime behavior."""
import copy
import importlib
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect


def s():
    return importlib.import_module("app.schemas")


def envelope(kind, data):
    return {"schema_version": 1, "type": kind, "session_id": "s1", "connection_id": "c1", "seq": 1, "wall_time": 1000.0, "data": data}


def playback():
    return {"session_id": "s1", "log_id": None, "playback_epoch": 0, "control_seq": 0, "state": "idle", "speed": 1, "duration_s": 0}


def joint_meta():
    p = importlib.import_module("replay_protocol")
    return {"robot_id": "r1", "canonical_order": list(p.CANONICAL_JOINTS), "limits_source": "assets/robots/r1pro_2026/isaacsim_6_x/reproduction/configs/r1pro_2026_common.json", "joints": [{"name": name, "type": "prismatic" if "finger" in name else ("continuous" if "wheel" in name else "revolute"), "unit": "m" if "finger" in name else "rad", "lower": None if "wheel" in name else -1, "upper": None if "wheel" in name else 1, "limits_provenance": "vendor URDF"} for name in p.CANONICAL_JOINTS]}


def snapshot():
    return {"session_id": "s1", "log_id": "l1", "playback_epoch": 1, "control_seq": 2, "target_seq": 3, "capture_id": 4, "render_seq": 4, "state": "paused", "speed": 1, "log_time_s": 0.5, "duration_s": 1, "progress": 0.5, "source_sample_index": 0, "joints": [0] * 28, "source_joints": [0] * 28, "quality": [{"origin": "measured", "valid": True, "processing": ["original"], "source_indices": [0]} for _ in range(28)], "chassis": None, "limit_flags": 0, "limit_warn_flags": 0, "captured_wall_s": 1000}


def samples():
    return [
        envelope("connection_snapshot", {"joint_meta": joint_meta(), "playback": playback(), "worker": {"phase": "booting"}, "video": {"transport": "whep", "publishing": False, "whep_url": None, "stream_path": None, "ice_servers": []}, "sync": {"state": "awaiting_pair", "playback_epoch": 0}}),
        envelope("system", {"event": "worker_phase_changed", "worker": {"phase": "loading_robot"}}),
        envelope("playback_state", {"playback": playback(), "reason": "loaded"}),
        envelope("frame_snapshot", snapshot()),
        envelope("alert", {"alert_id": "a1", "severity": "warning", "source": "limits", "code": "OVER_LIMIT", "message": "outside limit", "log_id": "l1", "playback_epoch": 1, "log_time_s": 0.5, "joint_index": 0}),
        envelope("health_telemetry", {"worker_alive": True, "render_fps": 30, "cpu_percent": 10, "memory_bytes": 1000}),
        envelope("pong", {"request_id": "heartbeat1"}),
        envelope("telemetry_rate_changed", {"request_id": "rate1", "hz": 20}),
    ]


def test_every_server_variant_roundtrips_as_strict_finite_json():
    for sample in samples():
        value = s().WS_SERVER_ADAPTER.validate_python(sample)
        dumped = json.loads(value.model_dump_json())
        assert dumped["type"] == sample["type"]
        json.dumps(dumped, allow_nan=False)
    assert s().WS_SERVER_ADAPTER.json_schema()["discriminator"]["propertyName"] == "type"


@pytest.mark.parametrize("kind,data", [("ping", {"request_id": "p1"}), ("set_telemetry_rate", {"request_id": "r1", "hz": 10}), ("set_telemetry_rate", {"request_id": "r1", "hz": 20}), ("set_telemetry_rate", {"request_id": "r1", "hz": 30})])
def test_client_messages_use_same_connection_envelope(kind, data):
    assert s().WS_CLIENT_ADAPTER.validate_python(envelope(kind, data)).type == kind


@pytest.mark.parametrize("change", [{"schema_version": 2}, {"schema_version": True}, {"seq": -1}, {"seq": True}, {"seq": 2**53}, {"wall_time": float("nan")}, {"unknown": 1}, {"session_id": "other"}])
def test_invalid_server_envelope_and_nested_session_are_rejected(change):
    sample = samples()[2]
    sample.update(change)
    with pytest.raises(ValidationError):
        s().WS_SERVER_ADAPTER.validate_python(sample)


def test_connection_snapshot_requires_complete_reconnection_state():
    sample = samples()[0]
    for field in ["joint_meta", "playback", "worker", "video", "sync"]:
        broken = copy.deepcopy(sample)
        del broken["data"][field]
        with pytest.raises(ValidationError):
            s().WS_SERVER_ADAPTER.validate_python(broken)
    broken = copy.deepcopy(sample)
    broken["data"]["latest_frame"] = snapshot()
    with pytest.raises(ValidationError):
        s().WS_SERVER_ADAPTER.validate_python(broken)


def test_sync_snapshot_reuses_shared_frame_contract_and_health_cannot_drive_panel():
    frame = s().WS_SERVER_ADAPTER.validate_python(samples()[3])
    assert isinstance(frame.data, importlib.import_module("replay_protocol").FrameSnapshot)
    bad = samples()[5]
    bad["data"]["joints"] = [1] * 28
    with pytest.raises(ValidationError):
        s().WS_SERVER_ADAPTER.validate_python(bad)
    bad = samples()[3]
    bad["data"]["joints"] = [0] * 27
    with pytest.raises(ValidationError):
        s().WS_SERVER_ADAPTER.validate_python(bad)


@pytest.mark.parametrize("hz", [0, 15, 60, True, "20"])
def test_ordinary_telemetry_rate_cannot_silently_change_sync_snapshot_contract(hz):
    with pytest.raises(ValidationError):
        s().WS_CLIENT_ADAPTER.validate_python(envelope("set_telemetry_rate", {"request_id": "r1", "hz": hz}))


def test_joint_meta_rejects_wrong_order_units_and_invalid_limits():
    for mutation in ["order", "unit", "limits"]:
        data = joint_meta()
        if mutation == "order":
            data["joints"][0], data["joints"][1] = data["joints"][1], data["joints"][0]
        elif mutation == "unit":
            data["joints"][0]["unit"] = "m"
        else:
            data["joints"][0]["lower"] = 2
        with pytest.raises(ValidationError):
            s().JointMetadata.model_validate(data)


def test_ws_unconfigured_runtime_closes_with_retry_later():
    client = TestClient(importlib.import_module("app.main").app)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/ws/sessions/s1"):
            pytest.fail("contract-only service must not pretend a live connection exists")
    assert caught.value.code == 1013


def test_exportable_examples_cover_every_ws_discriminator_branch():
    examples = importlib.import_module("app.schemas.examples").examples()
    seen = {"ws-server": set(), "ws-client": set()}
    for entry in examples.values():
        json.dumps(entry["payload"], allow_nan=False)
        if entry["schema"] in seen:
            adapter = s().WS_SERVER_ADAPTER if entry["schema"] == "ws-server" else s().WS_CLIENT_ADAPTER
            result = adapter.validate_python(entry["payload"])
            seen[entry["schema"]].add(result.type)
        else:
            s().REST_MODELS[entry["schema"]].model_validate(entry["payload"])
    assert seen["ws-server"] == {"connection_snapshot", "system", "playback_state", "frame_snapshot", "alert", "health_telemetry", "pong", "telemetry_rate_changed"}
    assert seen["ws-client"] == {"ping", "set_telemetry_rate"}


@pytest.mark.parametrize("change", [{"log_id": "wrong-log"}, {"control_seq": 999}])
def test_connection_render_identity_cannot_escape_current_log_or_control_barrier(change):
    data = samples()[0]["data"]
    data["playback"] = dict(playback(), log_id="l1", playback_epoch=1, control_seq=2, duration_s=1)
    identity = {"session_id": "s1", "log_id": "l1", "playback_epoch": 1, "control_seq": 2, "target_seq": 3, "capture_id": 4, "render_seq": 4}
    identity.update(change)
    data["sync"].update(playback_epoch=1, last_rendered_identity=identity)
    with pytest.raises(ValidationError):
        s().ConnectionSnapshotData.model_validate(data)


@pytest.mark.parametrize("remove", [("log_id",), ("playback_epoch",), ("log_time_s",), ("log_id", "playback_epoch", "log_time_s")])
def test_positioned_alert_requires_complete_log_epoch_time_identity(remove):
    data = samples()[4]["data"]
    for key in remove:
        del data[key]
    with pytest.raises(ValidationError):
        s().AlertData.model_validate(data)


def test_system_alert_can_omit_all_log_position_fields():
    data = {"alert_id": "a2", "severity": "error", "source": "worker", "code": "WORKER_EXITED", "message": "Worker stopped"}
    assert s().AlertData.model_validate(data).log_id is None


def test_exported_discriminators_are_required_just_like_wire_validation():
    for adapter in (s().WS_SERVER_ADAPTER,s().WS_CLIENT_ADAPTER):
        schema=adapter.json_schema()
        for variant in schema['oneOf']:
            branch=schema['$defs'][variant['$ref'].split('/')[-1]]
            assert 'type' in branch['required']
