"""Resource evidence math against synthetic /proc and NVIDIA XML, never hardware."""
import importlib
import shutil
import subprocess
from types import SimpleNamespace

import pytest


def resources():
    assert importlib.util.find_spec('scripts.validation_resources') is not None, 'resource sampler is missing'
    return importlib.import_module('scripts.validation_resources')


def write_process(proc, pid, *, parent=1, start=100, cpu=0, rss=256,
                  threads=2, name='python', command='python app.py'):
    directory = proc / str(pid)
    directory.mkdir(exist_ok=True)
    # Fields 3..24 from proc(5); CPU is utime+stime and RSS is pages.
    fields = ['S', str(parent), str(pid), '0', '0', '0', '0', '0', '0', '0', '0',
              str(cpu), '0', '0', '0', '20', '0', str(threads), '0', str(start), '0', str(rss)]
    (directory / 'stat').write_text(f'{pid} ({name}) ' + ' '.join(fields))
    (directory / 'cmdline').write_bytes(command.replace(' ', '\0').encode())
    (directory / 'fd').mkdir(exist_ok=True)
    for fd in ('0', '1', '2'):
        (directory / 'fd' / fd).touch()


def setup_sampler(tmp_path, monkeypatch, roots=(10,), roles=None):
    module = resources()
    (tmp_path / 'meminfo').write_text('MemTotal:       8388608 kB\n')
    clock = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'time', lambda: 1700000000 + clock[0])
    monkeypatch.setattr(module.os, 'cpu_count', lambda: 8)
    monkeypatch.setattr(module.os, 'sysconf', lambda key: {'SC_CLK_TCK': 100, 'SC_PAGE_SIZE': 4096}[key])
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('nvidia-smi unavailable')
    monkeypatch.setattr(module.subprocess, 'run', unavailable)
    return module.ResourceSampler(roots, roles=roles, proc_root=tmp_path), clock


def test_cpu_math_and_descendants_are_counted_once(tmp_path, monkeypatch):
    write_process(tmp_path, 10, cpu=100, name='python (api)', command='uvicorn app.main:app')
    write_process(tmp_path, 11, parent=10, cpu=50, rss=512, command='python -m sim_worker.main')
    write_process(tmp_path, 12, parent=11, cpu=10, rss=128, command='mediamtx config.yml')
    write_process(tmp_path, 99, cpu=9000, rss=9000)
    sampler, clock = setup_sampler(tmp_path, monkeypatch, roots=(10, 11), roles={10: 'api', 11: 'worker'})
    first = sampler.sample()
    assert first['total']['rss_mib'] == 3.5
    assert first['total']['cpu_percent_one_core'] is None
    assert first['system']['memory_total_mib'] == 8192
    assert first['system']['cpu_count'] == 8
    assert first['total']['threads'] == 6
    assert first['total']['fds'] == 9
    clock[0] = 105
    write_process(tmp_path, 10, cpu=600, command='uvicorn app.main:app')
    write_process(tmp_path, 11, parent=10, cpu=300, rss=512, command='python -m sim_worker.main')
    write_process(tmp_path, 12, parent=11, cpu=135, rss=128, command='mediamtx config.yml')
    second = sampler.sample()
    assert second['elapsed_s'] == 5
    assert second['total']['cpu_ticks'] == 1035
    assert second['total']['cpu_seconds'] == 10.35
    assert second['total']['cpu_percent_one_core'] == 175
    assert second['total']['cpu_percent_machine'] == 21.875
    assert {p['pid'] for p in second['processes']} == {10, 11, 12}
    assert {p['pid']: p['root_pid'] for p in second['processes']} == {10: 10, 11: 11, 12: 11}
    assert {p['pid']: p['role'] for p in second['processes']} == {10: 'api', 11: 'worker', 12: 'mediamtx'}
    assert second['timestamp_utc'].endswith('+00:00')


def test_descendant_stays_owned_when_reparented_and_pid_reuse_is_excluded(tmp_path, monkeypatch):
    write_process(tmp_path, 10)
    write_process(tmp_path, 11, parent=10, start=110)
    sampler, clock = setup_sampler(tmp_path, monkeypatch)
    sampler.sample()
    shutil.rmtree(tmp_path / '10')
    write_process(tmp_path, 11, parent=1, start=110, cpu=500)
    write_process(tmp_path, 12, parent=11, start=120)
    clock[0] += 5
    second = sampler.sample()
    assert {p['pid'] for p in second['processes']} == {11, 12}
    assert second['owned_descendant_pids'] == [11, 12]
    # Both configured-root and already-observed-descendant PID reuse are excluded.
    write_process(tmp_path, 10, start=999, cpu=100000)
    write_process(tmp_path, 11, start=998, cpu=100000)
    shutil.rmtree(tmp_path / '12')
    clock[0] += 5
    third = sampler.sample()
    assert third['processes'] == []
    assert third['total']['rss_mib'] == 0
    assert {(p['pid'], p['start_time_ticks']) for p in third['owned_processes']} == {(10, 100), (11, 110), (12, 120)}


def test_missing_root_is_never_adopted_later(tmp_path, monkeypatch):
    sampler, clock = setup_sampler(tmp_path, monkeypatch)
    write_process(tmp_path, 10, start=999)
    assert sampler.sample()['processes'] == []


def test_exited_process_marks_group_cpu_incomplete_but_keeps_observed_cumulative_cpu(tmp_path, monkeypatch):
    write_process(tmp_path, 10, cpu=100)
    write_process(tmp_path, 11, parent=10, cpu=200)
    sampler, clock = setup_sampler(tmp_path, monkeypatch, roles={10: 'api'})
    sampler.sample()
    shutil.rmtree(tmp_path / '11')
    write_process(tmp_path, 10, cpu=150)
    clock[0] += 5
    sample = sampler.sample()
    assert sample['total']['cpu_percent_one_core'] == 10
    assert sample['total']['observed_cumulative_cpu_seconds'] == 3.5
    assert sample['total']['cpu_interval_complete'] is False
    assert sample['groups'][0]['cpu_interval_complete'] is False


def test_unreadable_fds_and_malformed_process_do_not_drop_valid_counter_data(tmp_path, monkeypatch):
    write_process(tmp_path, 10, cpu=100)
    write_process(tmp_path, 11, parent=10)
    sampler, clock = setup_sampler(tmp_path, monkeypatch)
    shutil.rmtree(tmp_path / '10' / 'fd')
    (tmp_path / '11' / 'stat').write_text('incomplete')
    sample = sampler.sample()
    assert sample['total']['rss_mib'] == 1
    assert sample['total']['fds'] is None
    assert sample['total']['cpu_seconds'] == 1


def test_new_child_uses_lifetime_cpu_only_if_born_during_interval(tmp_path, monkeypatch):
    (tmp_path / 'uptime').write_text('1000.0 0.0\n')
    write_process(tmp_path, 10, start=90000, cpu=100)
    sampler, clock = setup_sampler(tmp_path, monkeypatch)
    sampler.sample()
    clock[0] += 5
    (tmp_path / 'uptime').write_text('1005.0 0.0\n')
    write_process(tmp_path, 11, parent=10, start=100100, cpu=200)
    second = sampler.sample()
    child = next(p for p in second['processes'] if p['pid'] == 11)
    assert child['cpu_percent_one_core'] == 40
    assert second['total']['cpu_percent_one_core'] == 40


GPU_XML = '''<nvidia_smi_log><gpu id="0000:01:00.0"><uuid>GPU-one</uuid>
<fb_memory_usage><total>24576 MiB</total><used>1500 MiB</used></fb_memory_usage>
<utilization><gpu_util>83 %</gpu_util></utilization><processes>
<process_info><pid>10</pid><type>C+G</type><used_memory>400 MiB</used_memory></process_info>
<process_info><pid>99</pid><type>C</type><used_memory>1000 MiB</used_memory></process_info>
</processes></gpu></nvidia_smi_log>'''


def test_gpu_vram_only_charges_owned_identities_and_keeps_device_context(tmp_path, monkeypatch):
    write_process(tmp_path, 10)
    sampler, clock = setup_sampler(tmp_path, monkeypatch)
    module = resources()
    def query(command, **kwargs):
        assert command == ['nvidia-smi', '-q', '-x']
        assert 0 < kwargs['timeout'] <= 1
        return SimpleNamespace(returncode=0, stdout=GPU_XML, stderr='')
    monkeypatch.setattr(module.subprocess, 'run', query)
    gpu = sampler.sample()['gpu']
    assert gpu['status'] == 'ok'
    assert gpu['owned_vram_mib'] == 400
    assert [p['pid'] for p in gpu['owned_processes']] == [10]
    assert gpu['devices'][0]['memory_used_mib'] == 1500
    assert gpu['devices'][0]['utilization_percent'] == 83


@pytest.mark.parametrize('outcome', ['missing', 'timeout', 'xml', 'exit'])
def test_gpu_failure_is_explicit_and_does_not_abort_resource_sample(tmp_path, monkeypatch, outcome):
    write_process(tmp_path, 10)
    sampler, clock = setup_sampler(tmp_path, monkeypatch)
    module = resources()
    def query(command, **kwargs):
        if outcome == 'missing':
            raise FileNotFoundError('missing')
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        return SimpleNamespace(returncode=0 if outcome == 'xml' else 1, stdout='invalid XML', stderr='query failed')
    monkeypatch.setattr(module.subprocess, 'run', query)
    sample = sampler.sample()
    assert sample['total']['rss_mib'] == 1
    assert sample['gpu']['status'] == 'unavailable'
    assert sample['gpu']['owned_vram_mib'] is None
    assert sample['gpu']['error']


def test_gpu_unknown_memory_and_pid_reuse_are_not_reported_as_zero(tmp_path, monkeypatch):
    write_process(tmp_path, 10)
    sampler, clock = setup_sampler(tmp_path, monkeypatch)
    module = resources()
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=GPU_XML.replace('400 MiB', 'N/A'), stderr=''))
    gpu = sampler.sample()['gpu']
    assert gpu['owned_vram_mib'] is None
    assert gpu['owned_processes'][0]['memory_used_mib'] is None
    def query(*args, **kwargs):
        write_process(tmp_path, 10, start=999)
        return SimpleNamespace(returncode=0, stdout=GPU_XML, stderr='')
    monkeypatch.setattr(module.subprocess, 'run', query)
    assert sampler.sample()['gpu']['owned_processes'] == []


def test_unsupported_gpu_process_accounting_is_unknown_not_zero(tmp_path, monkeypatch):
    write_process(tmp_path, 10)
    sampler, clock = setup_sampler(tmp_path, monkeypatch)
    module = resources()
    xml = '<nvidia_smi_log><gpu><uuid>GPU-one</uuid><processes>N/A</processes></gpu></nvidia_smi_log>'
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=xml, stderr=''))
    gpu = sampler.sample()['gpu']
    assert gpu['status'] == 'partial'
    assert gpu['owned_vram_mib'] is None
    assert gpu['process_accounting_available'] is False


def test_summary_uses_separate_stable_windows_and_reports_literal_rss_slope():
    module = resources()
    # Warmup peak is preserved, stable windows [60,120), (120,180].
    samples = [
        {'monotonic_s': t, 'total': {'rss_mib': rss}, 'groups': [], 'processes': []}
        for t, rss in [(0, 200), (30, 50), (60, 100), (90, 102), (120, 104), (150, 106), (180, 108)]
    ]
    summary = module.summarize_resources(samples, stable_after_s=60, window_s=60)
    rss = summary['rss']['total']
    assert rss['peak_mib'] == 200
    assert rss['first_stable_window']['mean_mib'] == 101
    assert rss['last_stable_window']['mean_mib'] == 107
    assert rss['stable_delta_mib'] == 6
    assert rss['stable_slope_mib_per_min'] == pytest.approx(4)
    assert summary['window_status'] == 'available'
    assert 'passed' not in summary


def test_short_summary_exposes_insufficient_windows_without_reusing_same_samples():
    module = resources()
    summary = module.summarize_resources([
        {'monotonic_s': t, 'total': {'rss_mib': rss}, 'groups': [], 'processes': []}
        for t, rss in [(0, 10), (5, 12), (10, 14)]
    ])
    assert summary['window_status'] == 'insufficient_duration'
    assert summary['rss']['total']['first_mib'] == 10
    assert summary['rss']['total']['last_mib'] == 14
    assert summary['rss']['total']['delta_mib'] == 4
    assert summary['rss']['total']['stable_delta_mib'] is None
    assert module.summarize_resources([])['sample_count'] == 0


def test_summary_flags_missing_stable_window_samples():
    summary = resources().summarize_resources([
        {'monotonic_s': t, 'total': {'rss_mib': 10}, 'groups': [], 'processes': []}
        for t in (0, 180)
    ])
    assert summary['window_status'] == 'insufficient_samples'
    assert summary['rss']['total']['stable_delta_mib'] is None


def test_browser_driver_overhead_is_separate_from_chrome(tmp_path, monkeypatch):
    write_process(tmp_path, 10, command='node node_modules/playwright/cli.js test')
    write_process(tmp_path, 11, parent=10, command='/opt/google/chrome/chrome --type=renderer')
    write_process(tmp_path, 12, parent=10, command='node workerMain.js')
    sampler, _ = setup_sampler(tmp_path, monkeypatch, roles={10: 'browser'})
    sample = sampler.sample()
    assert {p['pid']: p['role'] for p in sample['processes']} == {10: 'browser_driver', 11: 'browser', 12: 'browser_driver'}
    assert len(sample['groups']) == 1 and sample['groups'][0]['root_role'] == 'browser'
