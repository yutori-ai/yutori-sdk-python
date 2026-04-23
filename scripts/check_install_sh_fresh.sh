#!/usr/bin/env bash
#
# Pre-commit hook: ensure install.sh is regenerated from its current inputs.
#
# Runs scripts/build_install.sh and fails if install.sh would change — that
# means the author edited install.sh.template (or an asset) without
# regenerating the artifact, and the PR would ship a stale install.sh.
#
# On failure, install.sh is left freshly regenerated in the working tree so
# the author can review and `git add install.sh` before retrying.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
cd "$repo_root"

bash scripts/build_install.sh >/dev/null

if ! git diff --quiet -- install.sh; then
    # Use single-quoted strings so the backticks around `git add install.sh`
    # aren't evaluated as command substitution by the hook's own shell.
    printf '\n%s\n' 'install.sh is stale relative to its inputs (template / banner / frames).'
    printf '%s\n' 'scripts/build_install.sh has regenerated it in your working tree.'
    printf '%s\n' 'Review the diff, then `git add install.sh` and commit again:'
    printf '\n'
    git diff --stat -- install.sh
    exit 1
fi
