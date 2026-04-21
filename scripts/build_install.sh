#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
template_path="$repo_root/install.sh.template"
output_path="$repo_root/install.sh"
banner_path="$repo_root/assets/terminal/yutori_banner.txt"
frames_path="$repo_root/assets/terminal/agent_logo_frames.txt"
monorepo_root="${1:-${YUTORI_MONOREPO:-$HOME/projects/yutori-monorepo}}"
play_script="$monorepo_root/scripts/brand/agent_logo_terminal/play.sh"
frames_cache_file="${TMPDIR:-/tmp}/yutori-agent-logo-terminal-frames-v3.txt"

if [[ ! -f "$template_path" ]]; then
    printf 'Template not found: %s\n' "$template_path" >&2
    exit 1
fi

if [[ ! -f "$banner_path" ]]; then
    printf 'Banner asset not found: %s\n' "$banner_path" >&2
    exit 1
fi

if [[ -x "$play_script" ]]; then
    bash "$play_script" --dump-frame 0 >/dev/null

    if [[ ! -f "$frames_cache_file" ]]; then
        printf 'Frame cache not found after running %s\n' "$play_script" >&2
        exit 1
    fi

    cp "$frames_cache_file" "$frames_path"
elif [[ ! -f "$frames_path" ]]; then
    printf 'Navigator animation frames not found: %s\n' "$frames_path" >&2
    exit 1
fi

python3 - "$template_path" "$output_path" "$banner_path" "$frames_path" <<'PY'
from pathlib import Path
import sys

template_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
banner_path = Path(sys.argv[3])
frames_path = Path(sys.argv[4])

template = template_path.read_text(encoding="utf-8")
banner = banner_path.read_text(encoding="utf-8").rstrip("\n")
frames = frames_path.read_text(encoding="utf-8").rstrip("\n")

SENTINELS = ("__INLINE_BANNER__", "__INLINE_AGENT_LOGO_FRAMES__")
for sentinel in SENTINELS:
    count = template.count(sentinel)
    if count != 1:
        sys.exit(f"Template sentinel {sentinel!r} must appear exactly once (found {count}).")

rendered = template.replace("__INLINE_BANNER__", banner).replace("__INLINE_AGENT_LOGO_FRAMES__", frames)

for sentinel in SENTINELS:
    if sentinel in rendered:
        sys.exit(f"Sentinel {sentinel!r} leaked into generated install.sh — replacement failed.")

output_path.write_text(rendered if rendered.endswith("\n") else rendered + "\n", encoding="utf-8")
PY

chmod +x "$output_path"
printf 'Wrote %s\n' "$output_path"
