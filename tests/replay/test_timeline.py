"""Clock/cadence and strict control semantics, without real sleeps."""
import math
import pytest
from app.replay.timeline import Timeline
from app.replay.timeline_base import TimelineError
from timeline_support import ManualClock, FakeWorker, build_sampler, pump


def setup(times=(0.,.5,1.)):
    clock=ManualClock();sampler=build_sampler(times=times)
    timeline=Timeline('s',lambda lid:sampler if lid=='log' else (_ for _ in ()).throw(KeyError(lid)),clock=clock)
    worker=FakeWorker('s',clock)
    return clock,timeline,worker


def ready(times=(0.,.5,1.)):
    clock,t,w=setup(times);t.load('log');pump(t,w);return clock,t,w


def test_default_cadence_publishes_sixty_latest_targets_per_second_without_catchup_bursts():
    clock,t,w=ready((0.,10.))
    t.play();pump(t,w)
    sent=[]
    for i in range(1,121):
        clock.advance(1/120);t.tick()
        if target:=t.take_target():sent.append(target.frame)
    assert len(sent)==60
    assert sent[0].log_time_s==pytest.approx(1/60)
    assert sent[-1].log_time_s==pytest.approx(1.)
    clock.advance(3.);t.tick()
    assert t.take_target().frame.log_time_s==pytest.approx(4.)
    assert t.take_target() is None


def test_speed_changes_anchor_position_under_old_speed_and_preserve_continuity():
    clock,t,w=ready((0.,10.))
    t.play();pump(t,w);clock.advance(1.5)
    changed=t.set_speed(2.);assert changed.target_position.log_time_s==1.5
    pump(t,w);clock.advance(2.)
    paused=t.pause();assert paused.target_position.log_time_s==5.5
    pump(t,w);clock.advance(100.)
    assert t.snapshot().target_position.log_time_s==5.5
    t.set_speed(.1);pump(t,w);t.play();pump(t,w);clock.advance(2.)
    assert t.pause().target_position.log_time_s==pytest.approx(5.7)


def test_pending_critical_render_excludes_wait_time_from_logical_playback():
    clock,t,w=ready((0.,10.))
    accepted=t.play();assert accepted.applied_position.target_seq<accepted.target_position.target_seq
    clock.advance(5.)
    t.tick();assert t.take_target() is None
    assert t.snapshot().target_position.log_time_s==0.
    pump(t,w);clock.advance(.5);t.tick()
    assert t.take_target().frame.log_time_s==.5


def test_frame_and_time_seek_clamp_and_step_use_original_irregular_sample_set():
    clock,t,w=ready((4.,4.2,4.9,6.))
    assert t.seek(frame_index=-500).target_position.source_sample_index==0;pump(t,w)
    end=t.seek(frame_index=500);assert end.target_position.source_sample_index==3 and end.target_position.log_time_s==2.;pump(t,w)
    t.seek(time_s=.5);pump(t,w)
    assert t.step('forward').target_position.log_time_s==pytest.approx(.9);pump(t,w)
    assert t.step('backward').target_position.log_time_s==pytest.approx(.2)


def test_zero_duration_load_and_play_emit_confirmable_single_frame_and_ended_progress():
    clock,t,w=ready((7.,))
    result=t.play();assert result.state=='ended' and result.duration_s==0.
    assert result.target_position.source_sample_index==0
    pump(t,w);clock.advance(1.);t.tick();assert t.take_rpc() is None and t.take_target() is None
    assert t.seek(time_s=100).state=='paused';pump(t,w)
    assert t.play().state=='ended'


@pytest.mark.parametrize('bad',[0,.099,10.001,float('nan'),float('inf'),True,'2'])
def test_invalid_speed_does_not_mutate_state_or_allocate_sequences(bad):
    clock,t,w=ready();before=t.snapshot()
    with pytest.raises(TimelineError) as error:t.set_speed(bad)
    assert error.value.status_code==400 and t.snapshot()==before


@pytest.mark.parametrize('kwargs',[{}, {'time_s':None},{'time_s':0,'frame_index':0}, {'time_s':None,'frame_index':0},
    {'time_s':True},{'time_s':float('nan')},{'time_s':float('inf')},{'frame_index':True},{'frame_index':.5},{'frame_index':2**53}])
def test_seek_validates_shared_xor_and_numeric_contract_before_any_state_change(kwargs):
    clock,t,w=ready();before=t.snapshot()
    with pytest.raises(TimelineError) as error:t.seek(**kwargs)
    assert error.value.status_code==400 and t.snapshot()==before


def test_log_resolution_failure_or_foreign_sampler_does_not_replace_active_log():
    clock,t,w=ready();before=t.snapshot()
    with pytest.raises(TimelineError) as error:t.load('missing')
    assert error.value.status_code==404 and t.snapshot()==before
    foreign=Timeline('s',lambda lid:build_sampler(session='other'),clock=clock)
    with pytest.raises(TimelineError):foreign.load('log')
    assert foreign.snapshot().state=='idle'


def test_regressed_or_nonfinite_clock_is_terminal_and_cannot_resume_controls():
    clock,t,w=ready((0.,10.));t.play();pump(t,w);clock.advance(1.);t.tick()
    clock.advance(-.5)
    assert t.tick().state=='error'
    assert t.error is not None
    with pytest.raises(TimelineError):t.play()
    assert t.take_rpc() is None and t.take_target() is None


def test_snapshot_only_reports_generated_target_not_unissued_clock_projection():
    clock,t,w=ready((0.,10.));t.play();pump(t,w)
    clock.advance(.005)
    assert t.snapshot().target_position.log_time_s==0.
    t.tick();assert t.take_target() is None
