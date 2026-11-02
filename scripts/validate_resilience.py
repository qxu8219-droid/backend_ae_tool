#!/usr/bin/env python3
"""Run an owned real B041 matrix or wall-clock soak, preserving failure evidence."""
import argparse
import asyncio
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core.ports import PortPool
from app.core.processes import ManagedProcess, process_info
from scripts.validate_b041_frontend import raw_hashes, sha256, wait_ready
from sim_worker.resources import listening_addresses


def assess_browser(report, *, mode, seconds):
    """A successful short probe must never be reported as a formal soak."""
    errors = []
    if report.get('passed') is not True or report.get('mode') != mode:
        errors.append('Browser mode or passing result is missing')
    acceptance = mode if mode != 'soak' else 'soak_30_minutes' if seconds >= 1800 else 'smoke_only'
    if mode == 'soak':
        try:
            soak = report['soak']; healthy = soak['healthy']
            elapsed, requested = soak['elapsedSeconds'], soak['requestedSeconds']
            fps, age = healthy['presentedFps'], healthy['maxCaptureAgeMs']
            if not all(type(value) in (int, float) and math.isfinite(value) for value in (elapsed, requested, fps, age)):
                raise ValueError('Nonfinite soak measurements')
            if requested != seconds or elapsed < seconds:
                errors.append('Requested wall-clock duration was not completed')
            if fps < 15. or not 0 <= age < 2000.:
                errors.append('Healthy window requires >=15fps and capture age <2000ms')
        except (KeyError, TypeError, ValueError):
            errors.append('Complete finite soak measurements are required')
    return {'passed': not errors, 'acceptance': 'failed' if errors else acceptance, 'errors': errors}


def inputs(args):
    from app.adapters.b041.reader import read_recording
    from app.adapters.base import ParseLimits
    bundle, profile = args.offline_bundle.absolute(), args.profile.absolute()
    recording = read_recording(bundle, ParseLimits())
    config = json.loads(profile.read_text())['b041']
    if recording.source.format != 'full23_parquet' or config['basis'] != 'state' or config['status'] != 'provisional':
        raise ValueError('T17 requires an offline B041 source and explicit provisional state profile')
    hashes = raw_hashes()
    hashes['data/real_logs/catalog.json'] = sha256(ROOT / 'data/real_logs/catalog.json')
    return {'source_id': recording.source.id, 'source_hashes': hashes,
            'input_hashes': {str(bundle): sha256(bundle), str(profile): sha256(profile)}}


def measured_resources(samples, browser, *, mode):
    if mode != 'soak': return samples
    try:
        soak = browser['soak']
        start = datetime.fromisoformat(soak['startedAt'].replace('Z', '+00:00')).timestamp()
        end = datetime.fromisoformat(soak['endedAt'].replace('Z', '+00:00')).timestamp()
        if not math.isfinite(start) or not math.isfinite(end) or end <= start: raise ValueError('Invalid interval')
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError('Soak resource summary requires the actual measurement interval') from exc
    return [sample for sample in samples if start <= sample['wall_time_s'] <= end]


def recording_passed(browser, analysis):
    active = analysis.get('active_window', {})
    counts = active.get('counts', {})
    return (browser.get('passed') is True and browser.get('cleanupAcknowledged') is True
            and active.get('passed') is True and active.get('duration_s', 0) >= 8.
            and active.get('decoded_frames', 0) > 0
            and counts.get('matched') == active.get('decoded_frames')
            and all(value == 0 for key, value in counts.items() if key != 'matched'))


async def monitor(sampler, output, stop):
    samples = []
    with (output / 'resources.jsonl').open('x') as stream:
        while not stop.is_set():
            sample = await asyncio.to_thread(sampler.sample)
            samples.append(sample)
            stream.write(json.dumps(sample, allow_nan=False) + '\n'); stream.flush()
            (output / 'resource-latest.json').write_text(json.dumps(sample, allow_nan=False) + '\n')
            try: await asyncio.wait_for(stop.wait(), timeout=5.)
            except asyncio.TimeoutError: pass
    return samples


async def run(args, before):
    import httpx
    from scripts.validation_resources import ResourceSampler, summarize_resources
    output = args.output_dir.absolute()
    if output.resolve().is_relative_to((ROOT / 'data/real_logs').resolve()):
        raise ValueError('Validation outputs cannot replace raw or derived data')
    if not args.isaac_python.absolute().is_file():
        raise ValueError('SDK Python entry point is missing')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'inputs-before.json').write_text(json.dumps(before, indent=2) + '\n')
    with PortPool(6900, 6999).lease(['tcp', 'tcp', 'tcp', 'udp', 'tcp']) as lease:
        api, mtx, whep, ice, page = lease.ports
    env = dict(os.environ, ISAACSIM_PYTHON=str(args.isaac_python.absolute()),
        REPLAY_API_PORT=str(api), REPLAY_MTX_API_PORT=str(mtx), REPLAY_WHEP_PORT=str(whep), REPLAY_ICE_PORT=str(ice),
        REPLAY_ALLOWED_ORIGINS=f'http://127.0.0.1:{page}', REPLAY_STORAGE_ROOT=str(output / 'service'),
        VITE_USE_MOCK='false', VITE_API_BASE_URL='/api/v1', API_PROXY_TARGET=f'http://127.0.0.1:{api}',
        REAL_API_URL=f'http://127.0.0.1:{api}', REAL_BASE_URL=f'http://127.0.0.1:{page}', REAL_OUTPUT_DIR=str(output),
        B041_OFFLINE_BUNDLE=str(args.offline_bundle.absolute()), B041_PROFILE=str(args.profile.absolute()),
        PLAYWRIGHT_BROWSERS_PATH=str(ROOT / 'outputs/t17/playwright-tools'),
        T17_MODE=args.mode, T17_SOAK_SECONDS=str(args.seconds), T17_OWNERSHIP_DIR=str(output / 'service'))
    for key in ('PYTHONPATH', 'PYTHONHOME', 'LD_PRELOAD', 'LD_LIBRARY_PATH'): env.pop(key, None)
    processes = []; failure = None; task = None; samples = []; stop = asyncio.Event()
    report = {'passed': False, 'mode': args.mode, 'requested_seconds': args.seconds, 'inputs': before,
        'ports': {'api': api, 'mtx_api': mtx, 'whep': whep, 'ice_udp': ice, 'frontend': page},
        'owned_processes': [], 'cleanup_errors': [], 'shutdown': []}
    try:
        for command, log, cwd in [(['bash', ROOT / 'scripts/run_backend.sh'], 'api.log', ROOT),
                                 (['npm', 'run', 'dev', '--', '--port', page, '--strictPort'], 'vite.log', ROOT.parent / 'frontend')]:
            process = await ManagedProcess.spawn(command, output / log, cwd=cwd, env=env)
            processes.append(process)
            report['owned_processes'].append({'pid': process.pid, 'command': list(map(str, command))})
        env['T17_API_PID'] = str(processes[0].pid)
        async with httpx.AsyncClient(trust_env=False, timeout=5.) as client:
            await wait_ready(client, f'http://127.0.0.1:{api}/api/v1/scenes', processes)
            await wait_ready(client, f'http://127.0.0.1:{page}', processes)
        command = ['node', 'node_modules/@playwright/test/cli.js', 'test', '--config', 'playwright.resilience.config.ts']
        browser = await ManagedProcess.spawn(command, output / 'browser.log', cwd=ROOT.parent / 'frontend', env=env)
        processes.append(browser)
        report['owned_processes'].append({'pid': browser.pid, 'command': command})
        sampler = ResourceSampler([p.pid for p in processes], roles={p.pid: role for p, role in zip(processes, ('api', 'vite', 'browser'))})
        task = asyncio.create_task(monitor(sampler, output, stop))
        timeout = args.seconds + 480. if args.mode == 'soak' else 900.
        await asyncio.wait_for(browser.process.wait(), timeout=timeout)
        if browser.returncode != 0:
            raise RuntimeError('Browser validation failed; see browser.log')
        if args.mode in ('matrix', 'recording'):
            recording = output / 'recording'
            recording_browser = json.loads((recording / 'browser-recording-evidence.json').read_text())
            command = ['/usr/bin/python3', ROOT / 'scripts/validate_recording.py', recording_browser['video'],
                recording / 'recording-observations.jsonl', '--window', recording / 'active-window.json',
                '--output', recording / 'recording-report.json', '--frames', recording / 'recording-frames.jsonl']
            analyzer = await ManagedProcess.spawn(command, output / 'recording-analysis.log', cwd=ROOT, env=env)
            processes.append(analyzer)
            report['owned_processes'].append({'pid': analyzer.pid, 'command': list(map(str, command))})
            await asyncio.wait_for(analyzer.process.wait(), timeout=300.)
            analysis = json.loads((recording / 'recording-report.json').read_text())
            report['recording'] = {'passed': recording_passed(recording_browser, analysis),
                'worker_pid': recording_browser.get('workerPid'), 'analysis_report': 'recording/recording-report.json',
                'active_window': analysis.get('active_window'), 'strict_full_frame_passed': analysis.get('strict_full_frame_passed')}
            if analyzer.returncode != 0 or not report['recording']['passed']:
                raise RuntimeError('Recorded-screen active window failed; see recording/recording-report.json')
    except BaseException as exc:
        failure = exc; report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if task is not None:
            stop.set()
            try: samples = await asyncio.wait_for(task, timeout=10.)
            except BaseException as exc: report['cleanup_errors'].append(f'Resource monitor: {exc}')
        for process in reversed(processes):
            start = time.monotonic()
            try: await process.terminate(grace_s=10.)
            except BaseException as exc: report['cleanup_errors'].append(f'pid {process.pid}: {exc}')
            report['shutdown'].append({'pid': process.pid, 'elapsed_s': time.monotonic() - start,
                'term_to_force_budget_s': 10., 'group_released': not process._members()})
        browser_file = output / 'browser-evidence.json'
        try: browser_report = json.loads(browser_file.read_text()) if browser_file.is_file() else {}
        except (OSError, ValueError) as exc:
            browser_report = {}; report['cleanup_errors'].append(f'Browser evidence unreadable: {exc}')
        if args.mode == 'recording':
            try:
                evidence = json.loads((output / 'recording/browser-recording-evidence.json').read_text())
                browser_report = {'mode': 'recording', 'passed': evidence.get('passed') is True,
                    'workerPids': [evidence['workerPid']] if evidence.get('workerPid') else []}
            except (OSError, ValueError): browser_report = {}
        report['browser'] = assess_browser(browser_report, mode=args.mode, seconds=args.seconds)
        report['worker_pids'] = browser_report.get('workerPids', [])
        if report.get('recording', {}).get('worker_pid'):
            report['worker_pids'].append(report['recording']['worker_pid'])
        worker_ports = set()
        for file in (output / 'service/state').glob('*/lifecycle-final.json'):
            try:
                final = json.loads(file.read_text())
                worker_ports.update(value for key, value in final['ports'].items() if key.endswith('_port'))
                report['cleanup_errors'].extend(final.get('cleanup_errors', []))
            except (OSError, ValueError, KeyError) as exc: report['cleanup_errors'].append(f'Lifecycle evidence: {exc}')
        report['worker_ports'] = sorted(worker_ports)
        owned = samples[-1].get('owned_processes', []) if samples else []
        survivors = []
        for identity in owned:
            info = process_info(identity['pid'])
            if info and info['start'] == identity['start_time_ticks'] and info['state'] != 'Z': survivors.append(identity)
        report['observed_owned_survivors'] = survivors
        report['owned_processes_released'] = not survivors and all(not p._members() for p in processes)
        report['workers_released'] = all(not (info := process_info(pid)) or info['state'] == 'Z' for pid in report['worker_pids'])
        report['tcp_ports_released'] = all(not listening_addresses(port) for port in {api, mtx, whep, page, *worker_ports})
        try:
            with PortPool(ice, ice).lease(['udp']): report['ice_udp_released'] = True
        except OSError as exc:
            report['ice_udp_released'] = False; report['cleanup_errors'].append(str(exc))
        try:
            after = inputs(args)
            report['sources_unchanged'] = after == before
            (output / 'inputs-after.json').write_text(json.dumps(after, indent=2) + '\n')
        except BaseException as exc:
            report['sources_unchanged'] = False; report['hash_error'] = str(exc)
        report['all_process_lifetime_resources'] = summarize_resources(samples) if samples else None
        try:
            measured = measured_resources(samples, browser_report, mode=args.mode)
            report['resources'] = summarize_resources(measured) if measured else None
            report['resource_scope'] = 'actual browser soak interval, excluding creation and teardown' if args.mode == 'soak' else 'whole matrix process lifetime'
        except ValueError as exc:
            report['resources'] = None; report['resource_error'] = str(exc)
        report['ownedResourcesReleased'] = all(report[key] for key in ('owned_processes_released', 'workers_released', 'tcp_ports_released', 'ice_udp_released')) and not report['cleanup_errors']
        report['passed'] = failure is None and report['browser']['passed'] and report['sources_unchanged'] and report['ownedResourcesReleased'] and report['resources'] is not None
        if args.mode in ('matrix', 'recording'): report['passed'] = report['passed'] and report.get('recording', {}).get('passed') is True
        (output / 'validation-evidence.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({key: report[key] for key in ('passed', 'mode', 'browser', 'sources_unchanged', 'ownedResourcesReleased')}, indent=2))
    if failure is not None: raise failure
    if not report['passed']: raise RuntimeError(f'T17 run or cleanup failed: {output}')


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('matrix', 'soak', 'recording'), default='soak')
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--isaac-python', type=Path)
    parser.add_argument('--offline-bundle', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if not 10 <= args.seconds <= 7200: parser.error('--seconds must be 10..7200; only >=1800 qualifies as formal soak')
    if not args.check and (args.output_dir is None or args.isaac_python is None):
        parser.error('Execution requires --output-dir and --isaac-python')
    return args


if __name__ == '__main__':
    args = arguments(); before = inputs(args)
    if args.check: print(json.dumps({'checked_only': True, **before}, indent=2))
    else: asyncio.run(run(args, before))
