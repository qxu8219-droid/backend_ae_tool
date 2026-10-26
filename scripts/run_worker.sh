#!/usr/bin/env bash
set -euo pipefail
: "${ISAACSIM_PYTHON:?Set ISAACSIM_PYTHON to the pinned IsaacSim 6.2 environment}"
[[ "$ISAACSIM_PYTHON" = /* && -x "$ISAACSIM_PYTHON" ]] || { echo 'Invalid IsaacSim Python path' >&2; exit 2; }
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
unset CARB_APP_PATH EXP_PATH ISAAC_PATH PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD
export OMNI_KIT_ACCEPT_EULA="${OMNI_KIT_ACCEPT_EULA:-YES}"
exec "$ISAACSIM_PYTHON" -m sim_worker.main "$@"
