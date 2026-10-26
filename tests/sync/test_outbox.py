import asyncio

import pytest


def test_health_coalesces_without_spending_sequence_numbers_and_frames_are_preserved():
    from app.sync.outbox import Outbox
    async def run():
        box = Outbox('sid', capacity=8)
        box.offer('health_telemetry', {'value': 1})
        box.offer('frame_snapshot', {'capture_id': 1})
        box.offer('health_telemetry', {'value': 2})
        box.offer('frame_snapshot', {'capture_id': 2})
        messages = [await box.next() for _ in range(3)]
        assert [m['seq'] for m in messages] == [1, 2, 3]
        assert [m['data']['capture_id'] for m in messages if m['type'] == 'frame_snapshot'] == [1, 2]
        assert [m['data']['value'] for m in messages if m['type'] == 'health_telemetry'] == [2]
    asyncio.run(run())


def test_slow_consumer_is_closed_explicitly_instead_of_silently_losing_frames():
    from app.sync.outbox import Outbox, ConnectionClosed
    async def run():
        box = Outbox('sid', capacity=2)
        for capture in (1, 2, 3):
            box.offer('frame_snapshot', {'capture_id': capture})
        with pytest.raises(ConnectionClosed) as error:
            await box.next()
        assert error.value.code == 1013 and error.value.reason == 'SLOW_CONSUMER'
        assert box.pending_count == 0
    asyncio.run(run())


def test_context_reset_keeps_same_epoch_alerts_but_discards_old_frames_and_epochs():
    from app.sync.outbox import Outbox
    async def run():
        box = Outbox('sid')
        box.offer('frame_snapshot', {'capture_id': 1})
        box.offer('alert', {'log_id': 'log', 'playback_epoch': 1, 'log_time_s': .1})
        box.offer('alert', {'log_id': 'log', 'playback_epoch': 2, 'log_time_s': .2})
        box.offer('pong', {'request_id': 'ping'})
        box.reset({'playback': {'log_id': 'log', 'playback_epoch': 2}})
        messages = [await box.next() for _ in range(3)]
        assert [m['type'] for m in messages] == ['connection_snapshot', 'alert', 'pong']
        assert messages[1]['data']['playback_epoch'] == 2
        other = Outbox('sid')
        assert other.connection_id != box.connection_id
    asyncio.run(run())


def test_resync_preserves_lifecycle_events_in_order_but_replaces_old_sync_notices():
    from app.sync.outbox import Outbox
    async def run():
        box = Outbox('sid')
        for event in ('mediamtx_unreachable', 'sync_resync_required', 'mediamtx_recovered', 'worker_exited'):
            box.offer('system', {'event': event})
        box.reset({'playback': {'log_id': 'log', 'playback_epoch': 2}})
        messages = [await box.next() for _ in range(box.pending_count)]
        assert [message['data']['event'] for message in messages if message['type'] == 'system'] == [
            'mediamtx_unreachable', 'mediamtx_recovered', 'worker_exited']
    asyncio.run(run())


def test_frame_cache_expires_and_rejects_old_control_context_without_using_wall_clock():
    from app.sync.frames import FrameCache
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis'))
    from analysis_support import rendered
    from app.analysis.metadata import load_joint_metadata
    now = [0.]
    cache = FrameCache(load_joint_metadata().specs(), capacity=2, ttl_s=2., clock=lambda: now[0])
    cache.context('s', 'analysis-log', 1, 1)
    for capture in (1, 2, 3):
        assert cache.add(rendered(.5, capture)) is not None
    assert cache.capture_ids == (2, 3)
    cache.context('s', 'analysis-log', 2, 2)
    assert cache.add(rendered(.5, 4)) is None
    frame = cache.add(rendered(.75, 5, epoch=2, control=2))
    assert frame.log_time_s == .75 and frame.state == 'playing' and frame.speed == 10.
    now[0] = 2.
    assert cache.capture_ids == ()
