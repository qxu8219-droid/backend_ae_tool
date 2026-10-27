"""Public REST contract tests; no worker, media service, GPU, or sample log required."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


@pytest.fixture
def client():
    return TestClient(importlib.import_module("app.main").app)


def schemas():
    return importlib.import_module("app.schemas")


ROUTES = [
    ("get", "/scenes", None, 200),
    ("get", "/robots", None, 200),
    ("get", "/robots/r1/joints", None, 200),
    ("post", "/sessions", {"scene_id": "scene1", "robot_id": "r1", "isaacsim_version": "6.2.0"}, 201),
    ("get", "/sessions/s1", None, 200),
    ("delete", "/sessions/s1", None, 204),
    ("post", "/sessions/s1/logs", "upload", 201),
    ("get", "/sessions/s1/logs/l1/channels", None, 200),
    ("get", "/sessions/s1/logs/l1/annotations", None, 200),
    ("get", "/sessions/s1/logs/l1/curve?channels=joint1", None, 200),
    ("post", "/sessions/s1/playback/load", {"log_id": "l1"}, 200),
    ("post", "/sessions/s1/playback/play", {}, 200),
    ("post", "/sessions/s1/playback/pause", {}, 200),
    ("post", "/sessions/s1/playback/stop", {}, 200),
    ("post", "/sessions/s1/playback/seek", {"time_s": -1.5}, 200),
    ("post", "/sessions/s1/playback/step", {"direction": "backward"}, 200),
    ("post", "/sessions/s1/playback/speed", {"speed": 1.5}, 200),
    ("get", "/sessions/s1/playback", None, 200),
    ("get", "/sessions/s1/video", None, 200),
]


@pytest.mark.parametrize("method,path,body,status", ROUTES)
def test_valid_routes_report_unconfigured_instead_of_faking_success(client, method, path, body, status):
    kwargs = {"files": {"file": ("log.csv", b"time,joint\n0,1\n", "text/csv")}} if body == "upload" else ({"json": body} if body is not None else {})
    response = client.request(method, "/api/v1" + path, **kwargs)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "BACKEND_NOT_CONFIGURED"
    schemas().ErrorEnvelope.model_validate(response.json())


def test_openapi_declares_all_routes_and_error_meanings(client):
    schema = client.get("/openapi.json").json()
    operations = [op for path, methods in schema["paths"].items() if path.startswith("/api/v1/") for method, op in methods.items() if method in {"get", "post", "delete"}]
    assert len(operations) == 19
    for op in operations:
        assert "400" in op["responses"] and "503" in op["responses"]
        for code in ("400", "404", "409", "413", "422", "503"):
            if code in op["responses"]:
                assert op["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorEnvelope")
    assert "201" in schema["paths"]["/api/v1/sessions"]["post"]["responses"]
    assert "204" in schema["paths"]["/api/v1/sessions/{sid}"]["delete"]["responses"]
    assert "content" not in schema["paths"]["/api/v1/sessions/{sid}"]["delete"]["responses"]["204"]
    assert "422" not in schema["paths"]["/api/v1/sessions/{sid}/playback/seek"]["post"]["responses"]
    assert "422" in schema["paths"]["/api/v1/sessions/{sid}/logs"]["post"]["responses"]
    upload = schema["paths"]["/api/v1/sessions/{sid}/logs"]["post"]["requestBody"]["content"]
    assert "multipart/form-data" in upload


@pytest.mark.parametrize("body", [{}, {"time_s": 0, "frame_index": 0}, {"time_s": True}, {"frame_index": True}, {"frame_index": 1.5}, {"time_s": "1"}, {"frame_index": 2**53}, {"time_s": None}, {"time_s": 0, "unexpected": 1}])
def test_seek_rejects_ambiguous_or_non_numeric_input_with_400(client, body):
    response = client.post("/api/v1/sessions/s1/playback/seek", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("body", [{"time_s": -100}, {"frame_index": -100}, {"frame_index": 0}, {"time_s": 1.2}])
def test_seek_accepts_out_of_range_positions_for_future_runtime_clamping(client, body):
    assert client.post("/api/v1/sessions/s1/playback/seek", json=body).status_code == 503


@pytest.mark.parametrize("raw", ['{"time_s": NaN}', '{"time_s": Infinity}', '{"time_s": -Infinity}', '{"speed": NaN}', '{'])
def test_bad_json_never_leaks_nonfinite_values_or_422(client, raw):
    endpoint = "speed" if "speed" in raw else "seek"
    response = client.post(f"/api/v1/sessions/s1/playback/{endpoint}", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    json.loads(response.text, parse_constant=lambda value: pytest.fail(f"nonfinite JSON: {value}"))


@pytest.mark.parametrize("speed", [0, 0.09, 10.01, True, "2"])
def test_speed_rejects_values_outside_numeric_contract(client, speed):
    assert client.post("/api/v1/sessions/s1/playback/speed", json={"speed": speed}).status_code == 400


@pytest.mark.parametrize("speed", [0.1, 10])
def test_speed_accepts_inclusive_boundaries(client, speed):
    assert client.post("/api/v1/sessions/s1/playback/speed", json={"speed": speed}).status_code == 503


def test_step_and_session_version_are_explicit_enums(client):
    assert client.post("/api/v1/sessions/s1/playback/step", json={"direction": "next"}).status_code == 400
    assert client.post("/api/v1/sessions", json={"scene_id": "s", "robot_id": "r", "isaacsim_version": "5.1.0"}).status_code == 400
    assert client.post("/api/v1/sessions", json={"scene_id": "s", "robot_id": "r", "isaacsim_version": "6.2.0", "mode": "physics"}).status_code == 400


@pytest.mark.parametrize("query", ["", "channels=", "channels=a,,b", "channels=a&from_s=2&to_s=1", "channels=a&max_points=1", "channels=a&max_points=10001", "channels=a&from_s=NaN", "channels=a&from_s=-1", "channels=a&max_points=true", "channels=a&unknown=2"])
def test_curve_validates_csv_range_point_budget_and_unknown_query(client, query):
    assert client.get("/api/v1/sessions/s1/logs/l1/curve?" + query).status_code == 400


def test_curve_normalizes_csv_whitespace_and_first_occurrence_order():
    query = schemas().CurveQuery.model_validate({"channels": " b, a,b ,c "})
    assert query.channels == ["b", "a", "c"]
    assert query.max_points == 2000


@pytest.mark.parametrize("mapping", ["", "{", "[]", "null", '{"x":NaN}', '{"nested":{"x":Infinity}}'])
def test_upload_invalid_inline_mapping_is_a_request_error(client, mapping):
    response = client.post("/api/v1/sessions/s1/logs", files={"file": ("log.csv", b"t,j\n0,1")}, data={"mapping_config": mapping})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_upload_valid_mapping_and_named_parser_remain_unconfigured(client):
    response = client.post("/api/v1/sessions/s1/logs", files={"file": ("log.csv", b"t,j\n0,1")}, data={"mapping_config": '{"schema_version":1,"joints":{}}', "parser": "csv_v1"})
    assert response.status_code == 503
    assert client.post("/api/v1/sessions/s1/logs", data={"parser": "csv_v1"}).status_code == 400


def test_explicit_empty_parser_is_not_treated_as_omitted(client):
    assert client.post("/api/v1/sessions/s1/logs", files={"file": ("log.csv", b"t,j\n0,1")}, data={"parser": ""}).status_code == 400


def test_playback_ack_correlates_order_without_claiming_presented():
    p = {"session_id": "s1", "log_id": "l1", "playback_epoch": 1, "control_seq": 4, "state": "paused", "speed": 1, "duration_s": 2}
    body = {"request_id": "arbitrary-opaque-id", "control_seq": 4, "playback_epoch": 1, "ack_stage": "accepted", "playback": p}
    assert schemas().RESTPlaybackResponse.model_validate(body).control_seq == 4
    for changes in [{"ack_stage": "presented"}, {"control_seq": True}, {"control_seq": 3}, {"playback_epoch": 2}, {"request_id": 123}]:
        with pytest.raises(ValidationError):
            schemas().RESTPlaybackResponse.model_validate(dict(body, **changes))
    with pytest.raises(ValidationError):
        schemas().PlaybackSnapshot.model_validate(dict(p, presented_position={"log_time_s": 1}))


def test_error_details_are_finite_and_model_registry_exports():
    with pytest.raises(ValidationError):
        schemas().ErrorEnvelope.model_validate({"error": {"code": "TEST", "message": "bad", "details": {"nested": [float("nan")]}}})
    for model in schemas().ALL_MODELS:
        json.dumps(model.model_json_schema(), allow_nan=False)
    assert "RESTPlaybackResponse" in schemas().REST_MODELS


def test_seek_schema_expresses_nonnull_exclusive_position():
    from jsonschema import Draft202012Validator
    schema = schemas().SeekRequest.model_json_schema()
    validator=Draft202012Validator(schema)
    for valid in [{'time_s':1.5},{'frame_index':2},{'time_s':-1},{'frame_index':-2}]:
        assert validator.is_valid(valid)
    for invalid in [{},{'time_s':None},{'time_s':1,'frame_index':2},
                    {'time_s':1,'frame_index':None},{'time_s':True},{'frame_index':2.5}]:
        assert not validator.is_valid(invalid)


def test_seek_explicit_null_other_key_is_still_mutually_exclusive(client):
    assert client.post("/api/v1/sessions/s1/playback/seek", json={"time_s": 0, "frame_index": None}).status_code == 400


def test_publishing_video_declares_sei_parser_identity_and_capabilities():
    data={'publishing':True,'whep_url':'http://localhost:8889/s/whep','stream_path':'s'}
    with pytest.raises(ValidationError):schemas().VideoInfo.model_validate(data)
    video=schemas().VideoInfo.model_validate(dict(data,sei_uuid_hex='a'*32))
    assert video.catalog_schema_version==1
    assert video.decoder=='webcodecs_h264'
    with pytest.raises(ValidationError):schemas().VideoInfo.model_validate(dict(data,sei_uuid_hex='bad'))


def test_success_upload_example_can_load_its_log_id_and_preserves_mapping():
    examples = importlib.import_module("app.schemas.examples").examples()
    result = schemas().UploadResult.model_validate(examples["upload_result"]["payload"])
    request = schemas().LoadRequest(log_id=result.log_id)
    assert request.log_id == result.log_id
    assert result.mapping_snapshot_id and result.raw_file_ref and result.raw_file_sha256
    assert len(result.stats.joints_mapped) + len(result.stats.joints_missing) == 28
    assert result.channels[0].quality.origin == "measured"


def test_curve_null_samples_cannot_claim_valid_source_data():
    measured = {"origin": "measured", "valid": True, "processing": ["original"], "source_indices": [0]}
    with pytest.raises(ValidationError):
        schemas().CurvePoint(time_s=0, value=None, quality=measured)
    missing = {"origin": "missing", "valid": False, "processing": [], "source_indices": []}
    assert schemas().CurvePoint(time_s=0, value=None, quality=missing).value is None


@pytest.mark.parametrize("semantics,period", [("angular_wrapped", None), ("angular_unwrapped", 6.28), ("bounded", 6.28), ("linear", 1.0)])
def test_channel_descriptor_period_matches_shared_source_semantics(semantics, period):
    data = {"name": "angle", "label": "Angle", "kind": "custom", "unit": "rad", "source_field": "angle", "quality": {"origin": "measured", "valid": True}, "angle_semantics": semantics, "wrap_period": period}
    with pytest.raises(ValidationError):
        schemas().ChannelDescriptor.model_validate(data)
