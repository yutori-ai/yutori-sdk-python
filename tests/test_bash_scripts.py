"""Smoke tests for the shell scripts shipped with the installer."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_TEMPLATE = REPO_ROOT / "install.sh.template"
UNINSTALL_SH = REPO_ROOT / "uninstall.sh"

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


def test_banner_line_counter_uses_pre_increment() -> None:
    """Regression: `(( n++ ))` returns the pre-increment value (0 on the first
    call), which under `set -euo pipefail` aborts the entire installer before
    the animation can render. Must use `(( ++n ))` or `(( n += 1 ))`.
    """
    content = INSTALL_TEMPLATE.read_text()
    assert "(( banner_lines++ ))" not in content, (
        "`(( banner_lines++ ))` under `set -e` aborts on first iteration "
        "because the expression evaluates to 0. Use `(( ++banner_lines ))`."
    )
    assert "(( ++banner_lines ))" in content


def test_cleanup_probes_tty_before_writing() -> None:
    """Regression: cleanup must probe whether /dev/tty is actually usable
    before writing to it. `[[ -r /dev/tty ]]` / `[[ -w /dev/tty ]]` only check
    permission bits and return true under non-interactive docker / CI without
    a controlling terminal, causing the subsequent redirect to fail with
    'No such device or address' on every exit. `has_usable_tty` actually
    opens the device as a probe.
    """
    content = INSTALL_TEMPLATE.read_text()
    assert "has_usable_tty()" in content, (
        "has_usable_tty helper must exist — it's the only way to know "
        "the TTY is actually openable, not just readable/writable."
    )
    # cleanup() must call has_usable_tty before touching /dev/tty.
    cleanup_start = content.index("cleanup() {")
    cleanup_end = content.index("}", cleanup_start)
    cleanup_body = content[cleanup_start:cleanup_end]
    assert "has_usable_tty" in cleanup_body, (
        "cleanup() must guard its /dev/tty write with has_usable_tty."
    )
    # Same for handoff_to_python_ui's </dev/tty redirect.
    handoff_start = content.index("handoff_to_python_ui()")
    handoff_end = content.index("\n}\n", handoff_start)
    handoff_body = content[handoff_start:handoff_end]
    assert "has_usable_tty" in handoff_body, (
        "handoff_to_python_ui must probe the TTY before exec'ing with "
        "</dev/tty — otherwise the redirect fails on non-interactive runs."
    )


def test_uninstall_probes_tty_before_prompting() -> None:
    """Regression: prompt_confirm used `[[ ! -r "$TTY" ]]` which passes on
    non-interactive docker/CI where the device exists with readable mode bits
    but no controlling terminal. `read <$TTY` then fails silently (via
    `|| reply=""`), reply defaults to `default_answer` ("Y" for both
    prompts), and the uninstaller silently removes the CLI + ~/.yutori
    without actual user confirmation. Must use a has_usable_tty probe.
    """
    content = UNINSTALL_SH.read_text()
    assert "has_usable_tty" in content, (
        "uninstall.sh must probe the TTY before relying on read <\"$TTY\" — "
        "otherwise it silently auto-accepts destructive prompts on non-TTY."
    )
    # prompt_confirm must call the probe, not just `[[ -r "$TTY" ]]`.
    prompt_start = content.index("prompt_confirm()")
    prompt_end = content.index("\n}\n", prompt_start)
    prompt_body = content[prompt_start:prompt_end]
    assert "has_usable_tty" in prompt_body, (
        "prompt_confirm must invoke has_usable_tty before proceeding."
    )


def test_uninstall_summary_rule_is_not_misread_as_printf_flag() -> None:
    """Regression: `printf '-------\\n'` errors with 'invalid option' because
    `-------` is parsed as flags. Must quote the argument separately, e.g.
    `printf '%s\\n' "-------"`.
    """
    content = UNINSTALL_SH.read_text()
    assert "printf '-------\\n'" not in content and "printf '------\\n'" not in content, (
        "`printf '-------\\n'` errors with `printf: --: invalid option`. "
        "Use `printf '%s\\n' \"-------\"` instead."
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
