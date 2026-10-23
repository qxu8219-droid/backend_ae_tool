#!/usr/bin/env bash
set -euo pipefail
: "${ISAACSIM_ROOT:?Set ISAACSIM_ROOT to the Isaac Sim 5.1 installation directory}"
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$ISAACSIM_ROOT/python.sh" ]]; then
  echo "Isaac Sim python.sh is missing or not executable: $ISAACSIM_ROOT" >&2
  exit 2
fi
cd "$project_root"
exec "$ISAACSIM_ROOT/python.sh" scripts/spike/smoke.py "$@"
