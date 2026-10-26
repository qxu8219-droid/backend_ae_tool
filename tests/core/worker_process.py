"""CPU-only lifecycle fixture; no production entry point can select this runtime."""
import argparse
import json
from pathlib import Path
import signal
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sim_worker.config import WorkerConfig
from sim_worker.transport import WorkerServer
from core_support import CpuRuntime

parser = argparse.ArgumentParser()
parser.add_argument('--session-id', required=True)
parser.add_argument('--output-dir')
parser.add_argument('--fixture-mode', default='normal')
for name in ('rpc', 'target', 'event', 'rtsp'):
    parser.add_argument('--' + name + '-port', type=int, required=True)
args = parser.parse_args()
config = WorkerConfig(args.session_id, args.rpc_port, args.target_port, args.event_port, args.rtsp_port)

def phase(value):
    print('WORKER_PHASE', json.dumps({'session_id': args.session_id, 'phase': value}), flush=True)

phase('booting')
if args.fixture_mode == 'exit':
    raise SystemExit(23)
if args.fixture_mode == 'hang':
    time.sleep(60)
if args.fixture_mode == 'slow':
    time.sleep(.2)
for value in ('loading_scene', 'loading_robot', 'rtsp_publishing'):
    phase(value)
    time.sleep(.01)

running = True
def stop(*_):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)
with socket.socket() as rtsp, WorkerServer(config, CpuRuntime()) as server:
    rtsp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rtsp.bind(('127.0.0.1', config.rtsp_port)); rtsp.listen()
    if args.fixture_mode == 'no-heartbeat':
        original = server.engine.rpc
        def rpc(route, request):
            if request.command.cmd != 'heartbeat':
                original(route, request)
        server.engine.rpc = rpc
    print('WORKER_READY', json.dumps({'session_id': args.session_id, 'rtsp_binding_addresses': ['127.0.0.1'], 'test_runtime': 'CPU fixture, no IsaacSim'}), flush=True)
    deadline = time.monotonic()
    while running and server.engine.running:
        server.poll(2)
        if time.monotonic() >= deadline:
            server.engine.step(); deadline = time.monotonic() + .05
