#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_root"
mkdir -p outputs/t11
cc -std=c11 -O2 -Wall -Wextra -Wpedantic -Werror \
  scripts/worker_validation/rtsp_identity.c -o outputs/t11/rtsp_identity \
  $(pkg-config --cflags --libs libavformat libavcodec libavutil libswscale) -lm
