#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
mkdir -p outputs/tools
temporary="$(mktemp outputs/tools/.rtsp_loopback.XXXXXX)"
trap 'rm -f -- "$temporary"' EXIT
cc -std=c11 -O2 -Wall -Wextra -Wpedantic -Werror -fPIC -shared \
  sim_worker/native/rtsp_loopback.c -o "$temporary" -ldl
mv -- "$temporary" outputs/tools/rtsp_loopback.so
