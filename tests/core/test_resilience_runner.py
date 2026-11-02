import importlib.util
from pathlib import Path

import pytest


def runner():
    path = Path(__file__).resolve().parents[2] / 'scripts/validate_resilience.py'
    assert path.is_file(), 'T17 owned-process runner is missing'
    spec = importlib.util.spec_from_file_location('t17_runner', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evidence():
    return {'passed': True, 'mode': 'soak', 'soak': {'requestedSeconds': 1800, 'elapsedSeconds': 1800,
        'healthy': {'presentedFps': 19., 'maxCaptureAgeMs': 200.}}}


def test_smoke_never_counts_as_30_minute_acceptance():
    module = runner()
    report = evidence(); report['soak'].update(requestedSeconds=10, elapsedSeconds=11)
    result = module.assess_browser(report, mode='soak', seconds=10)
    assert result == {'passed': True, 'acceptance': 'smoke_only', 'errors': []}
    result = module.assess_browser(report, mode='soak', seconds=1800)
    assert not result['passed'] and result['acceptance'] == 'failed'


@pytest.mark.parametrize('kind', ['unpassed', 'fps', 'latency', 'nan', 'short', 'wrong_mode', 'missing'])
def test_formal_gate_rejects_missing_failed_or_unhealthy_measurements(kind):
    module = runner(); report = evidence()
    if kind == 'unpassed': report['passed'] = False
    elif kind == 'fps': report['soak']['healthy']['presentedFps'] = 14.99
    elif kind == 'latency': report['soak']['healthy']['maxCaptureAgeMs'] = 2000
    elif kind == 'nan': report['soak']['healthy']['presentedFps'] = float('nan')
    elif kind == 'short': report['soak']['elapsedSeconds'] = 1799.99
    elif kind == 'wrong_mode': report['mode'] = 'matrix'
    else: report.pop('soak')
    result = module.assess_browser(report, mode='soak', seconds=1800)
    assert result['passed'] is False and result['errors']


def test_formal_gate_distinguishes_soak_and_matrix():
    module = runner()
    assert module.assess_browser(evidence(), mode='soak', seconds=1800)['acceptance'] == 'soak_30_minutes'
    assert module.assess_browser({'passed': True, 'mode': 'matrix'}, mode='matrix', seconds=1800)['acceptance'] == 'matrix'


def test_soak_resources_exclude_startup_and_teardown():
    module = runner()
    samples = [{'wall_time_s': value} for value in (99., 100., 105., 1800., 1900., 1901.)]
    browser = {'soak': {'startedAt': '1970-01-01T00:01:40Z', 'endedAt': '1970-01-01T00:31:40Z'}}
    assert module.measured_resources(samples, browser, mode='soak') == samples[1:5]
    assert module.measured_resources(samples, browser, mode='matrix') == samples
    with pytest.raises(ValueError): module.measured_resources(samples, {}, mode='soak')


def test_matrix_requires_recorded_frame_analysis_not_just_browser_success():
    module = runner()
    browser = {'passed': True, 'cleanupAcknowledged': True}
    assert not module.recording_passed(browser, {})
    assert not module.recording_passed(browser, {'active_window': {'passed': False}})
    analysis = {'active_window': {'passed': True, 'duration_s': 10., 'decoded_frames': 250,
        'counts': {'matched': 250, 'panel_mismatch': 0, 'unreadable_stamp': 0}}}
    assert module.recording_passed(browser, analysis)
    analysis['active_window']['counts']['panel_mismatch'] = 1
    assert not module.recording_passed(browser, analysis)


def test_recording_only_is_not_reported_as_complete_matrix():
    module = runner()
    report = {'passed': True, 'mode': 'recording'}
    result = module.assess_browser(report, mode='recording', seconds=1800)
    assert result['acceptance'] == 'recording'
