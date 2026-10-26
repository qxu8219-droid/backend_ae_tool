import sys
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app.schemas.ws import WS_SERVER_ADAPTER

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
from api_support import CSV, REQUEST, make_service
from test_runtime_api import ready


def message(ws, kind):
    for _ in range(100):
        value = ws.receive_json()
        WS_SERVER_ADAPTER.validate_python(value)
        if value['type'] == kind:
            return value
    raise AssertionError('Expected WebSocket message not received')


def test_ws_reconnect_metadata_frames_and_client_messages_use_independent_connection_sequences(tmp_path):
    from app.main import create_app
    service = make_service(tmp_path)
    with TestClient(create_app(replay_service=service)) as client:
        sid = client.post('/api/v1/sessions', json=REQUEST.model_dump()).json()['session_id']
        base = f'/api/v1/sessions/{sid}'
        ready(client, sid)
        lid = client.post(base + '/logs', files={'file': ('source.csv', CSV)}).json()['log_id']
        with client.websocket_connect(f'/ws/sessions/{sid}') as ws:
            first = ws.receive_json()
            WS_SERVER_ADAPTER.validate_python(first)
            assert first['seq'] == 1 and first['type'] == 'connection_snapshot'
            assert len(first['data']['joint_meta']['joints']) == 28
            assert first['data']['sync']['state'] == 'awaiting_pair'
            cid = first['connection_id']
            ws.send_json({'schema_version': 1, 'session_id': sid, 'connection_id': cid, 'seq': 1, 'wall_time': time.time(),
                          'type': 'set_telemetry_rate', 'data': {'request_id': 'rate', 'hz': 10}})
            assert message(ws, 'telemetry_rate_changed')['data']['hz'] == 10
            ws.send_json({'schema_version': 1, 'session_id': sid, 'connection_id': cid, 'seq': 2, 'wall_time': time.time(),
                          'type': 'ping', 'data': {'request_id': 'ping'}})
            assert message(ws, 'pong')['data']['request_id'] == 'ping'
            client.post(base + '/playback/load', json={'log_id': lid})
            frame = message(ws, 'frame_snapshot')['data']
            assert frame['log_id'] == lid and frame['log_time_s'] == 0 and frame['state'] == 'ready'
            accepted = client.post(base + '/playback/seek', json={'time_s': .1}).json()
            for _ in range(100):
                candidate = message(ws, 'frame_snapshot')['data']
                if candidate['control_seq'] == accepted['control_seq']:
                    break
            assert candidate['log_time_s'] == .1 and candidate['limit_flags'] & 1
        with client.websocket_connect(f'/ws/sessions/{sid}') as ws:
            second = ws.receive_json()
            assert second['type'] == 'connection_snapshot' and second['seq'] == 1
            assert second['connection_id'] != cid and len(second['data']['joint_meta']['joints']) == 28
            assert second['data']['playback']['playback_epoch'] == accepted['playback_epoch']


def test_ws_rejects_foreign_origins_and_stale_client_identity_without_harming_session(tmp_path):
    from app.main import create_app
    service = make_service(tmp_path)
    with TestClient(create_app(replay_service=service)) as client:
        sid = client.post('/api/v1/sessions', json=REQUEST.model_dump()).json()['session_id']
        ready(client, sid)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f'/ws/sessions/{sid}', headers={'Origin': 'https://foreign.example'}):
                pass
        with client.websocket_connect(f'/ws/sessions/{sid}') as ws:
            ws.receive_json()
            ws.send_json({'schema_version': 1, 'session_id': sid, 'connection_id': 'retired-connection', 'seq': 1,
                          'wall_time': time.time(), 'type': 'ping', 'data': {'request_id': 'bad'}})
            with pytest.raises(WebSocketDisconnect) as closed:
                for _ in range(100):
                    ws.receive_json()
            assert closed.value.code == 1008
        assert client.get(f'/api/v1/sessions/{sid}').json()['status'] == 'ready'


def test_deleting_session_closes_websocket_with_explicit_retired_session_reason(tmp_path):
    from app.main import create_app
    with TestClient(create_app(replay_service=make_service(tmp_path))) as client:
        sid = client.post('/api/v1/sessions', json=REQUEST.model_dump()).json()['session_id']
        with client.websocket_connect(f'/ws/sessions/{sid}') as ws:
            assert ws.receive_json()['type'] == 'connection_snapshot'
            assert client.delete(f'/api/v1/sessions/{sid}').status_code == 204
            with pytest.raises(WebSocketDisconnect) as closed:
                for _ in range(100):
                    ws.receive_json()
            assert closed.value.code == 1001 and closed.value.reason == 'SESSION_DELETED'
