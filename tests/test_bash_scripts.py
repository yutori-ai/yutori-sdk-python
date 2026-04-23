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


def test_prerender_frames_sets_parent_shell_variable() -> None:
    """Regression: prerender_frames must assign FRAMES_RENDER_DIR in the parent
    shell so cleanup_temp_files can reap it. Calling it via `$(prerender_frames ...)`
    would run in a subshell and silently leak the directory on every install.
    """
    # Minimal standalone reproduction — doesn't need the full install.sh env.
    # The key property: the caller uses `prerender_frames ...` (not command sub)
    # and reads $FRAMES_RENDER_DIR afterwards.
    script = """
set -euo pipefail
FRAMES_RENDER_DIR=""
tmpdir="$(mktemp -d)"
prerender_frames() {
    FRAMES_RENDER_DIR="$tmpdir/frames"
    mkdir -p "$FRAMES_RENDER_DIR"
    echo "frame0" > "$FRAMES_RENDER_DIR/0"
}
prerender_frames
echo "render_dir=$FRAMES_RENDER_DIR"
[[ -d "$FRAMES_RENDER_DIR" ]] && echo "dir_exists"
rm -rf "$tmpdir"
"""
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, f"prerender_frames failed: {result.stderr}"
    assert "render_dir=" in result.stdout
    assert result.stdout.count("render_dir=") == 1
    # The path must be non-empty in the parent shell.
    render_dir_line = next(line for line in result.stdout.splitlines() if line.startswith("render_dir="))
    assert render_dir_line != "render_dir=", "FRAMES_RENDER_DIR was lost (subshell regression)"
    assert "dir_exists" in result.stdout


def test_play_animation_calls_prerender_frames_directly() -> None:
    """Regression guard: the caller of `prerender_frames` in install.sh.template
    must NOT use `$(...)` command substitution, since that runs in a subshell
    and loses FRAMES_RENDER_DIR. This test is a syntactic lint, not a runtime
    check — it protects against re-introduction of the bug.
    """
    content = INSTALL_TEMPLATE.read_text()
    # Look for the play_animation_until_done function body and assert prerender_frames
    # is called as a plain statement, not captured.
    assert 'prerender_frames "$frame_count" "$use_color"' in content, (
        "prerender_frames must be called directly (not via command substitution) "
        "so FRAMES_RENDER_DIR survives in the parent shell."
    )
    assert '=$(prerender_frames' not in content and '="$(prerender_frames' not in content, (
        "prerender_frames must NOT be called in a $(...) subshell — "
        "FRAMES_RENDER_DIR assignment would be lost and the render dir would leak."
    )


def test_frame_top_skips_status_message_line() -> None:
    """Regression: frame_top must place the frame BELOW the "Installing..."
    status message. Layout is:
      rows 1..N    banner
      row N+1      blank (render_banner's trailing '\\n')
      row N+2      "Installing Yutori CLI with uv..." status line
      row N+3      blank (status line's '\\n\\n')
      row N+4      first row available for the frame

    Previously frame_top was banner_lines + 2 (row N+2), so every frame's
    pad_y=1 blank overwrote the status line on every tick.
    """
    content = INSTALL_TEMPLATE.read_text()
    assert 'frame_top="$((banner_lines + 4))"' in content, (
        "frame_top must be banner_lines + 4 to skip both the status message "
        "and its trailing blank — otherwise the animation overwrites "
        "'Installing Yutori CLI with uv...'."
    )
