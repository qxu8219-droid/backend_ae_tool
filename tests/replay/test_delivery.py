"""Delivery acceptance tests using real protocol envelopes and hand-set poses."""
from __future__ import annotations

import importlib

import pytest
from replay_protocol import AppliedFrame, RPCResponse, RenderedFrame, TargetFrame, WorkerEvent


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def tracker(*, capacity=64, timeout_s=10.0):
    module = importlib.import_module("app.replay.delivery")
    clock = Clock()
    result = module.DeliveryTracker("s", clock=clock, capacity=capacity, timeout_s=timeout_s)
    result.reset("log", 1, 1)
    return result, clock


def target(seq=1, *, control=1, epoch=1, time=0.0, log="log"):
    return TargetFrame(
        session_id="s", log_id=log, playback_epoch=epoch, control_seq=control,
        target_seq=seq, log_time_s=time, duration_s=1.0, progress=time,
        source_sample_index=0 if time < 1 else 1, joints=[2.0] * 28,
        quality=[{"origin": "measured", "valid": True, "processing": ["original"],
                  "source_indices": [0]}] * 28,
        chassis={"x": 1.0, "y": 2.0, "yaw": 0.0},
    )


def evidence(frame, *, capture=None, **changes):
    data = frame.model_dump()
    data.update(source_joints=list(frame.joints), applied_wall_s=1000.0)
    data.update(changes)
    if capture is None:
        return AppliedFrame.model_validate(data)
    data.update(capture_id=capture, render_seq=capture, captured_wall_s=1001.0,
                raw_identity_verified=True)
    return RenderedFrame.model_validate(data)


def response(request, *, stage="accepted", frame=None, error=None):
    return RPCResponse(session_id=request.session_id, request_id=request.request_id,
                       playback_epoch=request.playback_epoch, control_seq=request.control_seq,
                       stage=stage, ok=error is None, error=error, frame=frame)


def event(frame=None, *, seq=1, kind=None, **changes):
    payload = {"type": kind or ("rendered" if isinstance(frame, RenderedFrame) else "applied"),
               "frame": frame} if frame is not None else {"type": kind or "ready"}
    return WorkerEvent.model_validate({"session_id": "s", "seq": seq, "event": payload, **changes})


def test_delivery_module_exists():
    # A missing delivery engine must fail an assertion, before implementation exists.
    assert importlib.util.find_spec("app.replay.delivery") is not None


def test_load_prefix_then_keyframe_waits_for_rendered_proof():
    delivery, _ = tracker()
    delivery.keyframe(target(), prefix="load_log")
    assert delivery.critical_pending
    assert delivery.loaded_log_id is None
    load = delivery.take_rpc()
    assert load.command.cmd == "load_log"
    assert load.command.log_id == "log"
    assert delivery.take_rpc() is None
    assert delivery.receive_rpc(response(load))
    assert delivery.loaded_log_id == "log"
    assert not delivery.receive_rpc(response(load))
    apply = delivery.take_rpc()
    assert apply.request_id != load.request_id
    assert apply.command.cmd == "apply_keyframe"
    assert delivery.receive_rpc(response(apply))
    assert delivery.applied_position is None
    assert delivery.critical_pending
    assert delivery.receive_rpc(response(apply, stage="applied", frame=evidence(target())))
    assert delivery.applied_position.model_dump() == {
        "log_time_s": 0.0, "source_sample_index": 0, "target_seq": 1}
    assert delivery.critical_pending
    assert delivery.receive_rpc(response(apply, stage="rendered", frame=evidence(target(), capture=1)))
    assert not delivery.critical_pending
    assert delivery.rendered_identity.capture_id == 1
    assert not delivery.receive_rpc(response(apply, stage="rendered", frame=evidence(target(), capture=1)))


def test_pause_prefix_orders_fence_before_frame():
    delivery, _ = tracker()
    delivery.keyframe(target(3, time=.5), prefix="pause_barrier")
    pause = delivery.take_rpc()
    assert pause.command.cmd == "pause_barrier"
    assert pause.command.target_seq == 3
    assert delivery.take_rpc() is None
    assert delivery.receive_rpc(response(pause))
    apply = delivery.take_rpc()
    assert apply.command.frame.log_time_s == .5


def test_only_taken_current_target_can_complete_keyframe_from_event():
    delivery, _ = tracker()
    delivery.keyframe(target())
    assert not delivery.receive_event(event(evidence(target(), capture=1)))
    apply = delivery.take_rpc()
    assert delivery.receive_event(event(evidence(target(), capture=1), seq=2))
    assert not delivery.critical_pending
    assert delivery.take_rpc() is None
    assert not delivery.receive_rpc(response(apply))


def test_stream_is_latest_only_and_waits_for_pending_keyframe():
    delivery, _ = tracker()
    delivery.keyframe(target())
    apply = delivery.take_rpc()
    for seq in range(2, 502):
        delivery.queue_target(target(seq, time=.5))
    assert delivery.take_target() is None
    assert delivery.receive_rpc(response(apply, stage="rendered", frame=evidence(target(), capture=1)))
    message = delivery.take_target()
    assert message.frame.target_seq == 501
    assert message.frame.log_time_s == .5
    assert delivery.take_target() is None
    assert not delivery.receive_event(event(evidence(target(500, time=.5)), seq=1))
    assert delivery.receive_event(event(evidence(target(501, time=.5)), seq=2))
    assert delivery.applied_position.target_seq == 501


def test_reset_supersedes_load_and_keeps_unconfirmed_log_unloaded():
    delivery, _ = tracker()
    delivery.keyframe(target(), prefix="load_log")
    old_load = delivery.take_rpc()
    delivery.reset("log", 1, 2)
    delivery.keyframe(target(2, control=2), prefix="load_log")
    current_load = delivery.take_rpc()
    assert not delivery.receive_rpc(response(old_load))
    assert delivery.loaded_log_id is None
    assert delivery.take_rpc() is None
    assert delivery.receive_rpc(response(current_load))
    assert delivery.loaded_log_id == "log"
    assert delivery.take_rpc().command.frame.control_seq == 2


def test_keyframe_supersedes_old_frame_and_stream_even_without_reset():
    delivery, _ = tracker()
    delivery.keyframe(target())
    old = delivery.take_rpc()
    delivery.queue_target(target(2))
    delivery.keyframe(target(3, time=.5))
    current = delivery.take_rpc()
    assert not delivery.receive_rpc(response(old, stage="rendered", frame=evidence(target(), capture=1)))
    assert not delivery.receive_event(event(evidence(target(), capture=1)))
    assert delivery.critical_pending
    assert delivery.receive_rpc(response(current, stage="rendered", frame=evidence(target(3, time=.5), capture=2)))
    assert delivery.take_target() is None


@pytest.mark.parametrize("changes", [
    {"session_id": "foreign"}, {"log_id": "foreign"}, {"playback_epoch": 0},
    {"playback_epoch": 2}, {"control_seq": 0}, {"control_seq": 2},
    {"target_seq": 0}, {"target_seq": 2}, {"source_sample_index": 1},
    {"source_joints": [3.0] * 28}, {"log_time_s": .5, "progress": .5},
    {"duration_s": 2.0},
])
def test_current_envelope_cannot_claim_a_different_target(changes):
    delivery, _ = tracker()
    delivery.queue_target(target())
    delivery.take_target()
    changed = evidence(target(), **changes)
    packet = event(changed, session_id=changed.session_id)
    assert not delivery.receive_event(packet)
    assert delivery.applied_position is None
    assert delivery.rendered_identity is None


def test_actual_readback_joints_and_chassis_may_differ_from_source():
    delivery, _ = tracker()
    delivery.keyframe(target())
    request = delivery.take_rpc()
    rendered = evidence(target(), capture=1, joints=[-4.0] * 28,
                        chassis={"x": 4.0, "y": 5.0, "yaw": 6.0})
    assert delivery.receive_rpc(response(request, stage="rendered", frame=rendered))
    assert delivery.applied_position.target_seq == 1
    assert delivery.rendered_identity.capture_id == 1


@pytest.mark.parametrize("stage", ["accepted", "applied", "rendered"])
def test_timeout_includes_prefix_wait_and_rejects_late_rpc(stage):
    delivery, clock = tracker()
    delivery.keyframe(target(), prefix="load_log")
    load = delivery.take_rpc()
    clock.now = 9.0
    assert delivery.receive_rpc(response(load))
    request = delivery.take_rpc()
    clock.now = 10.0
    proof = None if stage == "accepted" else evidence(target(), capture=1 if stage == "rendered" else None)
    assert not delivery.receive_rpc(response(request, stage=stage, frame=proof))
    assert delivery.error.code == "WORKER_TIMEOUT"
    assert delivery.error.status_code == 504
    assert delivery.take_rpc() is None
    assert delivery.take_target() is None
    assert not delivery.critical_pending


def test_timeout_starts_before_request_taken_and_late_event_cannot_recover():
    delivery, clock = tracker()
    delivery.keyframe(target())
    clock.now = 10.0
    assert delivery.take_rpc() is None
    assert delivery.error.code == "WORKER_TIMEOUT"
    assert not delivery.receive_event(event(evidence(target(), capture=1)))
    assert delivery.rendered_identity is None
    assert not delivery.check_timeout()


def test_rendered_event_at_deadline_times_out():
    delivery, clock = tracker()
    delivery.keyframe(target())
    delivery.take_rpc()
    clock.now = 10.0
    assert not delivery.receive_event(event(evidence(target(), capture=1)))
    assert delivery.error.code == "WORKER_TIMEOUT"


def test_current_rpc_failure_and_worker_error_clear_outboxes():
    delivery, _ = tracker()
    delivery.keyframe(target(), prefix="load_log")
    load = delivery.take_rpc()
    delivery.queue_target(target(2))
    assert delivery.receive_rpc(response(load, error={"code": "BROKEN", "message": "No worker"}))
    assert (delivery.error.code, delivery.error.status_code) == ("BROKEN", 502)
    assert delivery.take_rpc() is None
    assert delivery.take_target() is None
    delivery.reset("log", 2, 2)
    assert delivery.error is None
    failure = WorkerEvent(session_id="s", seq=5, event={"type": "error", "error": {
        "code": "STOPPED", "message": "Worker stopped"}})
    assert delivery.receive_event(failure)
    assert (delivery.error.code, delivery.error.status_code) == ("STOPPED", 502)


def test_applied_and_rendered_positions_never_regress_and_capture_floor_survives_reset():
    delivery, _ = tracker()
    for seq, time in [(1, 0.0), (2, .5), (3, 1.0)]:
        delivery.queue_target(target(seq, time=time))
        delivery.take_target()
    assert delivery.receive_event(event(evidence(target(3, time=1.0)), seq=1))
    assert not delivery.receive_event(event(evidence(target(2, time=.5)), seq=2))
    assert delivery.receive_event(event(evidence(target(2, time=.5), capture=5), seq=3))
    assert delivery.applied_position.target_seq == 3
    assert delivery.rendered_identity.target_seq == 2
    assert not delivery.receive_event(event(evidence(target(1), capture=6), seq=4))
    assert not delivery.receive_event(event(evidence(target(3, time=1.0), capture=5), seq=5))
    assert delivery.receive_event(event(evidence(target(3, time=1.0), capture=6), seq=6))
    assert delivery.receive_event(event(evidence(target(3, time=1.0), capture=7), seq=7))
    delivery.reset("log", 1, 2)
    assert delivery.applied_position.target_seq == 3
    assert delivery.rendered_identity.capture_id == 7
    delivery.reset("log", 2, 3)
    assert delivery.applied_position is None
    assert delivery.rendered_identity is None
    current = target(4, control=3, epoch=2)
    delivery.keyframe(current)
    request = delivery.take_rpc()
    assert not delivery.receive_rpc(response(request, stage="rendered", frame=evidence(current, capture=7)))
    assert delivery.receive_rpc(response(request, stage="rendered", frame=evidence(current, capture=8)))
    assert delivery.rendered_identity.capture_id == 8


def test_event_sequence_and_session_guard_applies_to_ready_health_and_errors():
    delivery, _ = tracker()
    assert delivery.receive_event(event(seq=5))
    assert not delivery.receive_event(event(seq=5))
    assert not delivery.receive_event(event(seq=4))
    assert not delivery.receive_event(event(seq=100, session_id="foreign"))
    delivery.reset("log", 2, 2)
    assert not delivery.receive_event(event(seq=4))
    health = WorkerEvent(session_id="s", seq=6, event={"type": "health", "render_fps": 60.0, "wall_time_s": 42.0})
    assert delivery.receive_event(health)
    assert delivery.applied_position is None
    assert delivery.rendered_identity is None


def test_issued_ledger_evicts_old_targets_and_stays_bounded():
    delivery, _ = tracker(capacity=3)
    for seq in range(1, 501):
        delivery.queue_target(target(seq))
        delivery.take_target()
    assert delivery.diagnostics()["issued_targets"] == 3
    assert delivery.diagnostics()["queued_targets"] == 0
    assert not delivery.receive_event(event(evidence(target(497)), seq=1))
    assert delivery.receive_event(event(evidence(target(498)), seq=2))
    assert delivery.receive_event(event(evidence(target(500)), seq=3))
    assert delivery.applied_position.target_seq == 500


def test_close_preserves_observed_positions_and_is_permanent():
    delivery, _ = tracker()
    delivery.keyframe(target())
    request = delivery.take_rpc()
    assert delivery.receive_rpc(response(request, stage="rendered", frame=evidence(target(), capture=1)))
    delivery.queue_target(target(2))
    delivery.close()
    assert delivery.applied_position.target_seq == 1
    assert delivery.rendered_identity.capture_id == 1
    assert delivery.take_target() is None
    assert delivery.take_rpc() is None
    assert not delivery.receive_event(event(seq=1))
    assert delivery.diagnostics()["issued_targets"] == 0
    with pytest.raises(ValueError):
        delivery.reset("log", 1, 2)
    with pytest.raises(ValueError):
        delivery.keyframe(target(2))
    with pytest.raises(ValueError):
        delivery.queue_target(target(2))


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("session_id", 7), ("control_seq", True),
    ("target_seq", -1), ("progress", .2), ("joints", (0.0,) * 27),
    ("duration_s", float("nan")),
])
@pytest.mark.parametrize("method", ["keyframe", "queue_target"])
def test_mutated_target_instances_rejected_before_outbox_changes(field, value, method):
    delivery, _ = tracker()
    delivery.keyframe(target())
    bad = target(2)
    object.__setattr__(bad, field, value)
    with pytest.raises(ValueError):
        getattr(delivery, method)(bad)
    request = delivery.take_rpc()
    assert request.command.frame.target_seq == 1
    assert delivery.receive_rpc(response(request, stage="rendered", frame=evidence(target(), capture=1)))
    assert delivery.take_target() is None


@pytest.mark.parametrize("method", ["keyframe", "queue_target"])
@pytest.mark.parametrize("changes", [
    {"control": 2}, {"epoch": 2}, {"log": "other"},
])
def test_wrong_context_target_never_replaces_pending_work(method, changes):
    delivery, _ = tracker()
    delivery.keyframe(target())
    with pytest.raises(ValueError):
        getattr(delivery, method)(target(2, **changes))
    assert delivery.take_rpc().command.frame.target_seq == 1


@pytest.mark.parametrize("field,value", [
    ("raw_identity_verified", False), ("raw_identity_verified", 1),
    ("capture_id", 0), ("render_seq", 2), ("source_joints", (3.0,) * 28),
    ("duration_s", float("inf")), ("control_seq", True),
])
@pytest.mark.parametrize("receipt", ["rpc", "event"])
def test_mutated_receipt_models_are_revalidated_without_crashing(field, value, receipt):
    delivery, _ = tracker()
    delivery.keyframe(target())
    request = delivery.take_rpc()
    rendered = evidence(target(), capture=1)
    packet = response(request, stage="rendered", frame=rendered) if receipt == "rpc" else event(rendered)
    nested = packet.frame if receipt == "rpc" else packet.event.frame
    object.__setattr__(nested, field, value)
    assert not getattr(delivery, f"receive_{receipt}")(packet)
    assert delivery.applied_position is None
    assert delivery.rendered_identity is None
    assert delivery.critical_pending
    assert delivery.receive_rpc(response(request, stage="rendered", frame=evidence(target(), capture=1)))


@pytest.mark.parametrize("bad", [None, object(), [], {}, {"schema_version": 2}])
def test_corrupt_receipt_types_return_false(bad):
    delivery, _ = tracker()
    delivery.keyframe(target())
    delivery.take_rpc()
    assert not delivery.receive_rpc(bad)
    assert not delivery.receive_event(bad)
    assert delivery.critical_pending


def test_missing_mutated_frame_fields_do_not_crash_receive():
    delivery, _ = tracker()
    delivery.keyframe(target())
    request = delivery.take_rpc()
    packet = response(request, stage="rendered", frame=evidence(target(), capture=1))
    object.__delattr__(packet.frame, "capture_id")
    assert not delivery.receive_rpc(packet)
    assert delivery.critical_pending


@pytest.mark.parametrize("changes", [
    {"request_id": "not-issued"}, {"control_seq": 0}, {"control_seq": 2},
    {"playback_epoch": 0}, {"playback_epoch": 2}, {"session_id": "foreign"},
])
def test_foreign_rpc_failure_cannot_fail_current_work(changes):
    delivery, _ = tracker()
    delivery.keyframe(target())
    request = delivery.take_rpc()
    bad = response(request, error={"code": "WRONG", "message": "Must ignore"})
    bad = RPCResponse.model_validate({**bad.model_dump(), **changes})
    assert not delivery.receive_rpc(bad)
    assert delivery.error is None
    assert delivery.critical_pending
    assert delivery.receive_rpc(response(request, stage="rendered", frame=evidence(target(), capture=1)))


def test_load_confirmation_survives_same_log_reset_and_clears_on_log_change():
    delivery, _ = tracker()
    delivery.keyframe(target(), prefix="load_log")
    assert delivery.receive_rpc(response(delivery.take_rpc()))
    delivery.reset("log", 2, 2)
    assert delivery.loaded_log_id == "log"
    delivery.reset("other", 3, 3)
    assert delivery.loaded_log_id is None


@pytest.mark.parametrize("kwargs", [
    {"capacity": 0}, {"capacity": -1}, {"capacity": True}, {"capacity": 1.5},
    {"timeout_s": 0}, {"timeout_s": -1}, {"timeout_s": True},
    {"timeout_s": float("nan")}, {"timeout_s": float("inf")},
    {"session_id": ""}, {"session_id": 3},
])
def test_invalid_configuration_rejected(kwargs):
    module = importlib.import_module("app.replay.delivery")
    with pytest.raises(ValueError):
        module.DeliveryTracker(**{"session_id": "s", **kwargs})


@pytest.mark.parametrize("log,epoch,control", [
    ("", 1, 1), ("log", True, 1), ("log", -1, 1), ("log", 1, True), ("log", 1, 2**53),
])
def test_invalid_reset_does_not_clear_pending(log, epoch, control):
    delivery, _ = tracker()
    delivery.keyframe(target())
    with pytest.raises(ValueError):
        delivery.reset(log, epoch, control)
    assert delivery.take_rpc().command.frame.target_seq == 1


def test_invalid_prefix_does_not_supersede_pending():
    delivery, _ = tracker()
    delivery.keyframe(target())
    with pytest.raises(ValueError):
        delivery.keyframe(target(2), prefix="heartbeat")
    assert delivery.take_rpc().command.frame.target_seq == 1


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "1", -1.0])
def test_invalid_or_regressing_clock_cannot_install_keyframe(bad):
    delivery, clock = tracker()
    clock.now = bad
    with pytest.raises(ValueError):
        delivery.keyframe(target())
    assert not delivery.critical_pending
    assert delivery.diagnostics()["queued_rpc"] == 0


def test_stream_cannot_reissue_or_regress_target_sequence():
    delivery, _ = tracker()
    delivery.queue_target(target(3, time=.5))
    delivery.queue_target(target(2))
    assert delivery.take_target().frame.target_seq == 3
    delivery.queue_target(target(3, time=1.0))
    delivery.queue_target(target(1))
    assert delivery.take_target() is None
    assert delivery.receive_event(event(evidence(target(3, time=.5)), seq=1))
    assert delivery.applied_position.log_time_s == .5


def test_enqueued_and_taken_targets_cannot_mutate_evidence_ledger():
    delivery, _ = tracker()
    frame = target()
    delivery.queue_target(frame)
    object.__setattr__(frame, "joints", (3.0,) * 28)
    message = delivery.take_target()
    assert message.frame.joints == (2.0,) * 28
    object.__setattr__(message.frame, "joints", (4.0,) * 28)
    assert delivery.receive_event(event(evidence(target())))


def test_error_state_prevents_new_outbox_until_reset():
    delivery, _ = tracker()
    delivery.keyframe(target())
    request = delivery.take_rpc()
    assert delivery.receive_rpc(response(request, error={"code": "BAD", "message": "Failed"}))
    with pytest.raises(ValueError):
        delivery.keyframe(target(2))
    with pytest.raises(ValueError):
        delivery.queue_target(target(2))
    assert delivery.diagnostics()["queued_rpc"] == 0


def test_same_context_keyframe_keeps_stream_evidence_without_releasing_critical():
    delivery, _ = tracker()
    delivery.queue_target(target(1, time=.5))
    delivery.take_target()
    delivery.keyframe(target(2, time=1.0), prefix="pause_barrier")
    assert delivery.receive_event(event(evidence(target(1, time=.5), capture=3), seq=1))
    assert delivery.applied_position.log_time_s == .5
    assert delivery.rendered_identity.target_seq == 1
    assert delivery.critical_pending
    pause = delivery.take_rpc()
    assert delivery.receive_rpc(response(pause))
    apply = delivery.take_rpc()
    assert delivery.receive_rpc(response(apply, stage="rendered", frame=evidence(target(2, time=1.0), capture=4)))
    assert delivery.applied_position.log_time_s == 1.0
    assert not delivery.receive_event(event(evidence(target(1, time=.5), capture=5), seq=2))
    assert delivery.rendered_identity.target_seq == 2


def test_prefix_rejects_frame_stages_and_requires_acceptance():
    delivery, _ = tracker()
    delivery.keyframe(target(), prefix="load_log")
    load = delivery.take_rpc()
    assert not delivery.receive_rpc(response(load, stage="rendered", frame=evidence(target(), capture=1)))
    assert delivery.loaded_log_id is None
    assert delivery.applied_position is None
    assert delivery.take_rpc() is None
    assert delivery.receive_rpc(response(load))
    assert delivery.take_rpc().command.cmd == "apply_keyframe"


def test_duplicate_and_reversed_rpc_stages_do_not_change_observations():
    delivery, _ = tracker()
    delivery.keyframe(target())
    request = delivery.take_rpc()
    applied = response(request, stage="applied", frame=evidence(target()))
    assert delivery.receive_rpc(applied)
    assert not delivery.receive_rpc(applied)
    assert not delivery.receive_rpc(response(request))
    assert delivery.critical_pending
    assert delivery.rendered_identity is None
    assert delivery.receive_rpc(response(request, stage="rendered", frame=evidence(target(), capture=1)))


def test_reliable_target_sequence_cannot_be_reused_across_reset():
    delivery, _ = tracker()
    delivery.keyframe(target(4))
    delivery.reset("log", 2, 2)
    with pytest.raises(ValueError):
        delivery.keyframe(target(4, control=2, epoch=2))
    assert not delivery.critical_pending
    delivery.keyframe(target(5, control=2, epoch=2))
    assert delivery.take_rpc().command.frame.target_seq == 5


def test_rpc_outbox_bounds_and_request_counter_survive_many_supersessions():
    delivery, _ = tracker()
    for seq in range(1, 501):
        delivery.reset("log", 1, seq)
        delivery.keyframe(target(seq, control=seq), prefix="load_log")
        before = delivery.diagnostics()
        assert before["pending_rpc"] == 0
        assert before["queued_rpc"] == 2
        delivery.take_rpc()
        after = delivery.diagnostics()
        assert after["pending_rpc"] == 1
        assert after["queued_rpc"] == 1
        assert after["issued_targets"] == 0
    assert after["rpc_requests_created"] == 1000
    assert after["target_seq"] == 500
