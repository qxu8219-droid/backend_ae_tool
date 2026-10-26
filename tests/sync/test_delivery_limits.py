import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
from api_support import CSV, REQUEST, make_service, session
from test_runtime_api import ready
from test_websocket import message


def test_sending_deadline_disconnects_one_slow_peer_and_releases_subscription(tmp_path):
    from app.api.websocket import serve
    class BlockedTransport:
        headers = {'host': 'testserver'}
        url = SimpleNamespace(scheme='ws')
        def __init__(self):
            self.closed = []
        async def accept(self):
            pass
        async def send_text(self, _):
            await asyncio.Event().wait()
        async def receive(self):
            await asyncio.Event().wait()
        async def close(self, *, code, reason):
            self.closed.append((code, reason))
    async def run():
        async with make_service(tmp_path) as service:
            sid = await session(service)
            transport = BlockedTransport()
            await asyncio.wait_for(serve(transport, service, sid), timeout=3.)
            assert transport.closed == [(1013, 'SLOW_CONSUMER')]
            assert not service.contexts[sid].clients
            assert service.manager.get(sid).status == 'ready'
    asyncio.run(run())


def test_session_subscription_capacity_is_reusable_after_disconnect(tmp_path):
    from app.core.session_manager import SessionError
    async def run():
        async with make_service(tmp_path) as service:
            sid = await session(service)
            clients = [service.subscribe(sid) for _ in range(8)]
            with pytest.raises(SessionError) as error:
                service.subscribe(sid)
            assert error.value.code == 'WS_CAPACITY'
            service.unsubscribe(sid, clients[0])
            assert service.subscribe(sid).connection_id not in {item.connection_id for item in clients}
    asyncio.run(run())


def test_ten_hz_health_setting_does_not_throttle_twenty_hz_frame_evidence(tmp_path):
    from app.main import create_app
    with TestClient(create_app(replay_service=make_service(tmp_path))) as client:
        sid = client.post('/api/v1/sessions', json=REQUEST.model_dump()).json()['session_id']
        ready(client, sid)
        base = f'/api/v1/sessions/{sid}'
        lid = client.post(base + '/logs', files={'file': ('source.csv', CSV)}).json()['log_id']
        with client.websocket_connect(f'/ws/sessions/{sid}') as ws:
            first = ws.receive_json()
            ws.send_json({'schema_version': 1, 'session_id': sid, 'connection_id': first['connection_id'],
                          'seq': 1, 'wall_time': time.time(), 'type': 'set_telemetry_rate', 'data': {'request_id': 'rate', 'hz': 10}})
            message(ws, 'telemetry_rate_changed')
            client.post(base + '/playback/load', json={'log_id': lid})
            message(ws, 'frame_snapshot')
            start = time.monotonic(); frames = health = 0
            while time.monotonic() - start < 1.1:
                item = ws.receive_json()
                frames += item['type'] == 'frame_snapshot'
                health += item['type'] == 'health_telemetry'
            assert frames >= 15 and health <= 13, (frames, health)
