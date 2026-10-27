"""Read annotations from the owned immutable archive, never external references."""
import asyncio
from types import SimpleNamespace
import pytest
from app.core.session_manager import SessionError
from app.services.replay_service import ReplayService


def payload():
    return dict(source_id='episode-8', source_sha256='a' * 64, alignment='source_frames',
        provisional=True, assumptions=['temporary calibration'], reference={'path': '/not/a/file'},
        segments=[dict(id='stage', start_s=0., end_s=.3, start_frame=0, end_frame=3,
            label='grasp', review_status='pending_review', boundary_status='pending_review',
            training_approved=False, details={})], events=[])


def build_service(value=None):
    # Only the archive I/O and process-owning context are substituted; the query
    # still exercises real validation, scope checks and boundary enforcement.
    data = SimpleNamespace(metadata={} if value is None else {'log_annotations': value, 'b041': {'source_frame_count': 3}},
        raw_file_sha256='b' * 64, sample_times_s=[0., .1, .2])
    service = object.__new__(ReplayService)
    context = SimpleNamespace(closed=False)
    def owned(sid):
        if sid != 's': raise SessionError(404, 'SESSION_NOT_FOUND', 'Session does not exist')
        return context
    async def get(sid, lid):
        if (sid, lid) != ('s', 'l'): raise SessionError(404, 'LOG_NOT_FOUND', 'Log does not exist')
        return data
    service.context = owned
    service.logs = SimpleNamespace(get=get)
    return service


def query(service, sid='s', lid='l'):
    method = getattr(service, 'annotations', None)
    assert method is not None, 'Annotation service must read archive metadata'
    return asyncio.run(method(sid, lid))


def test_plain_logs_return_explicit_empty_annotations_and_archive_digest():
    result = query(build_service())
    assert result.log_id == 'l' and result.source_sha256 == 'b' * 64
    assert result.alignment == 'none' and not result.provisional
    assert result.segments == result.events == result.assumptions == []


def test_metadata_is_validated_with_archive_identity_and_terminal_half_open_interval():
    result = query(build_service(payload()))
    assert result.log_id == 'l'
    assert result.reference == {'path': '/not/a/file'}
    assert result.segments[0].end_s == .3 and result.segments[0].training_approved is False


@pytest.mark.parametrize('kind', ['start_after_end', 'past_duration', 'past_frames', 'event_past_duration', 'wrong_shape', 'wrong_log'])
def test_invalid_stored_annotations_use_a_bounded_error_envelope(kind):
    value = payload()
    if kind == 'start_after_end': value['segments'][0]['start_s'] = .4
    elif kind == 'past_duration': value['segments'][0]['end_s'] = .31
    elif kind == 'past_frames': value['segments'][0]['end_frame'] = 4
    elif kind == 'event_past_duration': value['events'] = [dict(id='e', time_s=.21, kind='feedback', message='', details={})]
    elif kind == 'wrong_shape': value = '/no/path/read'
    else: value['log_id'] = 'forged'
    with pytest.raises(SessionError) as caught:
        query(build_service(value))
    assert caught.value.status_code == 422 and caught.value.code == 'INVALID_LOG_ANNOTATIONS'


@pytest.mark.parametrize('sid,lid,code', [('missing', 'l', 'SESSION_NOT_FOUND'), ('s', 'missing', 'LOG_NOT_FOUND')])
def test_annotation_queries_respect_session_and_log_ownership(sid, lid, code):
    with pytest.raises(SessionError) as caught: query(build_service(), sid, lid)
    assert caught.value.status_code == 404 and caught.value.code == code


def test_annotation_http_route_returns_fallback_and_bounded_resource_errors():
    from fastapi.testclient import TestClient
    from app.main import create_app
    service = build_service()
    service.manager = None
    service.logs.limits = SimpleNamespace(max_file_bytes=1024)
    service.allowed_origins = ()
    client = TestClient(create_app(replay_service=service))
    try:
        result = client.get('/api/v1/sessions/s/logs/l/annotations')
        assert result.status_code == 200
        assert result.json()['alignment'] == 'none' and result.json()['log_id'] == 'l'
        assert client.get('/api/v1/sessions/s/logs/missing/annotations').status_code == 404
        value = payload(); value['segments'][0]['end_frame'] = 4
        broken = build_service(value)
        service.logs.get = broken.logs.get
        response = client.get('/api/v1/sessions/s/logs/l/annotations')
        assert response.status_code == 422
        assert response.json() == {'error': {'code': 'INVALID_LOG_ANNOTATIONS',
            'message': 'Stored annotations violate their contract or source bounds', 'details': {}}}
    finally:
        client.close()
