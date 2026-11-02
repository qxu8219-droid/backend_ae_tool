import asyncio
import time

import httpx
from fastapi.testclient import TestClient
from app.adapters.base import ParseLimits
from app.schemas.rest import ErrorEnvelope, RESTPlaybackResponse, UploadResult, CurveResponse
from api_support import CSV, REQUEST, make_service


def ready(client, sid):
    for _ in range(400):
        value = client.get(f'/api/v1/sessions/{sid}').json()
        if value['status'] == 'ready':
            return
        assert value['status'] != 'error', value
        time.sleep(.01)
    raise AssertionError(value)


def test_configured_rest_routes_complete_import_and_all_playback_controls(tmp_path):
    from app.main import create_app
    service = make_service(tmp_path)
    with TestClient(create_app(replay_service=service)) as client:
        assert client.get('/api/v1/scenes').json()['scenes'][0]['id'] == 'empty'
        assert client.get('/api/v1/robots').json()['robots'][0]['dof'] == 28
        assert len(client.get('/api/v1/robots/galaxea_r1pro_2026/joints').json()['joints']) == 28
        created = client.post('/api/v1/sessions', json=REQUEST.model_dump())
        assert created.status_code == 201
        sid = created.json()['session_id']; base = f'/api/v1/sessions/{sid}'
        assert client.post('/api/v1/sessions', json=REQUEST.model_dump()).status_code == 409
        ready(client, sid)
        assert client.post(base + '/playback/play', json={}).status_code == 409
        uploaded = client.post(base + '/logs', files={'file': ('source.csv', CSV, 'text/csv')})
        assert uploaded.status_code == 201, uploaded.text
        log = UploadResult.model_validate(uploaded.json())
        assert log.stats.sample_count == 6
        assert client.get(base + '/playback').json()['state'] == 'idle'
        assert client.get(base + f'/logs/{log.log_id}/channels').status_code == 200
        curve = CurveResponse.model_validate(client.get(base + f'/logs/{log.log_id}/curve', params={'channels': 'steer_motor_joint1', 'max_points': 2}).json())
        assert 4.2 in [p.value for p in curve.series[0].points]
        for command, payload in [('load', {'log_id': log.log_id}), ('play', {}), ('speed', {'speed': 10.}),
                                 ('seek', {'time_s': .1}), ('pause', {}), ('step', {'direction': 'forward'}), ('stop', {})]:
            result = client.post(base + '/playback/' + command, json=payload)
            assert result.status_code == 200, result.text
            response = RESTPlaybackResponse.model_validate(result.json())
            assert response.ack_stage == 'accepted'
        assert client.get(base + '/video').status_code == 200
        assert client.delete(base).status_code == 204
        assert client.delete(base).status_code == 204


def test_configured_errors_and_cors_use_real_service_boundaries(tmp_path):
    from app.main import create_app
    with TestClient(create_app(replay_service=make_service(tmp_path))) as client:
        for path in ('/api/v1/robots/unknown/joints', '/api/v1/sessions/unknown'):
            response = client.get(path)
            assert response.status_code == 404
            ErrorEnvelope.model_validate(response.json())
        options = client.options('/api/v1/sessions', headers={'Origin': 'http://127.0.0.1:5173', 'Access-Control-Request-Method': 'POST'})
        assert options.headers['access-control-allow-origin'] == 'http://127.0.0.1:5173'
        sid = client.post('/api/v1/sessions', json=REQUEST.model_dump()).json()['session_id']
        ready(client, sid)
        base = f'/api/v1/sessions/{sid}'
        response = client.post(base + '/logs', files={'file': ('source.csv', b'not a usable csv', 'text/csv')})
        assert response.status_code == 422
        assert ErrorEnvelope.model_validate(response.json()).error.details['issues']
        response = client.post(base + '/playback/load', json={'log_id': 'missing'})
        assert response.status_code == 404


def test_file_and_total_stream_limits_reject_before_unbounded_spooling(tmp_path):
    from app.main import create_app
    service = make_service(tmp_path, limits=ParseLimits(max_file_bytes=32))
    app = create_app(replay_service=service)
    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                sid = (await client.post('/api/v1/sessions', json=REQUEST.model_dump())).json()['session_id']
                for _ in range(300):
                    if (await client.get(f'/api/v1/sessions/{sid}')).json()['status'] == 'ready':
                        break
                    await asyncio.sleep(.01)
                path = f'/api/v1/sessions/{sid}/logs'
                response = await client.post(path, files={'file': ('source.csv', CSV, 'text/csv')})
                assert response.status_code == 413
                consumed = []
                async def huge():
                    yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="large.csv"\r\n\r\n'
                    for i in range(100):
                        consumed.append(i)
                        yield b'x' * 65536
                    yield b'\r\n--test--\r\n'
                response = await client.post(path, content=huge(), headers={'Content-Type': 'multipart/form-data; boundary=test'})
                assert response.status_code == 413, response.text
                ErrorEnvelope.model_validate(response.json())
                assert len(consumed) < 100
                assert not await service.logs.list_logs(sid)
    asyncio.run(run())
