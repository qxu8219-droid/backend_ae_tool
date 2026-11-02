"""Independent acceptance table for the public T10 control/state policy.

Every cell starts a fresh session and uses hand-derived expected values.  The
fake worker supplies transport evidence only; the timeline and sampler are real.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.replay.timeline import Timeline
from app.replay.timeline_base import TimelineError
from timeline_support import FakeWorker, ManualClock, build_sampler, pump


@dataclass(frozen=True)
class Cell:
    state: str
    command: str
    expected_state: str
    time_s: float | None
    source_index: int | None
    speed: float
    epoch_delta: int
    error_code: str | None = None


# Columns: initial state, command, resulting state, time, source index, speed,
# epoch increment, optional rejection code.  Do not derive this oracle from
# Timeline's transition table or the sampler's step/seek helpers.
CELLS = (
    Cell("idle", "load", "ready", 0.0, 0, 1.0, 1),
    Cell("idle", "play", "idle", None, None, 1.0, 0, "NO_LOG_LOADED"),
    Cell("idle", "pause", "idle", None, None, 1.0, 0, "NO_LOG_LOADED"),
    Cell("idle", "stop", "idle", None, None, 1.0, 0, "NO_LOG_LOADED"),
    Cell("idle", "seek", "idle", None, None, 1.0, 0, "NO_LOG_LOADED"),
    Cell("idle", "step", "idle", None, None, 1.0, 0, "NO_LOG_LOADED"),
    Cell("idle", "speed", "idle", None, None, 1.0, 0, "NO_LOG_LOADED"),
    Cell("ready", "load", "ready", 0.0, 0, 1.0, 1),
    Cell("ready", "play", "playing", 0.25, None, 1.0, 0),
    Cell("ready", "pause", "paused", 0.25, None, 1.0, 0),
    Cell("ready", "stop", "ready", 0.0, 0, 1.0, 1),
    Cell("ready", "seek", "ready", 0.5, 1, 1.0, 1),
    Cell("ready", "step", "paused", 0.5, 1, 1.0, 1),
    Cell("ready", "speed", "ready", 0.25, None, 2.0, 0),
    Cell("playing", "load", "ready", 0.0, 0, 1.0, 1),
    Cell("playing", "play", "playing", 0.25, None, 1.0, 0),
    Cell("playing", "pause", "paused", 0.25, None, 1.0, 0),
    Cell("playing", "stop", "ready", 0.0, 0, 1.0, 1),
    Cell("playing", "seek", "playing", 0.5, 1, 1.0, 1),
    Cell("playing", "step", "paused", 0.5, 1, 1.0, 1),
    Cell("playing", "speed", "playing", 0.25, None, 2.0, 0),
    Cell("paused", "load", "ready", 0.0, 0, 1.0, 1),
    Cell("paused", "play", "playing", 0.25, None, 1.0, 0),
    Cell("paused", "pause", "paused", 0.25, None, 1.0, 0),
    Cell("paused", "stop", "ready", 0.0, 0, 1.0, 1),
    Cell("paused", "seek", "paused", 0.5, 1, 1.0, 1),
    Cell("paused", "step", "paused", 0.5, 1, 1.0, 1),
    Cell("paused", "speed", "paused", 0.25, None, 2.0, 0),
    Cell("ended", "load", "ready", 0.0, 0, 1.0, 1),
    Cell("ended", "play", "playing", 0.0, 0, 1.0, 1),
    Cell("ended", "pause", "paused", 1.0, 2, 1.0, 0),
    Cell("ended", "stop", "ready", 0.0, 0, 1.0, 1),
    Cell("ended", "seek", "paused", 0.5, 1, 1.0, 1),
    Cell("ended", "step", "paused", 1.0, 2, 1.0, 1),
    Cell("ended", "speed", "ended", 1.0, 2, 2.0, 0),
    Cell("error", "load", "error", 0.25, None, 1.0, 0, "SESSION_ERROR"),
    Cell("error", "play", "error", 0.25, None, 1.0, 0, "SESSION_ERROR"),
    Cell("error", "pause", "error", 0.25, None, 1.0, 0, "SESSION_ERROR"),
    Cell("error", "stop", "error", 0.25, None, 1.0, 0, "SESSION_ERROR"),
    Cell("error", "seek", "error", 0.25, None, 1.0, 0, "SESSION_ERROR"),
    Cell("error", "step", "error", 0.25, None, 1.0, 0, "SESSION_ERROR"),
    Cell("error", "speed", "error", 0.25, None, 1.0, 0, "SESSION_ERROR"),
)


def _fresh_state(state: str):
    """Reach states through public controls, never by changing private fields."""
    clock = ManualClock()
    samplers = {
        "log": build_sampler(session="s", log_id="log"),
        "other": build_sampler(session="s", log_id="other"),
    }
    timeline = Timeline("s", samplers.__getitem__, clock=clock)
    worker = FakeWorker("s", clock)
    if state != "idle":
        timeline.load("log")
        pump(timeline, worker)
        timeline.seek(time_s=0.25)
        pump(timeline, worker)
        if state in {"playing", "ended"}:
            timeline.play()
            pump(timeline, worker)
        if state == "paused":
            timeline.pause()
            pump(timeline, worker)
        if state == "ended":
            clock.advance(0.75)
            timeline.tick()
            pump(timeline, worker)
        if state == "error":
            timeline.worker_failed("TEST_WORKER_FAILED", "Synthetic worker failure")
    assert timeline.snapshot().state == state
    return timeline, worker


def _command(timeline: Timeline, name: str):
    if name == "load":
        return timeline.load("other")
    if name == "play":
        return timeline.play()
    if name == "pause":
        return timeline.pause()
    if name == "stop":
        return timeline.stop()
    if name == "seek":
        return timeline.seek(time_s=0.5)
    if name == "step":
        return timeline.step("forward")
    if name == "speed":
        return timeline.set_speed(2.0)
    raise AssertionError(f"Unknown test command: {name}")


@pytest.mark.parametrize("cell", CELLS, ids=lambda cell: f"{cell.state}-{cell.command}")
def test_state_command_matrix(cell: Cell):
    """Wrong transition branches or missing control/epoch barriers fail a cell."""
    timeline, worker = _fresh_state(cell.state)
    before = timeline.snapshot()

    if cell.error_code:
        with pytest.raises(TimelineError) as raised:
            _command(timeline, cell.command)
        assert raised.value.code == cell.error_code
        assert raised.value.status_code == 409
        assert timeline.snapshot() == before
        assert timeline.take_rpc() is None
        assert timeline.take_target() is None
        return

    _command(timeline, cell.command)
    accepted = timeline.snapshot()
    assert accepted.state == cell.expected_state
    assert accepted.speed == cell.speed
    assert accepted.control_seq == before.control_seq + 1
    assert accepted.playback_epoch == before.playback_epoch + cell.epoch_delta
    assert accepted.log_id == ("other" if cell.command == "load" else "log")
    assert accepted.target_position is not None
    assert accepted.target_position.log_time_s == pytest.approx(cell.time_s)
    assert accepted.target_position.source_sample_index == cell.source_index
    if before.target_position is not None:
        assert accepted.target_position.target_seq > before.target_position.target_seq

    if cell.epoch_delta:
        assert accepted.applied_position is None
        assert accepted.rendered_identity is None
    elif accepted.applied_position is not None:
        # Acceptance alone cannot claim that the new target has been applied.
        assert accepted.applied_position.target_seq < accepted.target_position.target_seq

    observed = pump(timeline, worker)
    assert observed.state == cell.expected_state
    assert observed.control_seq == accepted.control_seq
    assert observed.playback_epoch == accepted.playback_epoch
    assert observed.target_position == accepted.target_position
    assert observed.applied_position == accepted.target_position
    assert observed.rendered_identity is not None
    assert observed.rendered_identity.target_seq == accepted.target_position.target_seq
    assert observed.rendered_identity.control_seq == accepted.control_seq


@pytest.mark.parametrize(
    "initial_state,command,expected_state,expected_time,expected_speed,epoch_delta",
    (
        ("ready", "load", "ready", 0.0, 1.0, 1),
        ("playing", "play", "playing", 0.25, 1.0, 0),
        ("paused", "pause", "paused", 0.25, 1.0, 0),
        ("ready", "stop", "ready", 0.0, 1.0, 1),
        ("ready", "seek", "ready", 0.5, 1.0, 1),
        ("ended", "step", "paused", 1.0, 1.0, 1),
        ("paused", "speed", "paused", 0.25, 2.0, 0),
    ),
    ids=("load", "play", "pause", "stop", "seek", "step-at-end", "speed"),
)
def test_repeated_controls_keep_pose_but_issue_new_sequence_barriers(
    initial_state, command, expected_state, expected_time, expected_speed, epoch_delta
):
    """Physical no-ops remain accepted controls with fresh reliable targets."""
    timeline, worker = _fresh_state(initial_state)
    _command(timeline, command)
    first = pump(timeline, worker)

    _command(timeline, command)
    repeated = timeline.snapshot()
    assert repeated.state == expected_state
    assert repeated.speed == expected_speed
    assert repeated.target_position.log_time_s == pytest.approx(expected_time)
    assert repeated.control_seq == first.control_seq + 1
    assert repeated.playback_epoch == first.playback_epoch + epoch_delta
    assert repeated.target_position.target_seq > first.target_position.target_seq
    if epoch_delta:
        assert repeated.applied_position is None
        assert repeated.rendered_identity is None

    observed = pump(timeline, worker)
    assert observed.state == expected_state
    assert observed.applied_position == repeated.target_position
    assert observed.rendered_identity.target_seq == repeated.target_position.target_seq
    assert observed.rendered_identity.capture_id > first.rendered_identity.capture_id
