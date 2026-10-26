"""Public Timeline fault integration with explicitly synthetic worker evidence."""
import pytest

from replay_protocol import RPCResponse, WorkerEvent

from app.replay.timeline import Timeline
from app.replay.timeline_base import TimelineError
from timeline_support import (
    FakeWorker, ManualClock, applied_frame, build_sampler, frame_event, pump,
    rendered_frame, rpc_response,
)


def rig(*, loaded=True, timeout=10., times=(0., .5, 1.), session='s'):
    clock = ManualClock()
    sampler = build_sampler(session=session, times=times)
    timeline = Timeline(session, lambda log_id: sampler, clock=clock, ack_timeout_s=timeout)
    worker = FakeWorker(session, clock)
    if loaded:
        timeline.load('log')
        pump(timeline, worker)
    return timeline, worker, clock


def take_apply(timeline, worker):
    """Accept preceding fences but hold back the actual application receipt."""
    for _ in range(3):
        request = timeline.take_rpc()
        assert request is not None
        if request.command.cmd == 'apply_keyframe':
            return request
        assert timeline.receive_rpc(worker.handle_rpc(request))
    raise AssertionError('Current critical target did not follow its prefix')


def test_load_acceptance_does_not_invent_applied_or_rendered_evidence():
    timeline, worker, _ = rig(loaded=False)
    accepted = timeline.load('log')
    assert accepted.state == 'ready'
    assert accepted.target_position.log_time_s == 0.
    assert accepted.applied_position is None and accepted.rendered_identity is None
    prefix = timeline.take_rpc()
    assert prefix.command.cmd == 'load_log'
    assert timeline.receive_rpc(worker.handle_rpc(prefix))
    assert timeline.snapshot().applied_position is None
    assert timeline.snapshot().rendered_identity is None
    request = timeline.take_rpc()
    assert request.command.cmd == 'apply_keyframe'
    assert timeline.receive_rpc(rpc_response(request))
    assert timeline.snapshot().applied_position is None
    assert timeline.snapshot().rendered_identity is None
    assert timeline.receive_rpc(rpc_response(request, stage='applied',
                                            frame=applied_frame(request.command.frame)))
    assert timeline.snapshot().applied_position.log_time_s == 0.
    assert timeline.snapshot().rendered_identity is None
    assert timeline.receive_rpc(worker.handle_rpc(request))
    assert timeline.snapshot().rendered_identity.target_seq == request.command.frame.target_seq


def test_accepted_and_applied_keyframe_keep_playback_clock_suspended_until_rendered():
    timeline, worker, clock = rig()
    timeline.play()
    request = take_apply(timeline, worker)
    assert timeline.receive_rpc(rpc_response(request))
    clock.advance(2.)
    assert timeline.tick().target_position.log_time_s == 0.
    assert timeline.take_target() is None
    assert timeline.receive_rpc(rpc_response(request, stage='applied',
                                            frame=applied_frame(request.command.frame)))
    clock.advance(2.)
    assert timeline.tick().target_position.log_time_s == 0.
    assert timeline.receive_rpc(worker.handle_rpc(request))
    clock.advance(.25)
    assert timeline.tick().target_position.log_time_s == pytest.approx(.25)


def test_render_event_can_complete_keyframe_before_rpc_and_duplicates_are_harmless():
    timeline, worker, clock = rig()
    timeline.play()
    request = take_apply(timeline, worker)
    response = worker.handle_rpc(request)
    event = frame_event(response.frame, seq=1)
    assert timeline.receive_event(event)
    confirmed = timeline.snapshot()
    assert not timeline.receive_event(event)
    assert not timeline.receive_rpc(response)
    assert timeline.snapshot() == confirmed
    clock.advance(.25)
    assert timeline.tick().target_position.log_time_s == pytest.approx(.25)


@pytest.mark.parametrize('mutation', [
    {'session_id': 'foreign'}, {'log_id': 'foreign'}, {'playback_epoch': 500},
    {'control_seq': 500}, {'target_seq': 500},
    {'log_time_s': .5, 'progress': .5, 'source_sample_index': 1},
    {'duration_s': 2.}, {'source_sample_index': 2},
    {'source_joints': [9.] * 28},
])
def test_invalid_render_evidence_cannot_confirm_current_keyframe(mutation):
    timeline, worker, _ = rig(loaded=False)
    timeline.load('log')
    request = take_apply(timeline, worker)
    invalid = rendered_frame(request.command.frame, capture_id=50, **mutation)
    assert not timeline.receive_event(frame_event(invalid, seq=50))
    assert timeline.snapshot().applied_position is None
    assert timeline.snapshot().rendered_identity is None
    assert timeline.take_target() is None
    valid = rendered_frame(request.command.frame, capture_id=51)
    assert timeline.receive_event(frame_event(valid, seq=51))
    assert timeline.snapshot().rendered_identity.target_seq == request.command.frame.target_seq


def test_rpc_identity_must_match_active_request_even_with_valid_nested_frame():
    timeline, worker, _ = rig(loaded=False)
    timeline.load('log')
    request = take_apply(timeline, worker)
    for changes in ({'request_id': 'unknown'}, {'session_id': 'foreign'},
                    {'control_seq': 500}, {'playback_epoch': 500}):
        payload = rpc_response(request).model_dump(mode='python')
        payload.update(changes)
        assert not timeline.receive_rpc(RPCResponse.model_validate(payload))
    assert timeline.snapshot().applied_position is None
    assert timeline.receive_rpc(worker.handle_rpc(request))


def test_queued_but_unissued_target_cannot_claim_worker_rendering():
    timeline, worker, _ = rig(loaded=False)
    accepted = timeline.load('log')
    prefix = timeline.take_rpc()
    assert timeline.receive_rpc(worker.handle_rpc(prefix))
    queued = build_sampler().sample_at(
        0., playback_epoch=accepted.playback_epoch, control_seq=accepted.control_seq,
        target_seq=accepted.target_position.target_seq,
    )
    assert not timeline.receive_event(frame_event(rendered_frame(queued), seq=1))
    assert timeline.snapshot().applied_position is None
    assert timeline.snapshot().rendered_identity is None
    request = timeline.take_rpc()
    assert request.command.cmd == 'apply_keyframe'
    assert timeline.receive_rpc(worker.handle_rpc(request))


def test_invalid_unverified_raw_evidence_cannot_bypass_public_receipt_validation():
    timeline, worker, _ = rig(loaded=False)
    timeline.load('log')
    request = take_apply(timeline, worker)
    # model_copy deliberately bypasses Pydantic construction to exercise the
    # receiving boundary's validation, as malformed transport decoding could.
    bad = rendered_frame(request.command.frame).model_copy(update={'raw_identity_verified': False})
    valid_event = frame_event(rendered_frame(request.command.frame))
    event = valid_event.model_copy(update={'event': valid_event.event.model_copy(update={'frame': bad})})
    assert not timeline.receive_event(event)
    assert timeline.snapshot().rendered_identity is None
    assert timeline.receive_rpc(worker.handle_rpc(request))


def test_actual_worker_readback_may_differ_from_issued_source_joints():
    timeline, worker, _ = rig(loaded=False)
    timeline.load('log')
    request = take_apply(timeline, worker)
    evidence = rendered_frame(request.command.frame, joints=[.125] * 28,
                              chassis={'x': 4., 'y': 2., 'yaw': 1.})
    assert timeline.receive_rpc(rpc_response(request, stage='rendered', frame=evidence))
    assert timeline.snapshot().applied_position.log_time_s == 0.
    assert timeline.snapshot().rendered_identity.capture_id == 1


def test_superseded_load_ack_cannot_release_latest_control_or_skip_load_prefix():
    timeline, worker, _ = rig(loaded=False)
    first = timeline.load('log')
    old_load = timeline.take_rpc()
    latest = timeline.play()
    assert latest.control_seq > first.control_seq
    current_load = timeline.take_rpc()
    assert current_load.command.cmd == 'load_log'
    assert current_load.control_seq == latest.control_seq
    assert current_load.request_id != old_load.request_id
    assert not timeline.receive_rpc(rpc_response(old_load))
    assert timeline.take_rpc() is None
    assert timeline.snapshot().rendered_identity is None
    assert timeline.receive_rpc(worker.handle_rpc(current_load))
    request = timeline.take_rpc()
    assert request.command.cmd == 'apply_keyframe'
    assert request.control_seq == latest.control_seq
    assert timeline.receive_rpc(worker.handle_rpc(request))
    assert timeline.snapshot().state == 'playing'


def test_many_controls_while_load_pending_deliver_only_latest_seek_pose():
    timeline, worker, _ = rig(loaded=False)
    timeline.load('log')
    obsolete = timeline.take_rpc()
    timeline.play()
    timeline.pause()
    timeline.set_speed(2.)
    final = timeline.seek(time_s=.5)
    assert not timeline.receive_rpc(rpc_response(obsolete))
    result = pump(timeline, worker)
    assert [request.command.cmd for request in worker.requests] == ['load_log', 'apply_keyframe']
    assert result.state == 'paused' and result.speed == 2.
    assert result.applied_position.log_time_s == .5
    assert result.rendered_identity.control_seq == final.control_seq


def test_old_pub_target_cannot_apply_after_pause_barrier():
    timeline, worker, clock = rig()
    timeline.play()
    pump(timeline, worker)
    clock.advance(.25)
    timeline.tick()
    old_message = timeline.take_target()
    assert old_message is not None
    paused = timeline.pause()
    barrier = timeline.take_rpc()
    assert barrier.command.cmd == 'pause_barrier'
    assert timeline.receive_rpc(worker.handle_rpc(barrier))
    # The delayed PUB message was already taken before the reliable pause fence.
    assert worker.publish_target(old_message) is None
    result = pump(timeline, worker)
    assert result.state == 'paused'
    assert result.applied_position.log_time_s == .25
    assert result.rendered_identity.control_seq == paused.control_seq
    clock.advance(.5)
    assert timeline.tick().target_position.log_time_s == .25


def test_delayed_pre_seek_receipt_cannot_replace_new_epoch_pose():
    timeline, worker, clock = rig()
    timeline.play()
    pump(timeline, worker)
    clock.advance(.25)
    timeline.tick()
    message = timeline.take_target()
    delayed = worker.publish_target(message)
    before = timeline.snapshot()
    sought = timeline.seek(time_s=.75)
    assert sought.playback_epoch > before.playback_epoch
    assert sought.applied_position is None and sought.rendered_identity is None
    assert not timeline.receive_event(delayed)
    result = pump(timeline, worker)
    assert result.applied_position.log_time_s == .75
    assert result.rendered_identity.playback_epoch == sought.playback_epoch


def test_out_of_order_stream_render_cannot_regress_newer_applied_position():
    timeline, worker, clock = rig()
    timeline.play()
    pump(timeline, worker)
    clock.advance(.25)
    timeline.tick()
    older = timeline.take_target().frame
    clock.advance(.25)
    timeline.tick()
    newer = timeline.take_target().frame
    assert timeline.receive_event(frame_event(applied_frame(newer), seq=1))
    assert timeline.receive_event(frame_event(rendered_frame(older, capture_id=10), seq=2))
    snapshot = timeline.snapshot()
    assert snapshot.applied_position.log_time_s == .5
    assert snapshot.rendered_identity.target_seq == older.target_seq
    assert timeline.receive_event(frame_event(rendered_frame(newer, capture_id=11), seq=3))
    newest = timeline.snapshot()
    assert not timeline.receive_event(frame_event(rendered_frame(older, capture_id=12), seq=4))
    assert timeline.snapshot() == newest


def test_capture_identity_cannot_be_reused_across_seek_epoch():
    timeline, worker, _ = rig()
    first_capture = timeline.snapshot().rendered_identity.capture_id
    timeline.seek(time_s=.5)
    request = take_apply(timeline, worker)
    stale_capture = rendered_frame(request.command.frame, capture_id=first_capture)
    assert not timeline.receive_rpc(rpc_response(request, stage='rendered', frame=stale_capture))
    assert timeline.snapshot().rendered_identity is None
    assert timeline.receive_rpc(worker.handle_rpc(request))
    assert timeline.snapshot().rendered_identity.capture_id > first_capture


def test_worker_sequence_replay_and_foreign_error_cannot_fail_live_session():
    timeline, worker, _ = rig()
    ready = WorkerEvent(session_id='s', seq=20, event={'type': 'ready'})
    assert timeline.receive_event(ready)
    stale_failure = WorkerEvent(
        session_id='s', seq=19,
        event={'type': 'error', 'error': {'code': 'STALE_FAILURE', 'message': 'Old worker event'}},
    )
    assert not timeline.receive_event(stale_failure)
    foreign_failure = stale_failure.model_copy(update={'session_id': 'other', 'seq': 21})
    assert not timeline.receive_event(foreign_failure)
    assert timeline.error is None
    assert timeline.snapshot().state == 'ready'
    health = WorkerEvent(session_id='s', seq=21,
                         event={'type': 'health', 'render_fps': 60., 'wall_time_s': 1000.})
    assert timeline.receive_event(health)
    assert not timeline.receive_event(health)


@pytest.mark.parametrize('receipt_kind', ['rpc', 'event'])
def test_proof_at_expired_deadline_cannot_revive_session(receipt_kind):
    timeline, worker, clock = rig(loaded=False, timeout=1.)
    timeline.load('log')
    request = take_apply(timeline, worker)
    response = worker.handle_rpc(request)
    clock.advance(1.)
    if receipt_kind == 'rpc':
        assert not timeline.receive_rpc(response)
    else:
        assert not timeline.receive_event(frame_event(response.frame))
    assert timeline.snapshot().state == 'error'
    assert timeline.error.code == 'WORKER_TIMEOUT'
    assert timeline.error.status_code == 504
    assert timeline.snapshot().rendered_identity is None
    assert timeline.take_rpc() is None and timeline.take_target() is None
    with pytest.raises(TimelineError, match='.') as error:
        timeline.load('log')
    assert error.value.code == 'SESSION_ERROR'


def test_timeout_budget_includes_prefix_and_is_not_restarted_by_accepted_ack():
    timeline, worker, clock = rig(loaded=False, timeout=1.)
    timeline.load('log')
    prefix = timeline.take_rpc()
    clock.advance(.75)
    assert timeline.receive_rpc(worker.handle_rpc(prefix))
    request = timeline.take_rpc()
    assert timeline.receive_rpc(rpc_response(request))
    clock.advance(.25)
    assert timeline.tick().state == 'error'
    assert timeline.error.code == 'WORKER_TIMEOUT'


def test_current_rpc_error_is_terminal_but_new_session_can_recover():
    timeline, worker, _ = rig(loaded=False)
    timeline.load('log')
    request = take_apply(timeline, worker)
    failure = rpc_response(request, error={'code': 'WORKER_REJECTED', 'message': 'Synthetic fault'})
    assert timeline.receive_rpc(failure)
    assert timeline.snapshot().state == 'error'
    assert timeline.error.code == 'WORKER_REJECTED' and timeline.error.status_code == 502
    assert timeline.take_rpc() is None and timeline.take_target() is None
    with pytest.raises(TimelineError) as error:
        timeline.play()
    assert error.value.code == 'SESSION_ERROR'
    fresh, fresh_worker, _ = rig(loaded=False, session='replacement')
    fresh.load('log')
    assert pump(fresh, fresh_worker).rendered_identity.session_id == 'replacement'
    assert not fresh.receive_rpc(failure)


def test_stale_rpc_failure_cannot_poison_new_current_control():
    timeline, worker, _ = rig(loaded=False)
    timeline.load('log')
    old = timeline.take_rpc()
    timeline.seek(time_s=.5)
    failure = rpc_response(old, error={'code': 'OLD_FAILURE', 'message': 'Superseded fault'})
    assert not timeline.receive_rpc(failure)
    assert timeline.error is None
    assert pump(timeline, worker).applied_position.log_time_s == .5


def test_worker_error_event_and_explicit_failure_end_session_without_false_render():
    timeline, worker, _ = rig()
    confirmed = timeline.snapshot().rendered_identity
    event = WorkerEvent(session_id='s', seq=1,
                        event={'type': 'error', 'error': {'code': 'WORKER_DIED', 'message': 'Synthetic fault'}})
    assert timeline.receive_event(event)
    assert timeline.snapshot().state == 'error'
    assert timeline.snapshot().rendered_identity == confirmed
    assert timeline.error.code == 'WORKER_DIED'
    assert not timeline.receive_event(event)
    fresh, _, _ = rig(loaded=False, session='new')
    failed = fresh.worker_failed('WORKER_EXITED', 'Synthetic exit')
    assert failed.state == 'error'
    assert failed.rendered_identity is None
    assert fresh.error.code == 'WORKER_EXITED'


def test_late_tick_delivers_exact_last_frame_once_on_reliable_path():
    timeline, worker, clock = rig()
    timeline.play()
    pump(timeline, worker)
    before = timeline.snapshot()
    clock.advance(5.)
    ended = timeline.tick()
    assert ended.state == 'ended'
    assert ended.target_position.log_time_s == 1.
    assert ended.target_position.source_sample_index == 2
    assert ended.control_seq == before.control_seq
    assert ended.playback_epoch == before.playback_epoch
    assert timeline.take_target() is None
    barrier = timeline.take_rpc()
    assert barrier.command.cmd == 'pause_barrier'
    assert timeline.receive_rpc(worker.handle_rpc(barrier))
    request = timeline.take_rpc()
    assert request.command.cmd == 'apply_keyframe'
    frame = request.command.frame
    assert (frame.log_time_s, frame.progress, frame.source_sample_index, frame.joints[6]) == (1., 1., 2, 2.)
    assert timeline.receive_rpc(worker.handle_rpc(request))
    confirmed = timeline.snapshot()
    for _ in range(5):
        clock.advance(1.)
        assert timeline.tick() == confirmed
        assert timeline.take_rpc() is None and timeline.take_target() is None


def test_backpressure_keeps_only_latest_generated_stream_target():
    timeline, worker, clock = rig(times=(0., 100.))
    timeline.play()
    pump(timeline, worker)
    for _ in range(500):
        clock.advance(.02)
        timeline.tick()
    snapshot = timeline.snapshot()
    target = timeline.take_target()
    assert target.frame.log_time_s == pytest.approx(10.)
    assert target.frame.target_seq == snapshot.target_position.target_seq
    assert timeline.take_target() is None
    assert timeline.take_rpc() is None
    event = worker.publish_target(target)
    assert timeline.receive_event(event)
    assert timeline.snapshot().applied_position.log_time_s == pytest.approx(10.)
