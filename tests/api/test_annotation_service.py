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


def build_service(value=None, *, replay_data=None):
    # Only the archive I/O and process-owning context are substituted; the query
    # still exercises real validation, scope checks and boundary enforcement.
    data = replay_data or SimpleNamespace(metadata={} if value is None else {'log_annotations': value, 'b041': {'source_frame_count': 3}},
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


def test_action_basis_without_final_sample_keeps_complete_source_annotation_end(tmp_path):
    from tests.b041.test_adapter import bundle, load
    value = bundle()
    value['samples'][-1]['action'] = value['samples'][-1]['action_quality'] = None
    data = load(tmp_path, value, {'b041': {'basis': 'action'}})
    assert data.sample_times_s == [0., .1]
    assert data.metadata['log_annotations']['segments'][0]['end_s'] == .3
    result = query(build_service(replay_data=data))
    assert result.segments[0].start_s == 0.
    assert result.segments[0].end_s == .3
    assert (result.segments[0].start_frame, result.segments[0].end_frame) == (0, 3)


@pytest.mark.parametrize('mutation', [
    'count', 'count_bool', 'missing_samples', 'frame_order', 'frame_bool', 'time_order',
    'nonfinite_time', 'fractional_ns', 'overflow_time', 'numeric_time', 'wrong_unit',
    'wrong_format', 'wrong_clock', 'missing_origin', 'nonfinite_origin', 'origin_not_sample',
    'selected_grid_not_source', 'wrong_source_id', 'wrong_source_hash',
])
def test_complete_source_metadata_must_be_valid_before_extending_annotation_bounds(tmp_path, mutation):
    from tests.b041.test_adapter import load
    data = load(tmp_path)
    source = data.metadata['b041']
    if mutation == 'count': source['source_frame_count'] = 4
    elif mutation == 'count_bool': source['source_frame_count'] = True
    elif mutation == 'missing_samples': source.pop('samples')
    elif mutation == 'frame_order': source['samples'][1]['frame_index'] = 0
    elif mutation == 'frame_bool': source['samples'][1]['frame_index'] = True
    elif mutation == 'time_order': source['samples'][1]['time'] = source['samples'][0]['time']
    elif mutation == 'nonfinite_time': source['samples'][1]['time'] = 'NaN'
    elif mutation == 'fractional_ns': source['samples'][1]['time'] += '.5'
    elif mutation == 'overflow_time': source['samples'][-1]['time'] = '1e999999'
    elif mutation == 'numeric_time': source['samples'][1]['time'] = int(source['samples'][1]['time'])
    elif mutation == 'wrong_unit': source['source']['time_unit'] = 's'
    elif mutation == 'wrong_format': source['source']['format'] = 'runtime_events'
    elif mutation == 'wrong_clock': source['source']['clock'] = 'run_monotonic_event_time'
    elif mutation == 'missing_origin': data.metadata.pop('source_origin_exact_s')
    elif mutation == 'nonfinite_origin': data.metadata['source_origin_exact_s'] = 'Infinity'
    elif mutation == 'origin_not_sample': data.metadata['source_origin_exact_s'] = '1800000000.050000001'
    elif mutation == 'selected_grid_not_source': data.sample_times_s[1] = .11
    elif mutation == 'wrong_source_id': source['source']['id'] = 'unrelated-source'
    elif mutation == 'wrong_source_hash': source['source']['sha256'] = 'c' * 64
    with pytest.raises(SessionError) as caught:
        query(build_service(replay_data=data))
    assert caught.value.status_code == 422 and caught.value.code == 'INVALID_LOG_ANNOTATIONS'


@pytest.mark.parametrize('mutation', ['segment_end', 'segment_start', 'event_time'])
def test_full_source_end_does_not_relax_selected_start_or_event_bounds(tmp_path, mutation):
    from tests.b041.test_adapter import bundle, load
    value = bundle()
    value['samples'][-1]['action'] = value['samples'][-1]['action_quality'] = None
    data = load(tmp_path, value, {'b041': {'basis': 'action'}})
    annotations = data.metadata['log_annotations']
    if mutation == 'segment_end': annotations['segments'][0]['end_s'] = .31
    elif mutation == 'segment_start': annotations['segments'][0]['start_s'] = .15
    else: annotations['events'] = [dict(id='outside-action', time_s=.15, kind='feedback', message='', details={})]
    with pytest.raises(SessionError) as caught:
        query(build_service(replay_data=data))
    assert caught.value.status_code == 422 and caught.value.code == 'INVALID_LOG_ANNOTATIONS'
