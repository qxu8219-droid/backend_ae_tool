"""Read-only Linux resource evidence for explicitly owned process trees.

ResourceSampler pins root identities at construction, so construct it after all
roots have started. Sampling never signals processes. Cleanup callers must
recheck each recorded (pid, start_time_ticks), never signal a bare historical PID.
CPU is user+system time (not child CPU, which would count descendants twice).
100% one-core means one CPU fully occupied; machine percent divides by cpu_count.
RSS sums resident pages and can count shared pages in multiple processes.
Processes born and exited between samples are not observable. CPU interval
totals are lower bounds when a process exits or lacks a previous observation.
"""
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import statistics
import subprocess
import time
import xml.etree.ElementTree as ET


def _number(value):
    try:
        result = float(value.split()[0])
        return result if math.isfinite(result) and result >= 0 else None
    except (AttributeError, IndexError, ValueError):
        return None


def _stat(proc_root, pid):
    try:
        value = (proc_root / str(pid) / 'stat').read_text()
        prefix, suffix = value.rsplit(')', 1)
        fields = suffix.split()
        return {'pid': pid, 'name': prefix.split('(', 1)[1], 'state': fields[0],
                'ppid': int(fields[1]), 'process_group': int(fields[2]),
                'cpu_ticks': int(fields[11]) + int(fields[12]),
                'threads': int(fields[17]), 'start_time_ticks': int(fields[19]),
                'rss_pages': max(0, int(fields[21]))}
    except (OSError, ValueError, IndexError):
        return None


def _role(command, default):
    text = command.lower()
    if 'mediamtx' in text:
        return 'mediamtx'
    if any(token in text for token in ('sim_worker', 'isaacsim', 'isaac-sim', 'isaac_sim')):
        return 'worker'
    if 'chrome' in text or 'chromium' in text:
        return 'browser'
    if default == 'browser':
        return 'browser_driver'
    if 'vite' in text:
        return 'vite'
    return default


def _totals(processes, has_interval):
    values = list(processes)
    cpu = [p['cpu_percent_one_core'] for p in values if p['cpu_percent_one_core'] is not None]
    machine = [p['cpu_percent_machine'] for p in values if p['cpu_percent_machine'] is not None]
    return {'process_count': len(values), 'rss_mib': sum(p['rss_mib'] for p in values),
            'threads': sum(p['threads'] for p in values),
            'fds': sum(p['fds'] for p in values) if all(p['fds'] is not None for p in values) else None,
            'cpu_ticks': sum(p['cpu_ticks'] for p in values),
            'cpu_seconds': sum(p['cpu_seconds'] for p in values),
            'cpu_percent_one_core': sum(cpu) if cpu or has_interval and not values else None,
            'cpu_percent_machine': sum(machine) if machine or has_interval and not values else None,
            'cpu_interval_complete': has_interval and len(cpu) == len(values)}


class ResourceSampler:
    def __init__(self, roots, *, roles=None, proc_root='/proc'):
        self.proc_root = Path(proc_root)
        self.cpu_count = os.cpu_count() or 1
        self.ticks_per_second = os.sysconf('SC_CLK_TCK')
        self.page_size = os.sysconf('SC_PAGE_SIZE')
        self.roots = {int(pid): _stat(self.proc_root, int(pid)) for pid in roots}
        self.roles = {int(pid): role for pid, role in (roles or {}).items()}
        self._owned = {}
        self._previous = {}
        self._previous_time = None
        self._previous_uptime = None
        self._observed_cpu_ticks = {}
        for pid, info in self.roots.items():
            if info is not None:
                self._remember(info, pid)

    def _remember(self, info, root_pid):
        key = (info['pid'], info['start_time_ticks'])
        self._owned[key] = {'pid': key[0], 'start_time_ticks': key[1],
                            'root_pid': root_pid, 'root_role': self.roles.get(root_pid, f'root-{root_pid}')}

    def _system(self):
        total = None
        try:
            for line in (self.proc_root / 'meminfo').read_text().splitlines():
                if line.startswith('MemTotal:'):
                    value = _number(line.split(':', 1)[1])
                    total = value / 1024 if value is not None else None
                    break
        except OSError:
            pass
        try:
            uptime = _number((self.proc_root / 'uptime').read_text())
        except OSError:
            uptime = None
        return {'cpu_count': self.cpu_count, 'ticks_per_second': self.ticks_per_second,
                'page_size_bytes': self.page_size, 'memory_total_mib': total, 'uptime_s': uptime}

    def _scan(self):
        infos = {}
        for path in self.proc_root.iterdir():
            if path.name.isdecimal() and (info := _stat(self.proc_root, int(path.name))):
                infos[info['pid']] = info
        owned = {}
        for pid, info in infos.items():
            identity = (pid, info['start_time_ticks'])
            if identity in self._owned:
                owned[pid] = self._owned[identity]['root_pid']
        # Pin explicit nested roots before traversing, then attach each child once.
        pending = True
        while pending:
            pending = False
            for pid, info in infos.items():
                if pid in owned or pid in self.roots or info['ppid'] not in owned:
                    continue
                parent = infos[info['ppid']]
                if info['start_time_ticks'] < parent['start_time_ticks']:
                    continue
                owned[pid] = owned[info['ppid']]
                self._remember(info, owned[pid])
                pending = True
        return [(infos[pid], root) for pid, root in sorted(owned.items()) if infos[pid]['state'] != 'Z']

    def _gpu(self, processes):
        unavailable = {'status': 'unavailable', 'devices': [], 'owned_processes': [],
                       'owned_vram_mib': None, 'process_accounting_available': False}
        try:
            result = subprocess.run(['nvidia-smi', '-q', '-x'], capture_output=True,
                                    text=True, timeout=1.0, check=False)
            if result.returncode:
                raise ValueError(f'nvidia-smi exit {result.returncode}: {result.stderr.strip()[:200]}')
            tree = ET.fromstring(result.stdout)
            if tree.tag != 'nvidia_smi_log':
                raise ValueError('Unrecognized nvidia-smi XML')
        except (OSError, subprocess.TimeoutExpired, ET.ParseError, ValueError) as exc:
            return dict(unavailable, error=f'{type(exc).__name__}: {exc}')
        identities = {p['pid']: p for p in processes}
        devices, owned = [], []
        accounting_available = True
        for gpu in tree.findall('gpu'):
            uuid = gpu.findtext('uuid') or gpu.get('id')
            devices.append({'uuid': uuid,
                            'memory_total_mib': _number(gpu.findtext('fb_memory_usage/total')),
                            'memory_used_mib': _number(gpu.findtext('fb_memory_usage/used')),
                            'utilization_percent': _number(gpu.findtext('utilization/gpu_util'))})
            accounting = gpu.find('processes')
            if accounting is None or (accounting.text or '').strip().lower() not in ('', 'none'):
                accounting_available = False
            for process in gpu.findall('processes/process_info'):
                try:
                    pid = int(process.findtext('pid'))
                except (TypeError, ValueError):
                    continue
                owner = identities.get(pid)
                current = _stat(self.proc_root, pid) if owner else None
                if not current or current['start_time_ticks'] != owner['start_time_ticks']:
                    continue
                owned.append({'pid': pid, 'start_time_ticks': owner['start_time_ticks'],
                              'root_pid': owner['root_pid'], 'role': owner['role'], 'gpu_uuid': uuid,
                              'type': process.findtext('type'),
                              'memory_used_mib': _number(process.findtext('used_memory'))})
        return {'status': 'ok' if accounting_available else 'partial', 'devices': devices, 'owned_processes': owned,
                'process_accounting_available': accounting_available,
                'owned_vram_mib': sum(p['memory_used_mib'] for p in owned)
                if accounting_available and all(p['memory_used_mib'] is not None for p in owned) else None,
                'utilization_scope': 'whole GPU, includes unrelated processes',
                'vram_scope': 'only owned PID/start-time identities reported by nvidia-smi; driver/context overhead is not attributed'}

    def sample(self):
        now, wall = time.monotonic(), time.time()
        elapsed = now - self._previous_time if self._previous_time is not None else None
        has_interval = elapsed is not None and elapsed > 0
        system = self._system()
        processes = []
        for info, root in self._scan():
            pid, start = info['pid'], info['start_time_ticks']
            directory = self.proc_root / str(pid)
            try:
                command = (directory / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace').strip()
            except OSError:
                command = info['name']
            try:
                fds = sum(1 for _ in (directory / 'fd').iterdir())
            except OSError:
                fds = None
            current = _stat(self.proc_root, pid)
            if current is None or current['start_time_ticks'] != start or current['state'] == 'Z':
                continue
            identity = (pid, start)
            previous_ticks = self._previous.get(identity)
            if (previous_ticks is None and has_interval and self._previous_uptime is not None
                    and system['uptime_s'] is not None
                    and self._previous_uptime <= start / self.ticks_per_second <= system['uptime_s']):
                previous_ticks = 0
            delta = info['cpu_ticks'] - previous_ticks if previous_ticks is not None else None
            cpu = 100 * delta / self.ticks_per_second / elapsed if has_interval and delta is not None and delta >= 0 else None
            root_role = self.roles.get(root, f'root-{root}')
            role = _role(command, root_role)
            self._owned[identity]['role'] = role
            processes.append(dict(info, root_pid=root, root_role=root_role,
                                  role=role,
                                  cpu_seconds=info['cpu_ticks'] / self.ticks_per_second,
                                  cpu_percent_one_core=cpu,
                                  cpu_percent_machine=cpu / self.cpu_count if cpu is not None else None,
                                  rss_mib=info['rss_pages'] * self.page_size / 1048576,
                                  fds=fds))
            self._observed_cpu_ticks[identity] = max(info['cpu_ticks'], self._observed_cpu_ticks.get(identity, 0))
        previous_identities = set(self._previous)
        self._previous = {(p['pid'], p['start_time_ticks']): p['cpu_ticks'] for p in processes}
        lost = previous_identities - self._previous.keys()
        self._previous_time, self._previous_uptime = now, system['uptime_s']
        total = _totals(processes, has_interval)
        total['cpu_interval_complete'] = total['cpu_interval_complete'] and not lost
        total['observed_cumulative_cpu_seconds'] = sum(self._observed_cpu_ticks.values()) / self.ticks_per_second
        groups = [dict(root_pid=root, root_role=self.roles.get(root, f'root-{root}'),
                       **_totals((p for p in processes if p['root_pid'] == root), has_interval))
                  for root in self.roots]
        lost_roots = {self._owned[identity]['root_pid'] for identity in lost}
        lost_roles = {self._owned[identity]['role'] for identity in lost}
        for group in groups:
            if group['root_pid'] in lost_roots:
                group['cpu_interval_complete'] = False
        roles = [dict(role=role, **_totals((p for p in processes if p['role'] == role), has_interval))
                 for role in sorted({p['role'] for p in processes} | lost_roles)]
        for group in roles:
            if group['role'] in lost_roles:
                group['cpu_interval_complete'] = False
        owned = [dict(value) for _, value in sorted(self._owned.items())]
        return {'timestamp_utc': datetime.fromtimestamp(wall, timezone.utc).isoformat(),
                'wall_time_s': wall, 'monotonic_s': now, 'elapsed_s': elapsed,
                'system': system, 'processes': processes, 'groups': groups, 'roles': roles,
                'total': total, 'owned_processes': owned,
                'owned_descendant_pids': sorted({p['pid'] for p in owned if p['pid'] not in self.roots}),
                'disappeared_processes': [{'pid': pid, 'start_time_ticks': start} for pid, start in sorted(lost)],
                'gpu': self._gpu(processes)}


def _window(points):
    if not points:
        return None
    return {'sample_count': len(points), 'start_monotonic_s': points[0][0],
            'end_monotonic_s': points[-1][0], 'mean_monotonic_s': statistics.mean(t for t, _ in points),
            'mean_mib': statistics.mean(value for _, value in points),
            'min_mib': min(value for _, value in points), 'max_mib': max(value for _, value in points)}


def _memory_summary(points, first_start, first_end, last_start, windows_available):
    first = _window([(t, value) for t, value in points if first_start <= t < first_end]) if windows_available else None
    last = _window([(t, value) for t, value in points if t > last_start]) if windows_available else None
    delta = last['mean_mib'] - first['mean_mib'] if first and last else None
    stable = [(t, value) for t, value in points if t >= first_start]
    slope = None
    if len(stable) >= 2:
        mean_t, mean_y = statistics.mean(t for t, _ in stable), statistics.mean(y for _, y in stable)
        variance = sum((t - mean_t) ** 2 for t, _ in stable)
        if variance:
            slope = 60 * sum((t - mean_t) * (y - mean_y) for t, y in stable) / variance
    return {'first_mib': points[0][1] if points else None, 'last_mib': points[-1][1] if points else None,
            'peak_mib': max((value for _, value in points), default=None),
            'delta_mib': points[-1][1] - points[0][1] if points else None,
            'first_stable_window': first, 'last_stable_window': last, 'stable_delta_mib': delta,
            'stable_slope_mib_per_min': slope,
            'window_delta_slope_mib_per_min': 60 * delta / (last['mean_monotonic_s'] - first['mean_monotonic_s'])
            if delta is not None and last['mean_monotonic_s'] > first['mean_monotonic_s'] else None}


def summarize_resources(samples, *, stable_after_s=60.0, window_s=60.0):
    """Describe observations, never impose an arbitrary memory growth threshold.

    ``stable`` means after a caller-selected warmup, not a statistical finding.
    Defaults compare the first and last minute after excluding the first minute.
    Nonoverlapping windows need at least warmup + two window durations. OLS RSS
    slope uses every sample after warmup and is descriptive, not a leak verdict.
    """
    if stable_after_s < 0 or window_s <= 0:
        raise ValueError('Warmup must be nonnegative and window duration positive')
    ordered = sorted(samples, key=lambda sample: sample['monotonic_s'])
    duration = ordered[-1]['monotonic_s'] - ordered[0]['monotonic_s'] if ordered else 0
    start = ordered[0]['monotonic_s'] if ordered else 0
    first_start, first_end = start + stable_after_s, start + stable_after_s + window_s
    last_start = start + duration - window_s
    available = bool(ordered) and duration >= stable_after_s + 2 * window_s
    def memory(points):
        return _memory_summary(points, first_start, first_end, last_start, available)
    totals = [(s['monotonic_s'], s['total']['rss_mib']) for s in ordered]
    roots = sorted({g['root_pid'] for s in ordered for g in s.get('groups', [])})
    roles = sorted({p['role'] for s in ordered for p in s.get('processes', [])})
    cpu = [s['total']['cpu_percent_one_core'] for s in ordered if s['total'].get('cpu_percent_one_core') is not None]
    machine = [s['total']['cpu_percent_machine'] for s in ordered if s['total'].get('cpu_percent_machine') is not None]
    gpu = [(s['monotonic_s'], s['gpu']['owned_vram_mib']) for s in ordered if s.get('gpu', {}).get('owned_vram_mib') is not None]
    total_rss = memory(totals)
    window_status = 'insufficient_duration'
    if available:
        window_status = 'available' if total_rss['first_stable_window'] and total_rss['last_stable_window'] else 'insufficient_samples'
    return {'sample_count': len(ordered), 'duration_s': duration,
            'stable_after_s': stable_after_s, 'window_s': window_s,
            'window_status': window_status,
            'rss': {'total': total_rss,
                    'by_root': {str(root): memory([(s['monotonic_s'], sum(g['rss_mib'] for g in s.get('groups', []) if g['root_pid'] == root)) for s in ordered]) for root in roots},
                    'by_role': {role: memory([(s['monotonic_s'], sum(p['rss_mib'] for p in s.get('processes', []) if p['role'] == role)) for s in ordered]) for role in roles}},
            'cpu': {'peak_percent_one_core': max(cpu, default=None),
                    'mean_sampled_percent_one_core': statistics.mean(cpu) if cpu else None,
                    'peak_percent_machine': max(machine, default=None),
                    'mean_sampled_percent_machine': statistics.mean(machine) if machine else None},
            'gpu_owned_vram': memory(gpu),
            'interpretation': 'Descriptive evidence only. Stable windows exclude configured warmup; they do not prove a plateau or determine acceptance. RSS sums may count shared pages; short-lived processes and post-last-observation CPU are unobserved.'}
