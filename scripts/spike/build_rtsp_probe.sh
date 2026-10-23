#!/usr/bin/env bash
set -euo pipefail

probe_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$probe_dir/../.." && pwd)"
output_dir="$repo_dir/outputs/p0/tools"
mkdir -p "$output_dir"
pkg-config --exists libavformat libavcodec libavutil libswscale
read -r -a media_flags <<< "$(pkg-config --cflags --libs libavformat libavcodec libavutil libswscale)"
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Wpedantic -Werror \
    "$probe_dir/rtsp_decode.c" -o "$output_dir/rtsp_decode" "${media_flags[@]}" -lm
printf 'Built %s\n' "$output_dir/rtsp_decode"
