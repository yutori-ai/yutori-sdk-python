"""Smoke tests for the shell scripts shipped with the installer."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_TEMPLATE = REPO_ROOT / "install.sh.template"

# Scripts that must always be present (hand-authored or committed artifact).
AUTHORED_SCRIPTS = [
    INSTALL_TEMPLATE,
    REPO_ROOT / "uninstall.sh",
    REPO_ROOT / "scripts" / "build_install.sh",
]


def _resolve_install_sh() -> Path:
    """Return a path to a generated install.sh, skipping only if truly unavailable."""
    if INSTALL_SH.exists():
        return INSTALL_SH
    if not INSTALL_TEMPLATE.exists():
        pytest.skip("install.sh.template not present")
    pytest.fail(
        "install.sh is missing — run `bash scripts/build_install.sh` to regenerate it. "
        "If you're on fresh CI, make sure the regenerate step runs before the tests."
    )


def _all_scripts() -> list[Path]:
    return AUTHORED_SCRIPTS + [_resolve_install_sh()]


@pytest.mark.parametrize("script", AUTHORED_SCRIPTS, ids=lambda p: p.name)
def test_authored_bash_syntax(script: Path) -> None:
    """`bash -n` on every hand-authored script — these must always exist."""
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


def test_generated_install_sh_syntax() -> None:
    """The generated install.sh is the actual shipping artifact; require it."""
    script = _resolve_install_sh()
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


@pytest.mark.parametrize("script", AUTHORED_SCRIPTS, ids=lambda p: p.name)
def test_authored_shellcheck_clean_if_available(script: Path) -> None:
    """Run shellcheck when available. Informational — non-blocking if absent."""
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck not installed")
    # Allow SC2034 (unused color constants in template), SC1091 (sourced files).
    result = subprocess.run(
        ["shellcheck", "-e", "SC2034,SC1091", str(script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"shellcheck failed for {script}:\n{result.stdout}\n{result.stderr}"


def test_cleanup_temp_files_returns_zero_under_set_e() -> None:
    """Regression: with `set -e`, cleanup_temp_files must not exit non-zero when
    all tracked files are unset (static / off animation modes). Before the fix,
    the final `[[ -z "" ]] && rm` in cleanup_temp_files returned 1 under `set -e`,
    aborting handoff_to_python_ui before the Python installer UI could run.
    """
    script = f"""
set -euo pipefail
source <(sed -n '/^cleanup_temp_files()/,/^}}/p' {INSTALL_TEMPLATE})
INSTALL_LOG=""
INSTALL_STATUS_FILE=""
FRAMES_CACHE_FILE=""
FRAMES_RENDER_DIR=""
cleanup_temp_files
echo reached_end
"""
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, f"cleanup_temp_files aborted under set -e:\n{result.stderr}"
    assert "reached_end" in result.stdout
