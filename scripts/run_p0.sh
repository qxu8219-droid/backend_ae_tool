#!/usr/bin/env bash
set -euo pipefail
: "${ISAACSIM_PYTHON:?Set ISAACSIM_PYTHON to the Isaac Sim 6.2 pip environment Python executable}"
if [[ "$ISAACSIM_PYTHON" != /* ]]; then
  echo "ISAACSIM_PYTHON must be an absolute path" >&2
  exit 2
fi
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$ISAACSIM_PYTHON" ]]; then
  echo "Isaac Sim Python is missing or not executable: $ISAACSIM_PYTHON" >&2
  exit 2
fi
cd "$project_root"
# This child process belongs to the selected pip runtime, not an inherited ROS/Isaac shell.
unset CARB_APP_PATH EXP_PATH ISAAC_PATH PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD
export OMNI_KIT_ACCEPT_EULA="${OMNI_KIT_ACCEPT_EULA:-YES}"
exec "$ISAACSIM_PYTHON" scripts/spike/smoke.py "$@"
