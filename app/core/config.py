"""Explicit local deployment settings; no SDK or process startup on import."""
from dataclasses import dataclass, field
import os
from pathlib import Path

from .gateway import GatewayConfig, ROOT
from .worker_client import valid_timeout


@dataclass(frozen=True)
class Settings:
    isaac_python: Path
    state_dir: Path = ROOT / 'outputs/sessions'
    port_first: int = 6100
    port_last: int = 6199
    max_sessions: int = 1
    startup_timeout_s: float = 150.
    load_timeout_s: float = 120.
    rpc_timeout_s: float = 10.
    heartbeat_interval_s: float = 1.
    heartbeat_timeout_s: float = 3.
    shutdown_grace_s: float = 10.
    gateway_poll_s: float = 1.
    history_limit: int = 16
    gateway: GatewayConfig = field(default_factory=GatewayConfig)

    def __post_init__(self):
        if type(self.max_sessions) is not int or self.max_sessions != 1:
            raise ValueError('Only max_sessions=1 is supported')
        if not Path(self.isaac_python).is_absolute() or not Path(self.isaac_python).is_file():
            raise ValueError('An existing absolute IsaacSim Python path is required')
        from .ports import PortPool
        PortPool(self.port_first, self.port_last)
        for value in (self.startup_timeout_s, self.load_timeout_s, self.rpc_timeout_s,
                      self.heartbeat_interval_s, self.heartbeat_timeout_s, self.gateway_poll_s):
            valid_timeout(value)
        if self.heartbeat_timeout_s <= self.heartbeat_interval_s or self.heartbeat_timeout_s > 3:
            raise ValueError('Heartbeat timeout must exceed the interval and be at most 3s')
        if type(self.shutdown_grace_s) not in (int, float) or not 0 <= self.shutdown_grace_s <= 10:
            raise ValueError('Shutdown grace must be 0..10 seconds')
        if type(self.history_limit) is not int or not 1 <= self.history_limit <= 64:
            raise ValueError('Session history limit must be 1..64')


@dataclass(frozen=True)
class Launch:
    command: list[str]
    environment: dict[str, str]


def worker_launch(settings, config, output_dir):
    if not (ROOT / 'outputs/tools/rtsp_loopback.so').is_file():
        raise RuntimeError('Run scripts/build_rtsp_loopback.sh before starting sessions')
    command = ['bash', str(ROOT / 'scripts/run_worker.sh'), '--session-id', config.session_id,
               '--output-dir', str(output_dir)]
    for name in ('rpc', 'target', 'event', 'rtsp'):
        command += ['--' + name + '-port', str(getattr(config, name + '_port'))]
    return Launch(command, {'ISAACSIM_PYTHON': str(settings.isaac_python),
                            'REPLAY_RTSP_LOOPBACK_PORT': str(config.rtsp_port)})


def child_environment(overrides):
    env = {key: value for key, value in os.environ.items()
           if key not in ('PYTHONPATH', 'PYTHONHOME', 'LD_PRELOAD', 'REPLAY_RTSP_LOOPBACK_PORT')}
    env.update(overrides)
    return env
