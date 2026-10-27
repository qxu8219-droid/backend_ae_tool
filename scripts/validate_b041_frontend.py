#!/usr/bin/env python3
"""Own one local SDK/browser validation of prepared B041 recordings.

--check validates inputs and hashes only; it starts no processes or browser.
The executing run requires a new output directory and reports failures/cleanup.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from app.core.ports import PortPool
from app.core.processes import ManagedProcess, process_info
from sim_worker.resources import listening_addresses


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def input_paths(args):
    values = {}
    for name, environment in [('offline_bundle', 'B041_OFFLINE_BUNDLE'),
                              ('runtime_bundle', 'B041_RUNTIME_BUNDLE'), ('profile', 'B041_PROFILE')]:
        value = getattr(args, name) or os.environ.get(environment)
        if not value:
            raise ValueError(f'Provide --{name.replace("_", "-")} or {environment}')
        file = Path(value).absolute()
        if not file.is_file():
            raise ValueError(f'Missing local input: {file}')
        values[name] = file
    return values


def raw_hashes(*, check_catalog=True):
    """Only follow the project's local catalog, never paths recorded in bundles."""
    directory = ROOT / 'data/real_logs'
    catalog = json.loads((directory / 'catalog.json').read_text())
    hashes = {}
    for source in catalog['sources']:
        for entry in source.get('files', []):
            file = (directory / entry['path']).resolve()
            if not file.is_relative_to((directory / 'raw').resolve()):
                raise ValueError('Catalog source escapes readonly raw directory')
            value = sha256(file)
            if check_catalog and value != entry['sha256']:
                raise ValueError(f'Raw source hash differs from catalog: {entry["path"]}')
            hashes[str(file.relative_to(ROOT))] = value
    return hashes


def check_inputs(paths):
    profile = json.loads(paths['profile'].read_text())['b041']
    if profile['basis'] != 'state' or profile['status'] != 'provisional' or profile['chassis']['velocity_frame'] != 'body':
        raise ValueError('Harness requires provisional state-basis body-velocity profile')
    result = {'input_hashes': {str(file): sha256(file) for file in paths.values()}, 'sources': []}
    for name, expected_format in [('offline_bundle', 'full23_parquet'), ('runtime_bundle', 'runtime_events')]:
        bundle = json.loads(paths[name].read_text())
        if bundle['schema'] != 'b041.recording.v1' or bundle['source']['format'] != expected_format:
            raise ValueError(f'{name} has wrong portable recording format')
        samples = [sample for sample in bundle['samples'] if sample['state'] is not None]
        if len(samples) < 2:
            raise ValueError(f'{name} has no replayable measured-state series')
        if expected_format == 'full23_parquet':
            if bundle['source']['time_unit'] != 'ns' or not all(isinstance(sample['time'], str) for sample in samples):
                raise ValueError('Offline clocks must preserve integer nanoseconds as strings')
            clocks = [int(sample['time']) for sample in samples]
            if any(b <= a for a, b in zip(clocks, clocks[1:])):
                raise ValueError('Offline source clocks must increase strictly')
        result['sources'].append({'id': bundle['source']['id'], 'format': expected_format,
                                  'state_samples': len(samples), 'annotation_alignment': bundle['annotations']['alignment']})
    result['raw_hashes'] = raw_hashes()
    return result


async def wait_ready(client, url, processes, timeout=30.):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if any(process.returncode is not None for process in processes):
            raise RuntimeError('Owned runtime process exited before readiness; inspect api.log/vite.log')
        try:
            if (await client.get(url)).status_code == 200:
                return
        except httpx.ConnectError:
            pass
        await asyncio.sleep(.1)
    raise TimeoutError(f'Local runtime did not become ready: {url}')


async def run(args, paths, before):
    if args.output_dir is None or args.isaac_python is None:
        raise ValueError('Execution requires --output-dir and --isaac-python; use --check for input inspection only')
    output = args.output_dir.absolute()
    if output.resolve().is_relative_to((ROOT / 'data/real_logs/raw').resolve()):
        raise ValueError('Validation outputs cannot be placed in readonly raw storage')
    # Keep the venv entry point, not its resolved base-Python symlink target.
    isaac_python = args.isaac_python.absolute()
    if not isaac_python.is_file():
        raise ValueError(f'Missing SDK Python entry point: {isaac_python}')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'inputs-before.json').write_text(json.dumps(before, indent=2) + '\n')
    with PortPool(6800, 6899).lease(['tcp', 'tcp', 'tcp', 'udp', 'tcp']) as lease:
        api, mtx, whep, ice, page = lease.ports
    env = dict(os.environ, ISAACSIM_PYTHON=str(isaac_python), REPLAY_API_PORT=str(api),
        REPLAY_MTX_API_PORT=str(mtx), REPLAY_WHEP_PORT=str(whep), REPLAY_ICE_PORT=str(ice),
        REPLAY_ALLOWED_ORIGINS=f'http://127.0.0.1:{page}', REPLAY_STORAGE_ROOT=str(output / 'service'),
        VITE_USE_MOCK='false', VITE_API_BASE_URL='/api/v1', API_PROXY_TARGET=f'http://127.0.0.1:{api}',
        REAL_API_URL=f'http://127.0.0.1:{api}', REAL_BASE_URL=f'http://127.0.0.1:{page}', REAL_OUTPUT_DIR=str(output),
        B041_OFFLINE_BUNDLE=str(paths['offline_bundle']), B041_RUNTIME_BUNDLE=str(paths['runtime_bundle']), B041_PROFILE=str(paths['profile']))
    for name in ('PYTHONPATH', 'PYTHONHOME', 'LD_PRELOAD', 'LD_LIBRARY_PATH'):
        env.pop(name, None)
    processes = []; failure = None
    report = {'passed': False, 'scope': 'Real recorded B041 numeric replay with provisional gripper/body-frame assumptions; no calibration truth claim',
              'ports': {'api': api, 'mtx_api': mtx, 'whep': whep, 'ice_udp': ice, 'frontend': page},
              'source_hashes_before': before['raw_hashes'], 'input_hashes_before': before['input_hashes'],
              'sources': before['sources'], 'owned_processes': [], 'cleanup_errors': []}
    try:
        for command, logfile, cwd in [
            (['bash', ROOT / 'scripts/run_backend.sh'], 'api.log', ROOT),
            (['npm', 'run', 'dev', '--', '--port', page, '--strictPort'], 'vite.log', ROOT.parent / 'frontend')]:
            process = await ManagedProcess.spawn(command, output / logfile, cwd=cwd, env=env)
            processes.append(process); report['owned_processes'].append({'pid': process.pid, 'command': list(map(str, command))})
        async with httpx.AsyncClient(trust_env=False, timeout=5.) as client:
            await wait_ready(client, f'http://127.0.0.1:{api}/api/v1/scenes', processes)
            await wait_ready(client, f'http://127.0.0.1:{page}', processes)
        command = ['node', 'node_modules/@playwright/test/cli.js', 'test', '--config', 'playwright.b041.config.ts']
        browser = await ManagedProcess.spawn(command, output / 'browser.log', cwd=ROOT.parent / 'frontend', env=env)
        processes.append(browser); report['owned_processes'].append({'pid': browser.pid, 'command': command})
        await asyncio.wait_for(browser.process.wait(), timeout=450.)
        if browser.returncode != 0:
            raise RuntimeError('B041 browser validation failed:\n' + (output / 'browser.log').read_text()[-8000:])
    except BaseException as exc:
        failure = exc; report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        for process in reversed(processes):
            try:
                await process.terminate()
            except BaseException as exc:
                report['cleanup_errors'].append(f'pid {process.pid}: {exc}')
        browser_file = output / 'browser-evidence.json'
        evidence = json.loads(browser_file.read_text()) if browser_file.is_file() else {}
        report['browser_evidence'] = 'browser-evidence.json' if evidence else None
        report['worker_pids'] = evidence.get('workerPids', [])
        worker_ports = set()
        for file in (output / 'service/state').glob('*/lifecycle-final.json'):
            final = json.loads(file.read_text())
            worker_ports.update(value for key, value in final['ports'].items() if key.endswith('_port'))
            report['cleanup_errors'].extend(final.get('cleanup_errors', []))
        report['worker_ports'] = sorted(worker_ports)
        report['owned_processes_released'] = all(not process._members() for process in processes)
        report['workers_released'] = all(not (info := process_info(pid)) or info['state'] == 'Z' for pid in report['worker_pids'])
        report['tcp_ports_released'] = all(not listening_addresses(port) for port in {api, mtx, whep, page, *worker_ports})
        try:
            with PortPool(ice, ice).lease(['udp']):
                report['ice_udp_released'] = True
        except OSError as exc:
            report['ice_udp_released'] = False; report['cleanup_errors'].append(str(exc))
        try:
            report['source_hashes_after'] = raw_hashes(check_catalog=False)
            report['input_hashes_after'] = {str(file): sha256(file) for file in paths.values()}
            report['sources_unchanged'] = report['source_hashes_before'] == report['source_hashes_after'] and report['input_hashes_before'] == report['input_hashes_after']
        except BaseException as exc:
            report['sources_unchanged'] = False; report['hash_error'] = str(exc)
        report['ownedResourcesReleased'] = all(report[key] for key in ('owned_processes_released', 'workers_released', 'tcp_ports_released', 'ice_udp_released')) and not report['cleanup_errors']
        report['passed'] = failure is None and evidence.get('passed', False) and report['sources_unchanged'] and report['ownedResourcesReleased']
        (output / 'validation-evidence.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('passed', 'sources_unchanged', 'ownedResourcesReleased')}, indent=2))
    if failure is not None:
        raise failure
    if not report['passed']:
        raise RuntimeError(f'Validation evidence or cleanup incomplete: {output / "validation-evidence.json"}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--isaac-python', type=Path)
    parser.add_argument('--offline-bundle', type=Path)
    parser.add_argument('--runtime-bundle', type=Path)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--check', action='store_true', help='Read inputs/hash originals only; no SDK/browser/process startup')
    args = parser.parse_args(); paths = input_paths(args); before = check_inputs(paths)
    if args.check:
        print(json.dumps({'checked_only': True, 'sources': before['sources'], 'verified_raw_files': len(before['raw_hashes']), 'input_hashes': before['input_hashes']}, indent=2))
    else:
        asyncio.run(run(args, paths, before))
