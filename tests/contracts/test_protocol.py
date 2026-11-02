"""Protocol behavior: reject malformed and ambiguous evidence at boundaries."""
import copy
import importlib
import json

import pytest
from pydantic import ValidationError, TypeAdapter


def protocol():
    return importlib.import_module("replay_protocol")


def quality():
    return {"origin": "measured", "valid": True, "processing": ["original"], "source_indices": [0]}


def target():
    return {"session_id": "s1", "log_id": "l1", "playback_epoch": 2,
            "control_seq": 3, "target_seq": 4, "log_time_s": 0.5,
            "duration_s": 1.0, "progress": 0.5, "source_sample_index": 0,
            "joints": [0.0] * 28, "quality": [quality() for _ in range(28)],
            "chassis": {"x": 1.0, "y": 2.0, "yaw": 0.0}}


def snapshot():
    return dict(target(), capture_id=5, render_seq=5, state="playing", speed=1.0,
                source_joints=[0.0] * 28, limit_flags=0, limit_warn_flags=0,
                captured_wall_s=1_000.0)


def replay():
    p = protocol()
    return {"log_id": "l1", "robot_id": "r1", "raw_file_ref": "uploads/source.csv",
            "raw_file_sha256": "a" * 64, "mapping_snapshot_id": "map1",
            "mapping_snapshot": {"joints": {"scale": 1.0}},
            "time_basis": {"unit": "s", "source_origin": 123.0, "relative_origin_s": 0.0},
            "canonical_order": list(p.CANONICAL_JOINTS), "sample_times_s": [0.0, 1.0],
            "channels": {"wheel_motor_joint1": {"timestamps_s": [0.0, 1.0],
                "values": [0.0, 7.0], "quality": [quality(), quality()],
                "source_field": "wheel", "unit": "rad", "angle_semantics": "angular_unwrapped"}}}


def test_protocol_import_and_valid_roundtrip():
    p = protocol()
    frame = p.FrameSnapshot.model_validate(snapshot())
    assert frame.capture_id == frame.render_seq == 5
    assert p.FrameSnapshot.model_validate_json(frame.model_dump_json()) == frame
    assert len(p.CANONICAL_JOINTS) == len(set(p.CANONICAL_JOINTS)) == 28


def test_chassis_quality_roundtrip_preserves_axis_provenance_and_legacy_unknown():
    p = protocol()
    data = snapshot()
    qualities = [dict(quality(), origin="derived", processing=["derived"],
                      method=f"body_twist.{axis}.v1") for axis in ("x", "y", "yaw")]
    data["chassis"]["quality"] = qualities
    frame = p.FrameSnapshot.model_validate(data)
    assert tuple(q.method for q in frame.chassis.quality) == (
        "body_twist.x.v1", "body_twist.y.v1", "body_twist.yaw.v1")
    assert p.FrameSnapshot.model_validate_json(frame.model_dump_json()) == frame
    with pytest.raises(TypeError):
        frame.chassis.quality[0] = p.ChannelQuality(**quality())
    assert p.FrameSnapshot.model_validate(snapshot()).chassis.quality is None


@pytest.mark.parametrize("count", [0, 1, 2, 4])
def test_chassis_quality_requires_exactly_three_axes_when_present(count):
    data = snapshot()
    data["chassis"]["quality"] = [quality()] * count
    with pytest.raises(ValidationError):
        protocol().FrameSnapshot.model_validate(data)


def test_render_identity_verification_cannot_be_invented_by_a_default():
    p=protocol()
    data=dict(target(),source_joints=[0.0]*28,applied_wall_s=999.0,
              capture_id=5,render_seq=5,captured_wall_s=1000.0)
    with pytest.raises(ValidationError):p.RenderedFrame.model_validate(data)
    response={'session_id':'s1','request_id':'r1','control_seq':3,'playback_epoch':2,
              'stage':'rendered','ok':True,'frame':data}
    with pytest.raises(ValidationError):p.RPCResponse.model_validate(response)
    assert p.RenderedFrame.model_validate(dict(data,raw_identity_verified=True)).raw_identity_verified is True


@pytest.mark.parametrize("value", [True, False, "1", float("nan"), float("inf"), -float("inf")])
def test_numbers_refuse_booleans_strings_and_nonfinite(value):
    with pytest.raises(ValidationError):
        TypeAdapter(protocol().FiniteNumber).validate_python(value)


@pytest.mark.parametrize("value", [True, -1, 1.5, 2**53, "1"])
def test_safe_integers_enforce_js_precision(value):
    with pytest.raises(ValidationError):
        TypeAdapter(protocol().SafeInt).validate_python(value)


@pytest.mark.parametrize("field,value", [
    ("joints", [0.0] * 27), ("source_joints", [0.0] * 29), ("quality", [quality()] * 27),
    ("schema_version", 2), ("schema_version", True), ("capture_id", 0),
    ("capture_id", 2**32), ("render_seq", 6), ("limit_flags", 2**28),
    ("limit_warn_flags", True), ("progress", 0.75), ("duration_s", -1),
    ("speed", 10.01), ("source_sample_index", True), ("log_time_s", 2),
])
def test_snapshot_rejects_malformed_identity_and_values(field, value):
    data = snapshot()
    data[field] = value
    with pytest.raises(ValidationError):
        protocol().FrameSnapshot.model_validate(data)


def test_unknown_fields_and_nonfinite_nested_quality_are_rejected():
    data = snapshot()
    data["presented"] = True
    with pytest.raises(ValidationError):
        protocol().FrameSnapshot.model_validate(data)
    data = snapshot()
    data["joints"][4] = float("nan")
    with pytest.raises(ValidationError):
        protocol().FrameSnapshot.model_validate(data)


def test_catalog_requires_unique_same_session_snapshots():
    p = protocol()
    data = {"session_id": "s1", "snapshots": [snapshot()], 'marker_pixels':[[i,0] for i in range(64)]}
    assert len(p.SnapshotCatalog.model_validate(data).snapshots) == 1
    for bad in [dict(data, snapshots=[]), dict(data, snapshots=[snapshot()] * 17),
                dict(data, snapshots=[snapshot(), snapshot()]), dict(data, session_id="s2")]:
        with pytest.raises(ValidationError):
            p.SnapshotCatalog.model_validate(bad)


def test_catalog_diagnostic_poll_does_not_choose_snapshot():
    result = protocol().SnapshotCatalog.model_validate(
        {"session_id": "s1", "snapshots": [snapshot()], "encoder_poll_capture_id": 99,
         'marker_pixels':[[i,0] for i in range(64)]})
    assert result.snapshots[0].capture_id == 5


def test_catalog_carries_unambiguous_image_marker_geometry():
    p=protocol()
    base={'session_id':'s1','snapshots':[snapshot()]}
    with pytest.raises(ValidationError):p.SnapshotCatalog.model_validate(base)
    for pixels in [[[0,0]]*64,[[i,0] for i in range(63)],[[-1,i] for i in range(64)]]:
        with pytest.raises(ValidationError):p.SnapshotCatalog.model_validate(dict(base,marker_pixels=pixels))
    catalog=p.SnapshotCatalog.model_validate(dict(base,marker_pixels=[[i,0] for i in range(64)]))
    assert catalog.marker_bits==64 and len(catalog.marker_pixels)==64


def test_playback_snapshot_cannot_claim_browser_presentation():
    p = protocol()
    data = {"session_id": "s1", "log_id": "l1", "playback_epoch": 2,
            "control_seq": 3, "state": "playing", "speed": 1.0, "duration_s": 1.0,
            "target_position": {"log_time_s": 0.5, "source_sample_index": 0, "target_seq": 4}}
    assert p.PlaybackSnapshot.model_validate(data).applied_position is None
    data["presented_position"] = data["target_position"]
    with pytest.raises(ValidationError):
        p.PlaybackSnapshot.model_validate(data)


def test_replay_preserves_source_samples_and_multiturn_semantics():
    p = protocol()
    data = p.ReplayData.model_validate(replay())
    assert data.channels["wheel_motor_joint1"].values == [0.0, 7.0]
    assert data.channels["wheel_motor_joint1"].angle_semantics == "angular_unwrapped"


@pytest.mark.parametrize("times", [[0, 0], [1, 0], [], [0, float("nan")]])
def test_replay_rejects_ambiguous_normalized_time_axis(times):
    data = replay()
    data["sample_times_s"] = times
    with pytest.raises(ValidationError):
        protocol().ReplayData.model_validate(data)


def test_replay_rejects_nonfinite_free_json_and_bad_channel_lengths():
    data = replay()
    data["mapping_snapshot"]["nested"] = [{"bad": float("inf")}]
    with pytest.raises(ValidationError):
        protocol().ReplayData.model_validate(data)
    data = replay()
    data["channels"]["wheel_motor_joint1"]["quality"] = []
    with pytest.raises(ValidationError):
        protocol().ReplayData.model_validate(data)


def test_source_quality_cannot_claim_missing_or_filled_as_original_measurement():
    p = protocol()
    for bad in [dict(quality(), origin="missing", valid=True),
                dict(quality(), origin="derived", method=None),
                dict(quality(), processing=["original", "defaulted"])]:
        with pytest.raises(ValidationError):
            p.ChannelQuality.model_validate(bad)


def test_rpc_discriminated_commands_and_stage_specific_evidence():
    p = protocol()
    request = {"session_id": "s1", "request_id": "request1", "control_seq": 3,
               "playback_epoch": 2, "command": {"cmd": "apply_keyframe", "frame": target()}}
    assert p.RPC_ADAPTER.validate_python(request).command.frame.target_seq == 4
    bad = copy.deepcopy(request)
    bad["command"]["frame"]["session_id"] = "other"
    with pytest.raises(ValidationError):
        p.RPCRequest.model_validate(bad)
    response = {"session_id": "s1", "request_id": "request1", "control_seq": 3,
                "playback_epoch": 2, "stage": "accepted", "ok": True}
    assert p.RPCResponse.model_validate(response).frame is None
    for changes in [{"stage": "presented"}, {"stage": "applied"}, {"ok": False}, {"schema_version": 9}]:
        with pytest.raises(ValidationError):
            p.RPCResponse.model_validate(dict(response, **changes))


def test_physics_and_nonfinite_targets_are_not_silently_enabled():
    p = protocol()
    assert p.TargetMessage.model_validate({"frame": target()}).frame.mode == "kinematic"
    for changes in [{"mode": "physics"}, {"joint_velocities": [0] * 28}]:
        with pytest.raises(ValidationError):
            p.TargetFrame.model_validate(dict(target(), **changes))


def test_every_public_schema_is_json_exportable_without_scientific_runtime():
    p = protocol()
    for name, model in p.PROTOCOL_MODELS.items():
        encoded = json.dumps(model.model_json_schema(), allow_nan=False)
        assert encoded and name
    for name, adapter in p.PROTOCOL_ADAPTERS.items():
        assert json.dumps(adapter.json_schema(), allow_nan=False) and name


def test_frame_evidence_is_deeply_immutable():
    frame = protocol().FrameSnapshot.model_validate(snapshot())
    with pytest.raises(TypeError):
        frame.joints[0] = 12.0
    with pytest.raises(TypeError):
        frame.quality[0].processing[0] = "derived"
    with pytest.raises(TypeError):
        frame.quality[0].source_indices[0] = 8
    catalog = protocol().SnapshotCatalog.model_validate({"session_id": "s1", "snapshots": [snapshot()],
        'marker_pixels':[[i,0] for i in range(64)]})
    with pytest.raises(TypeError):
        catalog.snapshots[0] = frame


def test_applied_and_rendered_responses_require_real_matching_evidence():
    p = protocol()
    applied = dict(target(), source_joints=[0.0] * 28, applied_wall_s=1000.0)
    rendered = dict(applied, capture_id=5, render_seq=5, captured_wall_s=1000.1,
                    raw_identity_verified=True)
    base = {"session_id": "s1", "request_id": "request1", "control_seq": 3,
            "playback_epoch": 2, "ok": True}
    assert p.RPCResponse.model_validate(dict(base, stage="applied", frame=applied)).frame.joints[0] == 0.0
    assert p.RPCResponse.model_validate(dict(base, stage="rendered", frame=rendered)).frame.capture_id == 5
    for change in [{"captured_wall_s": 999.0}, {"raw_identity_verified": False},
                   {"raw_identity_verified": 1}, {"render_seq": 6}]:
        with pytest.raises(ValidationError):
            p.RenderedFrame.model_validate(dict(rendered, **change))
    for change in [{"control_seq": 2}, {"playback_epoch": 1}, {"session_id": "old"}]:
        with pytest.raises(ValidationError):
            p.RPCResponse.model_validate(dict(base, stage="rendered", frame=dict(rendered, **change)))


@pytest.mark.parametrize("command", [
    {"cmd": "load_scene", "scene_id": "empty"}, {"cmd": "load_robot", "robot_id": "r1"},
    {"cmd": "load_log", "log_id": "l1"}, {"cmd": "pause_barrier", "target_seq": 4},
    {"cmd": "reset"}, {"cmd": "shutdown"}, {"cmd": "heartbeat"},
    {"cmd": "subscribe_ready", "channels": ["target", "telemetry"]},
])
def test_all_ipc_commands_have_discriminated_payloads(command):
    request = {"session_id": "s1", "request_id": "request1", "control_seq": 3,
               "playback_epoch": 2, "command": command}
    assert protocol().RPC_ADAPTER.validate_python(request).command.cmd == command["cmd"]


def test_worker_events_reject_cross_session_frame_and_unknown_discriminator():
    p = protocol()
    applied = dict(target(), source_joints=[0.0] * 28, applied_wall_s=1000.0)
    valid = {"session_id": "s1", "seq": 2, "event": {"type": "applied", "frame": applied}}
    assert p.WORKER_EVENT_ADAPTER.validate_python(valid).event.frame.target_seq == 4
    with pytest.raises(ValidationError):
        p.WorkerEvent.model_validate(dict(valid, session_id="other"))
    with pytest.raises(ValidationError):
        p.WorkerEvent.model_validate(dict(valid, event={"type": "presented"}))


def test_wrapped_source_needs_explicit_period_and_preserves_missing_values():
    p = protocol()
    series = {"timestamps_s": [0.0, 1.0], "values": [None, 3.0],
              "quality": [{"origin": "missing", "valid": False}, quality()],
              "unit": "rad", "angle_semantics": "angular_wrapped"}
    with pytest.raises(ValidationError):
        p.ChannelSeries.model_validate(series)
    result = p.ChannelSeries.model_validate(dict(series, wrap_period=6.283185307179586))
    assert result.values[0] is None
    bad = copy.deepcopy(series)
    bad["quality"][0] = quality()
    with pytest.raises(ValidationError):
        p.ChannelSeries.model_validate(dict(bad, wrap_period=6.283185307179586))


def test_public_examples_cover_ipc_variants_and_validate_as_json():
    p = protocol()
    example_module = importlib.import_module("replay_protocol.examples")
    fixtures = example_module.examples()
    commands, stages, events = set(), set(), set()
    for value in fixtures.values():
        schema, payload = value["schema"], value["payload"]
        encoded = json.dumps(payload, allow_nan=False)
        if schema in p.PROTOCOL_MODELS:
            p.PROTOCOL_MODELS[schema].model_validate_json(encoded)
        else:
            p.PROTOCOL_ADAPTERS[schema].validate_json(encoded)
        if schema == "RPCRequest":
            commands.add(payload["command"]["cmd"])
        if schema == "RPCResponse":
            stages.add(payload["stage"])
        if schema == "WorkerEvent":
            events.add(payload["event"]["type"])
    assert commands == {"load_scene", "load_robot", "load_log", "apply_keyframe", "pause_barrier",
                        "reset", "shutdown", "heartbeat", "subscribe_ready"}
    assert stages == {"accepted", "applied", "rendered"}
    assert events == {"ready", "health", "applied", "rendered", "error"}
    replay_example = next(v["payload"] for v in fixtures.values() if v["schema"] == "ReplayData")
    assert replay_example["channels"]["wheel_motor_joint1"]["values"][1] > 6.283185307179586
    assert any(q["origin"] == "missing" and not q["valid"]
               for c in replay_example["channels"].values() for q in c["quality"])
