"""Explicit factory for the real local backend: uvicorn app.runtime:create_runtime_app --factory."""
import os
from pathlib import Path

from app.adapters.base import ParseLimits
from app.analysis.metadata import load_joint_metadata, metadata_from_common, SOURCE_REF
from app.core.config import Settings
from app.core.gateway import GatewayConfig, ROOT
from app.core.session_manager import SessionManager
from app.main import create_app
from app.services.log_store import LogStore
from app.services.replay_service import ReplayService


def create_runtime_app():
    python = os.environ.get('ISAACSIM_PYTHON')
    if not python:
        raise RuntimeError('Set ISAACSIM_PYTHON to the pinned IsaacSim 6.2 venv entry point')
    cached = load_joint_metadata()
    current = metadata_from_common(ROOT / SOURCE_REF)
    if cached != current:
        raise RuntimeError('Cached joint metadata does not match the installed common config')
    root = Path(os.environ.get('REPLAY_STORAGE_ROOT', str(ROOT / 'outputs/service'))).absolute()
    origins = tuple(value.strip() for value in os.environ.get('REPLAY_ALLOWED_ORIGINS',
        'http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8768').split(',') if value.strip())
    gateway = GatewayConfig(api_port=int(os.environ.get('REPLAY_MTX_API_PORT', 6197)),
        http_port=int(os.environ.get('REPLAY_WHEP_PORT', 6189)), ice_port=int(os.environ.get('REPLAY_ICE_PORT', 6188)),
        allowed_origins=origins)
    settings = Settings(isaac_python=Path(python).absolute(), state_dir=root / 'state', gateway=gateway)
    limits = ParseLimits(max_file_bytes=int(os.environ.get('REPLAY_MAX_UPLOAD_BYTES', 64 * 1024 * 1024)))
    gap = os.environ.get('REPLAY_MAX_GAP_S')
    return create_app(replay_service=ReplayService(SessionManager(settings), LogStore(root / 'logs', limits),
        max_gap_s=float(gap) if gap else None, allowed_origins=origins))
