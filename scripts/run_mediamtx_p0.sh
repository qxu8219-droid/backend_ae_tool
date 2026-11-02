#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
tool_dir="$project_root/tools/mediamtx"
binary="$tool_dir/mediamtx"
config="$project_root/configs/mediamtx-p0.yml"

case "${1:-}" in
  --help|-h)
    echo "Usage: $0 [--validate-conf]"
    echo "Run the pinned, loopback-only MediaMTX P0 gateway, or validate its configuration."
    exit 0
    ;;
  ""|--validate-conf) ;;
  *) echo "Usage: $0 [--validate-conf]" >&2; exit 2 ;;
esac
if (( $# > 1 )); then
  echo "Usage: $0 [--validate-conf]" >&2
  exit 2
fi
if [[ ! -x "$binary" ]]; then
  echo "Missing MediaMTX v1.21.2 binary: $binary" >&2
  echo "Install it using $tool_dir/SOURCE.txt" >&2
  exit 2
fi
if ! (cd "$tool_dir" && sha256sum --check --status binary.sha256); then
  echo "MediaMTX binary checksum does not match the pinned official v1.21.2 build." >&2
  exit 2
fi

# MediaMTX supports both prefixes as configuration overrides. Remove inherited
# overrides so this launcher consistently applies the reviewed loopback config.
while IFS= read -r variable; do
  case "$variable" in
    MTX_*|RTSP_*) unset "$variable" ;;
  esac
done < <(compgen -e)

"$binary" --validate-conf "$config"
if [[ "${1:-}" == --validate-conf ]]; then
  exit 0
fi
cd "$project_root"
exec "$binary" "$config"
