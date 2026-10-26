"""Pinned local MediaMTX lifecycle; external mode owns paths, never the server."""
import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .ports import PortPool
from .processes import ManagedProcess
from .worker_client import valid_timeout

ROOT = Path(__file__).resolve().parents[2]
BINARY_SHA256 = '0ffb7f18210d08ee38cb16668c4f3a2c96622e69d401119c0026d078a2b89523'


class GatewayError(RuntimeError):
    pass


@dataclass(frozen=True)
class GatewayConfig:
    mode: str = 'managed'
    api_port: int = 6197
    http_port: int = 6189
    ice_port: int = 6188
    api_url: str | None = None
    whep_origin: str | None = None
    request_timeout_s: float = .75
    startup_timeout_s: float = 10.
    binary: Path = ROOT / 'tools/mediamtx/mediamtx'
    allowed_origins: tuple[str, ...] = ('http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:8768')

    def __post_init__(self):
        if self.mode not in ('managed', 'external'):
            raise ValueError('Gateway mode must be managed or external')
        ports = (self.api_port, self.http_port, self.ice_port)
        if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports) or len(set(ports)) != 3:
            raise ValueError('Gateway requires three distinct unprivileged ports')
        valid_timeout(self.request_timeout_s); valid_timeout(self.startup_timeout_s)
        if self.mode == 'external':
            for url in (self.api_url, self.whep_origin):
                parsed = urlsplit(url or '')
                if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1') or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
                    raise ValueError('External gateway requires explicit loopback HTTP origins')


@dataclass(frozen=True)
class GatewayHealth:
    reachable: bool
    publishing: bool
    error: str | None = None


class MediaGateway:
    def __init__(self, config: GatewayConfig, output_dir: Path):
        self.config = config
        self.output_dir = Path(output_dir)
        self.api_url = (config.api_url or f'http://127.0.0.1:{config.api_port}').rstrip('/')
        self.whep_origin = (config.whep_origin or f'http://127.0.0.1:{config.http_port}').rstrip('/')
        self.process = None
        self.owned_paths = {}
        self.generation = 0
        self.run_id = uuid.uuid4().hex
        self.closed = False
        self._lock = asyncio.Lock()

    def _request(self, method, path, payload, missing_ok):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(self.api_url + path, data=data, method=method, headers={'Content-Type': 'application/json'})
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=self.config.request_timeout_s) as response:
                body = response.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024:
                    raise GatewayError('Gateway response exceeded 1 MiB')
                return json.loads(body) if body else {}
        except HTTPError as exc:
            if exc.code == 404 and missing_ok:
                return None
            raise GatewayError(f'Gateway API {method} {path}: HTTP {exc.code}') from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise GatewayError(f'Gateway API unavailable: {exc}') from exc

    async def request(self, method, path, payload=None, *, missing_ok=False):
        task = asyncio.create_task(asyncio.to_thread(self._request, method, path, payload, missing_ok))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # urllib's thread cannot be canceled. Finish its bounded operation
            # before cleanup can race a still-in-flight path mutation.
            await asyncio.gather(task, return_exceptions=True)
            raise

    def _save_journal(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / 'owned-paths.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'api_url': self.api_url, 'mode': self.config.mode, 'paths': self.owned_paths}, indent=2))
        temporary.replace(path)

    def _read_journal(self):
        path = self.output_dir / 'owned-paths.json'
        if not path.exists():
            return {}
        value = json.loads(path.read_text())
        if value['paths'] and (value['api_url'] != self.api_url or value['mode'] != self.config.mode):
            raise GatewayError('Pending media paths belong to another gateway; clean them before changing configuration')
        for sid, desired in value['paths'].items():
            self._name(sid)
            if set(desired) != {'source', 'sourceOnDemand', 'rtspTransport'} or not re.fullmatch(r'rtsp://127\.0\.0\.1:[0-9]+/stream', desired['source']):
                raise GatewayError('Invalid media ownership journal')
        return value['paths']

    def _configuration(self):
        c = self.config
        return {
            'logLevel': 'info', 'logDestinations': ['stdout'],
            'authMethod': 'internal', 'authInternalUsers': [{'user': 'any', 'pass': '', 'ips': ['127.0.0.1', '::1'],
                'permissions': [{'action': 'read', 'path': ''}, {'action': 'api'}]}],
            'api': True, 'apiAddress': f'127.0.0.1:{c.api_port}', 'apiAllowOrigins': [],
            'metrics': False, 'pprof': False, 'playback': False,
            'rtsp': False, 'rtmp': False, 'hls': False, 'srt': False, 'moq': False,
            'webrtc': True, 'webrtcAddress': f'127.0.0.1:{c.http_port}',
            'webrtcAllowOrigins': list(c.allowed_origins), 'webrtcTrustedProxies': [],
            'webrtcLocalUDPAddress': f'127.0.0.1:{c.ice_port}', 'webrtcLocalTCPAddress': '',
            'webrtcIPsFromInterfaces': False, 'webrtcAdditionalHosts': ['127.0.0.1'], 'webrtcICEServers2': [],
            'paths': dict(self.owned_paths),
        }

    async def _start_managed(self):
        binary = self.config.binary.resolve()
        if hashlib.sha256(binary.read_bytes()).hexdigest() != BINARY_SHA256:
            raise GatewayError('MediaMTX binary checksum does not match v1.21.2')
        leases = []
        try:
            for port, kind in ((self.config.api_port, 'tcp'), (self.config.http_port, 'tcp'), (self.config.ice_port, 'udp')):
                leases.append(PortPool(port, port).lease([kind]))
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.generation += 1
            path = self.output_dir / f'gateway-{self.run_id}-{self.generation:03d}.json'
            with path.open('x') as output:
                json.dump(self._configuration(), output, indent=2)
            env = {key: value for key, value in os.environ.items() if not key.startswith(('MTX_', 'RTSP_')) and key not in ('LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME')}
            for lease in leases:
                lease.close()
            self.process = await ManagedProcess.spawn([binary, path.resolve()], path.with_suffix('.log'), env=env, cwd=ROOT)
            deadline = time.monotonic() + self.config.startup_timeout_s
            while True:
                if self.process.returncode is not None:
                    raise GatewayError(f'MediaMTX startup exited {self.process.returncode}')
                try:
                    await self.request('GET', '/v3/config/global/get')
                    return
                except GatewayError:
                    if time.monotonic() >= deadline:
                        raise GatewayError('MediaMTX startup timed out')
                    await asyncio.sleep(.05)
        except BaseException:
            if self.process is not None:
                await self.process.terminate()
            raise
        finally:
            for lease in leases:
                lease.close()

    async def start(self):
        async with self._lock:
            if self.closed:
                raise GatewayError('Gateway is closed')
            if self.config.mode == 'managed':
                if self.process is None:
                    self._read_journal()  # Reject a change from an uncleared external gateway.
                    self._save_journal()
                    await self._start_managed()
            else:
                await self.request('GET', '/v3/config/global/get')
                self.owned_paths.update(self._read_journal())
                for sid in list(self.owned_paths):
                    await self._remove_path(sid)

    @staticmethod
    def _name(sid):
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', sid):
            raise ValueError('Invalid gateway path name')
        return sid

    async def add_path(self, sid, rtsp_port):
        sid = self._name(sid)
        if type(rtsp_port) is not int or not 1024 <= rtsp_port <= 65535:
            raise ValueError('Invalid RTSP source port')
        desired = {'source': f'rtsp://127.0.0.1:{rtsp_port}/stream', 'sourceOnDemand': False, 'rtspTransport': 'tcp'}
        async with self._lock:
            if self.closed:
                raise GatewayError('Gateway is closed')
            current = await self.request('GET', '/v3/config/paths/get/' + sid, missing_ok=True)
            if current is not None:
                if sid in self.owned_paths and all(current.get(k) == v for k, v in desired.items()):
                    return
                raise GatewayError('Gateway path already exists; refusing to replace it')
            # Register ownership before the mutation: a canceled/lost HTTP reply
            # must leave enough information to clean a possibly committed path.
            self.owned_paths[sid] = desired
            self._save_journal()
            await self.request('POST', '/v3/config/paths/add/' + sid, desired)

    async def remove_path(self, sid):
        sid = self._name(sid)
        async with self._lock:
            await self._remove_path(sid)

    async def _remove_path(self, sid):
        desired = self.owned_paths.get(sid)
        if desired is None:
            return
        try:
            current = await self.request('GET', '/v3/config/paths/get/' + sid, missing_ok=True)
        except GatewayError:
            if self.config.mode != 'managed':
                raise
            # A managed server can be stopped safely when its API is unavailable.
            # Its next configuration is generated from the remaining owned paths.
            if self.process is not None:
                await self.process.terminate()
            current = None
        if current is not None:
            if any(current.get(k) != v for k, v in desired.items()):
                raise GatewayError('Owned gateway path was replaced; refusing to delete it')
            await self.request('DELETE', '/v3/config/paths/delete/' + sid, missing_ok=True)
        self.owned_paths.pop(sid, None)
        self._save_journal()

    async def health(self, sid=None):
        if self.closed or (self.config.mode == 'managed' and (self.process is None or self.process.returncode is not None)):
            return GatewayHealth(False, False, 'Managed gateway is not running')
        try:
            await self.request('GET', '/v3/config/global/get')
            if sid is None:
                return GatewayHealth(True, False)
            self._name(sid)
            path = await self.request('GET', '/v3/paths/get/' + sid, missing_ok=True)
            return GatewayHealth(True, bool(path and path.get('ready') and 'H264' in path.get('tracks', [])))
        except GatewayError as exc:
            return GatewayHealth(False, False, str(exc))

    async def recover(self):
        async with self._lock:
            if self.closed:
                raise GatewayError('Gateway is closed')
            if self.config.mode == 'managed':
                if self.generation >= 4:
                    raise GatewayError('Managed gateway restart budget exhausted')
                if self.process is not None:
                    await self.process.terminate()
                await self._start_managed()
            else:
                await self.request('GET', '/v3/config/global/get')
                for sid, desired in self.owned_paths.items():
                    current = await self.request('GET', '/v3/config/paths/get/' + sid, missing_ok=True)
                    if current is None:
                        await self.request('POST', '/v3/config/paths/add/' + sid, desired)
                    elif any(current.get(k) != v for k, v in desired.items()):
                        raise GatewayError('Gateway recovered with a conflicting path')

    async def close(self):
        if self.closed:
            return
        if self.config.mode == 'managed':
            async with self._lock:
                if self.process is not None:
                    await self.process.terminate()
                self.owned_paths.clear()
                self._save_journal()
                self.closed = True
        else:
            for sid in list(self.owned_paths):
                await self.remove_path(sid)
            self.closed = True
