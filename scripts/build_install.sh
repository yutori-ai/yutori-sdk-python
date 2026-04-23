#!/usr/bin/env bash
#
# Regenerate install.sh by inlining the committed banner and Navigator
# animation frames into install.sh.template.
#
# Usage:
#   bash scripts/build_install.sh

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
template_path="$repo_root/install.sh.template"
output_path="$repo_root/install.sh"
banner_path="$repo_root/assets/terminal/yutori_banner.txt"
frames_path="$repo_root/assets/terminal/agent_logo_frames.txt"
pyproject_path="$repo_root/pyproject.toml"

for path in "$template_path" "$banner_path" "$frames_path" "$pyproject_path"; do
    if [[ ! -f "$path" ]]; then
        printf 'Required asset not found: %s\n' "$path" >&2
        exit 1
    fi
done

python3 - "$template_path" "$output_path" "$banner_path" "$frames_path" "$pyproject_path" <<'PY'
from pathlib import Path
import re
import sys

template_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
banner_path = Path(sys.argv[3])
frames_path = Path(sys.argv[4])
pyproject_path = Path(sys.argv[5])

template = template_path.read_text(encoding="utf-8")
banner = banner_path.read_text(encoding="utf-8").rstrip("\n")
frames = frames_path.read_text(encoding="utf-8").rstrip("\n")

# Read the version from pyproject.toml rather than hardcoding it in the
# template, so cutting a release (= bumping pyproject.toml + regenerating
# install.sh) is a single edit that flows through to the installer header.
pyproject = pyproject_path.read_text(encoding="utf-8")
version_match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
if not version_match:
    sys.exit("Could not find version in pyproject.toml")
version = version_match.group(1)

SENTINELS = ("__INLINE_BANNER__", "__INLINE_AGENT_LOGO_FRAMES__", "__INLINE_VERSION__")
for sentinel in SENTINELS:
    count = template.count(sentinel)
    if count != 1:
        sys.exit(f"Template sentinel {sentinel!r} must appear exactly once (found {count}).")

rendered = (
    template
    .replace("__INLINE_BANNER__", banner)
    .replace("__INLINE_AGENT_LOGO_FRAMES__", frames)
    .replace("__INLINE_VERSION__", version)
)

for sentinel in SENTINELS:
    if sentinel in rendered:
        sys.exit(f"Sentinel {sentinel!r} leaked into generated install.sh — replacement failed.")

output_path.write_text(rendered if rendered.endswith("\n") else rendered + "\n", encoding="utf-8")
PY

chmod +x "$output_path"
printf 'Wrote %s\n' "$output_path"
