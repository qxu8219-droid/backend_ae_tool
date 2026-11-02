#!/usr/bin/env bash
set -euo pipefail
: "${ISAACSIM_PYTHON:?Set the absolute IsaacSim 6.2 venv Python entry point}"
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
unset PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH
exec .venv/bin/python -m uvicorn app.runtime:create_runtime_app --factory \
  --host 127.0.0.1 --port "${REPLAY_API_PORT:-8000}" --workers 1 \
  --ws websockets-sansio --ws-max-size 65536 --ws-max-queue 16
