"""Independent-review regressions for delivery-install failures and cadence arithmetic."""
import pytest
from app.replay.timeline import Timeline
from app.replay.timeline_base import TimelineError
from timeline_support import ManualClock, FakeWorker, build_sampler, pump


def test_clock_failure_during_keyframe_install_is_terminal_instead_of_leaving_a_usable_partial_load():
    clock=ManualClock();sampler=build_sampler()
    def resolve(log_id):
        clock.now=float('nan')
        return sampler
    timeline=Timeline('s',resolve,clock=clock)
    with pytest.raises(TimelineError) as error:timeline.load('log')
    assert error.value.code=='INVALID_CLOCK'
    assert timeline.snapshot().state=='error'
    assert timeline.error is not None
    clock.now=0.
    with pytest.raises(TimelineError) as blocked:timeline.play()
    assert blocked.value.code=='SESSION_ERROR'
    assert timeline.take_rpc() is None and timeline.take_target() is None


def test_extreme_finite_target_rate_uses_bounded_latest_tick_without_integer_overflow_or_same_clock_repeats():
    clock=ManualClock();sampler=build_sampler(times=(0.,10.))
    timeline=Timeline('s',lambda lid:sampler,clock=clock,target_hz=1e308)
    worker=FakeWorker('s',clock)
    timeline.load('log');pump(timeline,worker);timeline.play();pump(timeline,worker)
    clock.advance(2.)
    state=timeline.tick()
    assert state.state=='playing' and state.target_position.log_time_s==2.
    latest=timeline.take_target();assert latest.frame.log_time_s==2.
    timeline.tick()
    assert timeline.take_target() is None
    clock.advance(.5);timeline.tick()
    assert timeline.take_target().frame.log_time_s==2.5
