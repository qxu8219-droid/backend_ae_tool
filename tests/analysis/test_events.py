import pytest
from replay_protocol import ChannelQuality
from analysis_support import NAME, metadata, replay, rendered


def test_original_narrow_peak_keeps_entry_exit_and_peak_provenance():
    from app.analysis.events import HistoryIndex
    data = replay([0., 1.2, 3., 0.], times=[0., .1, .11, 1.])
    index = HistoryIndex(data, metadata=metadata())
    events = index.overlapping(0., 1.)
    assert len(events) == 1
    event = events[0]
    assert (event.from_s, event.last_observed_s, event.exit_s) == (.1, .11, 1.)
    assert (event.peak_value, event.peak_time_s, event.peak_quality.source_indices) == (3., .11, (2,))
    assert (event.channel, event.joint_index, event.side, event.severity) == (NAME, 0, 'upper', 'error')
    assert event.raw_file_sha256 == '1' * 64 and event.limits_source_sha256 == 'a' * 64
    assert index.entered_between(0., 1.) == events
    assert not index.overlapping(1., 1.)
    assert index.overlapping(.11, .11) == events


def test_missing_samples_and_declared_long_gaps_do_not_infer_normal_exit():
    from app.analysis.events import HistoryIndex
    data = replay([2., None, 3., 2., 0.], times=[0., 1., 2., 8., 9.])
    events = HistoryIndex(data, metadata=metadata(), max_gap_s=2.).overlapping(0., 9.)
    assert [(e.from_s, e.last_observed_s, e.exit_s, e.closed_by) for e in events] == [
        (0., 0., None, 'missing'), (2., 2., None, 'gap'), (8., 8., 9., 'normal')]


def test_quality_origin_change_splits_observed_events_and_estimates_are_unassessed():
    from app.analysis.events import HistoryIndex
    qualities = [ChannelQuality(origin='measured', valid=True, processing=['original']),
                 ChannelQuality(origin='commanded', valid=True, processing=['original']),
                 ChannelQuality(origin='derived', valid=True, processing=['derived'], method='fixture.estimate')]
    events = HistoryIndex(replay([2., 3., 100.], qualities=qualities), metadata=metadata()).overlapping(0., 2.)
    assert len(events) == 2
    assert [e.peak_quality.origin for e in events] == ['measured', 'commanded']
    assert [e.peak_value for e in events] == [2., 3.]
    assert events[1].exit_s is None


def test_observed_warning_and_error_transitions_are_not_debounced_away():
    from app.analysis.events import HistoryIndex
    index = HistoryIndex(replay([0., .91, 1.2, -.91, -2., 0.]), metadata=metadata())
    events = index.overlapping(0., 5.)
    assert [(e.severity, e.side, e.from_s, e.exit_s) for e in events] == [
        ('warning', 'upper', 1., 2.), ('error', 'upper', 2., 3.),
        ('warning', 'lower', 3., 4.), ('error', 'lower', 4., 5.)]


def test_history_is_stable_relative_to_log_origin_and_isolated_from_caller_mutation():
    from app.analysis.events import HistoryIndex
    data = replay([0., 2., 0.], times=[100., 101., 102.], origin=100.)
    before = data.model_dump_json()
    first = HistoryIndex(data, metadata=metadata())
    second = HistoryIndex(data, metadata=metadata())
    assert first.overlapping(0., 2.) == second.overlapping(0., 2.)
    assert first.overlapping(.5, 1.5)[0].from_s == 1.
    assert data.model_dump_json() == before
    data.channels[NAME].values[1] = 0.
    assert first.overlapping(0., 2.)[0].peak_value == 2.


def test_10x_crossed_events_are_candidates_without_future_peak_or_exit_disclosure():
    from app.analysis.events import HistoryIndex
    from app.analysis.cursor import RenderedEventCursor
    index = HistoryIndex(replay([0., 2., 0., 3., 0.], times=[0., .1, .11, .9, 1.]), metadata=metadata())
    cursor = RenderedEventCursor(index, session_id='s')
    assert cursor.advance(rendered(0., 1)) == ()
    alerts = cursor.advance(rendered(1., 2))
    assert [a.log_time_s for a in alerts] == [.1, .9]
    assert all(a.playback_epoch == 1 for a in alerts)
    assert all('peak_value' not in a.details and 'exit_s' not in a.details for a in alerts)
    assert cursor.advance(rendered(1., 3)) == ()
    assert cursor.advance(rendered(.5, 2)) == ()
    assert len(index.overlapping(0., 1.)) == 2


def test_seek_reanchors_delivery_while_replay_can_emit_same_history_ids_in_new_epoch():
    from app.analysis.events import HistoryIndex
    from app.analysis.cursor import RenderedEventCursor
    index = HistoryIndex(replay([0., 2., 0.]), metadata=metadata())
    cursor = RenderedEventCursor(index, session_id='s')
    cursor.advance(rendered(0., 1))
    old = cursor.advance(rendered(2., 2))[0]
    assert cursor.advance(rendered(1.5, 3, epoch=2)) == ()
    assert cursor.advance(rendered(0., 4, epoch=3)) == ()
    new = cursor.advance(rendered(2., 5, epoch=3))[0]
    assert old.details['history_event_id'] == new.details['history_event_id']
    assert old.alert_id != new.alert_id
    assert len(index.overlapping(0., 2.)) == 1
    assert cursor.advance(rendered(2., 6, epoch=2)) == ()


def test_invalid_ranges_and_identity_do_not_advance_cursor():
    from app.analysis.events import HistoryIndex
    from app.analysis.cursor import RenderedEventCursor
    index = HistoryIndex(replay([0., 2., 0.]), metadata=metadata())
    for start, end in [(2., 1.), (-1., 1.), (False, 1.), (0., float('nan'))]:
        with pytest.raises(ValueError):
            index.overlapping(start, end)
    cursor = RenderedEventCursor(index, session_id='s')
    with pytest.raises(ValueError):
        cursor.advance(rendered(2., 1, sid='other'))
    assert cursor.advance(rendered(0., 1)) == ()
    with pytest.raises(ValueError):
        cursor.advance(rendered(1., 2, log='other'))
    assert len(cursor.advance(rendered(2., 2))) == 1
