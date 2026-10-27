#!/usr/bin/env python3
"""Own a local real backend, Vite and Chrome test; synthetic commands only."""
import argparse
import asyncio
import csv
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from app.core.ports import PortPool
from app.core.processes import ManagedProcess, process_info
from replay_protocol import CANONICAL_JOINTS
from scripts.spike.smoke import sample_pose
from scripts.validate_api import until
from sim_worker.resources import listening_addresses


def synthetic():
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(['timestamp', *CANONICAL_JOINTS, 'x', 'y', 'yaw'])
    for index in range(241):
        t = index / 20; joints = sample_pose(0., list(CANONICAL_JOINTS))
        joints[0] = joints[10] = .1 * t
        for joint in (1, 3, 5): joints[joint] = t
        for joint in (17, 26): joints[joint] = .025 + .002 * t
        for joint in (18, 27): joints[joint] = -.025 - .002 * t
        writer.writerow([f'{t:.2f}', *joints, .01 * t, .005 * t, .01 * t])
    return output.getvalue()


async def run(args):
    output = args.output_dir.absolute(); output.mkdir(parents=True, exist_ok=False)
    (output / 'synthetic.csv').write_text(synthetic())
    with PortPool(6700, 6799).lease(['tcp', 'tcp', 'tcp', 'udp', 'tcp']) as lease:
        api, mtx, whep, ice, page = lease.ports
    env = dict(os.environ, ISAACSIM_PYTHON=str(args.isaac_python.absolute()), REPLAY_API_PORT=str(api),
        REPLAY_MTX_API_PORT=str(mtx), REPLAY_WHEP_PORT=str(whep), REPLAY_ICE_PORT=str(ice),
        REPLAY_ALLOWED_ORIGINS=f'http://127.0.0.1:{page}', REPLAY_STORAGE_ROOT=str(output / 'service'),
        VITE_USE_MOCK='false', VITE_API_BASE_URL='/api/v1', API_PROXY_TARGET=f'http://127.0.0.1:{api}',
        REAL_API_URL=f'http://127.0.0.1:{api}', REAL_BASE_URL=f'http://127.0.0.1:{page}', REAL_OUTPUT_DIR=str(output))
    processes = []
    try:
        processes.append(await ManagedProcess.spawn(['bash', ROOT / 'scripts/run_backend.sh'], output / 'api.log', cwd=ROOT, env=env))
        processes.append(await ManagedProcess.spawn(['npm', 'run', 'dev', '--', '--port', page], output / 'vite.log', cwd=ROOT.parent / 'frontend', env=env))
        async with httpx.AsyncClient(trust_env=False, timeout=5.) as client:
            async def ready(url):
                assert all(process.returncode is None for process in processes), 'Runtime process exited'
                try: return (await client.get(url)).status_code == 200
                except httpx.ConnectError: return False
            await until(lambda: ready(f'http://127.0.0.1:{api}/api/v1/scenes'), 30.)
            await until(lambda: ready(f'http://127.0.0.1:{page}'), 30.)
        browser = await ManagedProcess.spawn(['node', 'node_modules/@playwright/test/cli.js', 'test', '--config', 'playwright.real.config.ts'],
            output / 'browser.log', cwd=ROOT.parent / 'frontend', env=env)
        processes.append(browser)
        await asyncio.wait_for(browser.process.wait(), 330.)
        assert browser.returncode == 0, (output / 'browser.log').read_text()[-8000:]
    finally:
        for process in reversed(processes): await process.terminate()
    evidence = json.loads((output / 'browser-evidence.json').read_text())
    assert evidence['passed']
    assert all(not (info := process_info(pid)) or info['state'] == 'Z' for pid in evidence['workerPids'])
    assert all(not listening_addresses(port) for port in (api, mtx, whep, page))
    with PortPool(ice, ice).lease(['udp']): pass
    evidence['ownedResourcesReleased'] = True
    (output / 'browser-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps({key: evidence[key] for key in ('passed', 'healthy', 'workerErrorVisibleMs', 'ownedResourcesReleased')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--isaac-python', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
