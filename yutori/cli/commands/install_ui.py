"""Hidden ``yutori __install_ui`` subcommand.

Invoked by ``install.sh`` via the absolute path ``$(uv tool dir --bin)/yutori
__install_ui`` after ``uv tool install yutori`` completes. The bootstrap
reopens ``/dev/tty`` on our stdin so interactive prompts work under
``curl | bash``, and exports ``YUTORI_UV_BIN`` so we can locate ``uv`` before
``$PATH`` is repaired. Neither contract is load-bearing for direct ``yutori
__install_ui`` invocations — we fall back to ``PATH`` lookups in that case.

The module is not part of the public surface; users never call this command.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Sequence

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from yutori.auth.credentials import resolve_api_key
from yutori.auth.flow import get_auth_status, run_login_flow
from yutori.auth.types import AuthStatus

BRAND_MINT = "#1DCD98"
MINT_HIGHLIGHT = "#5AE8BD"
SLATE_TEXT = "#94A3B8"
ERROR_RED = "#FF5C5C"

# Canonical first-task example, mirroring docs.yutori.com.
VERIFICATION_TASK = "Give me a list of all employees (names and titles) of Yutori."
VERIFICATION_URL = "https://yutori.com"
VERIFICATION_MAX_STEPS = 5
VERIFICATION_TASK_DASHBOARD_BASE_URL = "https://platform.yutori.com/browsing/tasks"
FINAL_TASK_STATUSES = {"succeeded", "failed"}
# Case-insensitive substrings that identify an auth failure in CLI output.
# We can't rely on any single marker because the CLI's error phrasing varies
# across commands (see yutori/cli/commands/__init__.py and yutori/_http.py).
# Err on the side of classifying ambiguous-looking failures as auth-caused so
# the installer exits non-zero — a false positive is visible, a false negative
# silently hides a real regression.
AUTH_FAILURE_MARKERS = (
    "not authenticated",
    "invalid or missing api key",
    "authenticationerror",
    "unauthorized",
    "forbidden",
    "http 401",
    "http 403",
    " 401 ",
    " 403 ",
    "expired token",
    "revoked",
)

VERIFICATION_POLL_BUDGET_SECONDS = 180
VERIFICATION_POLL_INTERVAL_SECONDS = 5

# Per-subprocess timeouts. `uv` tool operations touch the network; SDK installs
# can resolve large dep graphs; polling calls should be snappy.
QUICK_CMD_TIMEOUT = 30
INSTALL_CMD_TIMEOUT = 300

StepStatus = Literal["success", "skipped", "failed"]
RegistrationState = Literal["creating_account", "logging_in"]

STATUS_LABELS: dict[StepStatus, tuple[str, str]] = {
    "success": ("OK", BRAND_MINT),
    "skipped": ("SKIP", SLATE_TEXT),
    "failed": ("FAIL", ERROR_RED),
}


@dataclass(frozen=True)
class StepResult:
    name: str
    status: StepStatus
    detail: str


@dataclass(frozen=True)
class CLIInstallState:
    cli_path: Path
    bin_dir: Path
    uv_path: str
    version: str
    on_path: bool
    shell_cli_path: Path | None = None


@dataclass(frozen=True)
class SDKInstallPlan:
    reason: str
    command: tuple[str, ...]
    default: bool
    availability_error: str | None = None


def is_interactive_terminal() -> bool:
    # Under `curl | bash`, install.sh reopens /dev/tty on our stdin before
    # exec'ing us, so isatty reflects the user's real terminal rather than
    # the download pipe.
    return sys.stdin.isatty() and sys.stdout.isatty()


def format_command(command: Sequence[str]) -> str:
    return shlex.join(list(command))


def _coerce_output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def normalize_path(value: os.PathLike[str] | str) -> str:
    return str(Path(value).expanduser().resolve())


def _is_executable(path: str | os.PathLike[str]) -> bool:
    candidate = Path(path).expanduser()
    return candidate.is_file() and os.access(str(candidate), os.X_OK)


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = QUICK_CMD_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with output capture and a default timeout.

    Catches ``FileNotFoundError``, ``PermissionError``, and ``TimeoutExpired``
    to return a synthetic ``CompletedProcess`` — callers already branch on
    ``returncode`` and ``stderr``, so they don't need to also handle exceptions.
    """
    argv = list(command)
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=dict(env) if env else None,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial_stdout = _coerce_output_text(exc.stdout)
        partial_stderr = _coerce_output_text(exc.stderr)
        return subprocess.CompletedProcess(
            argv,
            returncode=124,
            stdout=partial_stdout,
            stderr=f"{partial_stderr}\nTimed out after {timeout}s waiting for {format_command(argv)}.",
        )
    except (FileNotFoundError, PermissionError) as exc:
        return subprocess.CompletedProcess(
            argv,
            returncode=127,
            stdout="",
            stderr=f"Could not execute {argv[0]!r}: {exc}",
        )


def describe_completed_process(result: subprocess.CompletedProcess[str], *, max_lines: int = 8) -> str:
    lines: list[str] = []
    for stream in (result.stderr, result.stdout):
        if not stream:
            continue
        for line in stream.splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(stripped)

    if not lines:
        return f"Command exited with status {result.returncode}."

    return " ".join(lines[-max_lines:])


def collect_process_output(result: subprocess.CompletedProcess[str]) -> str:
    parts = [part.strip() for part in (_coerce_output_text(result.stderr), _coerce_output_text(result.stdout)) if part.strip()]
    return "\n".join(parts)


def parse_cli_field(output: str, label: str) -> str | None:
    prefix = f"{label}:"
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix) :].strip()
            if value:
                return value
    return None


def looks_like_auth_failure(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in AUTH_FAILURE_MARKERS)


def resolve_uv_path(env: Mapping[str, str] | None = None) -> str | None:
    resolved_env = env or os.environ
    explicit_uv = resolved_env.get("YUTORI_UV_BIN")
    if explicit_uv and _is_executable(explicit_uv):
        return explicit_uv

    uv_path = shutil.which("uv", path=resolved_env.get("PATH"))
    if uv_path:
        return uv_path

    home = Path(resolved_env.get("HOME", str(Path.home()))).expanduser()
    for candidate in (home / ".local" / "bin" / "uv", home / ".cargo" / "bin" / "uv"):
        if _is_executable(candidate):
            return str(candidate)

    return None


def python_has_pip(interpreter: str, env: Mapping[str, str] | None = None) -> bool:
    return run_command((interpreter, "-m", "pip", "--version"), env=env).returncode == 0


def render_header(console: Console, *, interactive: bool) -> None:
    mode = "Interactive terminal detected." if interactive else "Non-interactive terminal detected."
    console.print(f"[bold {MINT_HIGHLIGHT}]> Yutori installer[/bold {MINT_HIGHLIGHT}]")
    console.print(f"[{SLATE_TEXT}]| {mode}[/]")


def print_prompt_block(console: Console, title: str, description: str, *, command: Sequence[str] | None = None) -> None:
    console.print(f"\n[{SLATE_TEXT}]|[/]")
    console.print(f"[bold {MINT_HIGHLIGHT}]> {title}[/bold {MINT_HIGHLIGHT}]")
    console.print(f"[{SLATE_TEXT}]| {description}[/]")
    if command:
        console.print(f"[{SLATE_TEXT}]| Command: {format_command(command)}[/]")


def summarize_results(console: Console, results: Sequence[StepResult]) -> None:
    table = Table(box=box.ASCII, show_header=True, header_style=f"bold {MINT_HIGHLIGHT}")
    table.add_column("Step", style="bold")
    table.add_column("Status", width=8)
    table.add_column("Detail")

    for result in results:
        label, color = STATUS_LABELS[result.status]
        table.add_row(result.name, f"[{color}]{label}[/{color}]", result.detail)

    console.print("\n")
    console.print(Panel.fit(table, title="Install summary", border_style=BRAND_MINT, box=box.ASCII))


def inspect_cli_install(env: Mapping[str, str] | None = None) -> tuple[CLIInstallState | None, StepResult]:
    resolved_env = env or os.environ
    uv_path = resolve_uv_path(resolved_env)
    if not uv_path:
        return None, StepResult("CLI", "failed", "uv is not available, so the installed CLI could not be verified.")

    bin_dir_result = run_command([uv_path, "tool", "dir", "--bin"], env=resolved_env)
    if bin_dir_result.returncode != 0:
        return None, StepResult("CLI", "failed", describe_completed_process(bin_dir_result))

    # uv may print a line per directory plus warnings — take the last non-empty line.
    candidate_lines = [line.strip() for line in bin_dir_result.stdout.splitlines() if line.strip()]
    if not candidate_lines:
        return None, StepResult("CLI", "failed", "`uv tool dir --bin` returned no path.")
    bin_dir = Path(candidate_lines[-1])
    if not bin_dir.is_absolute():
        return None, StepResult("CLI", "failed", f"`uv tool dir --bin` returned a non-absolute path: {bin_dir}")
    cli_name = "yutori.exe" if os.name == "nt" else "yutori"
    cli_path = bin_dir / cli_name
    if not cli_path.exists():
        return None, StepResult("CLI", "failed", f"Expected CLI binary at {cli_path}, but it was not found.")

    version_result = run_command([str(cli_path), "--version"], env=resolved_env)
    if version_result.returncode != 0:
        return None, StepResult("CLI", "failed", describe_completed_process(version_result))

    version = version_result.stdout.strip() or version_result.stderr.strip() or "yutori installed"
    shell_cli = shutil.which("yutori", path=resolved_env.get("PATH"))
    shell_cli_path = Path(shell_cli) if shell_cli else None
    current_shell_resolves_to_install = bool(shell_cli and normalize_path(shell_cli) == normalize_path(cli_path))
    state = CLIInstallState(
        cli_path=cli_path,
        bin_dir=bin_dir,
        uv_path=uv_path,
        version=version,
        on_path=current_shell_resolves_to_install,
        shell_cli_path=shell_cli_path,
    )
    if shell_cli_path is None:
        detail = f"{version} at {cli_path}. `yutori` is not currently reachable from PATH."
    elif current_shell_resolves_to_install:
        detail = f"{version} at {cli_path}"
    else:
        detail = f"Current shell resolves `yutori` to {shell_cli_path}, not {cli_path}."
    return state, StepResult("CLI", "success", detail)


def detect_sdk_install_plan(cwd: Path | None = None, env: Mapping[str, str] | None = None) -> SDKInstallPlan:
    resolved_cwd = cwd or Path.cwd()
    resolved_env = env or os.environ

    if (resolved_cwd / "pyproject.toml").exists():
        uv_path = resolve_uv_path(resolved_env)
        return SDKInstallPlan(
            reason="Detected pyproject.toml in the current directory.",
            command=((uv_path or "uv"), "add", "yutori"),
            default=True,
            availability_error=None if uv_path else "`uv` is required for project installs.",
        )

    if resolved_env.get("VIRTUAL_ENV"):
        venv_python = Path(resolved_env["VIRTUAL_ENV"]) / "bin" / "python"
        python_path = str(venv_python) if _is_executable(venv_python) else shutil.which("python", path=resolved_env.get("PATH"))
        return SDKInstallPlan(
            reason=f"Detected active virtual environment at {resolved_env['VIRTUAL_ENV']}.",
            command=((python_path or "python"), "-m", "pip", "install", "yutori"),
            default=True,
            availability_error=(
                None
                if python_path and python_has_pip(python_path, resolved_env)
                else "`python -m pip` is not available in the active environment."
            ),
        )

    requirements_path = resolved_cwd / "requirements.txt"
    reason = "Detected requirements.txt but no active virtual environment." if requirements_path.exists() else (
        "No project-specific Python environment was detected."
    )
    # Prefer python3 but fall back to python — some minimal images and recent
    # Homebrew installs expose only one of the two.
    path_env = resolved_env.get("PATH")
    interpreter = shutil.which("python3", path=path_env) or shutil.which("python", path=path_env)
    available = bool(interpreter) and python_has_pip(interpreter or "", resolved_env)
    return SDKInstallPlan(
        reason=reason,
        command=((interpreter or "python3"), "-m", "pip", "install", "--user", "yutori"),
        default=False,
        availability_error=(
            None if available else "A Python interpreter with pip is required for a user-site SDK install."
        ),
    )


def maybe_repair_path(console: Console, state: CLIInstallState, *, interactive: bool) -> StepResult:
    if state.on_path:
        detail = f"{state.bin_dir} is already on PATH."
        print_prompt_block(console, "PATH", detail)
        return StepResult("PATH", "success", detail)

    if state.shell_cli_path is not None:
        detail = f"Current shell resolves `yutori` to {state.shell_cli_path}. Reorder PATH so {state.cli_path} wins."
        print_prompt_block(console, "PATH", detail)
        return StepResult(
            "PATH",
            "failed",
            detail,
        )

    detail = f"{state.bin_dir} is not on PATH."
    print_prompt_block(console, "Shell PATH update", detail, command=(state.uv_path, "tool", "update-shell"))

    if not interactive:
        return StepResult("PATH", "skipped", f"{detail} Skipped PATH repair because no interactive terminal is available.")

    if not Confirm.ask("Run uv tool update-shell now?", default=True, console=console):
        return StepResult("PATH", "skipped", f"{detail} PATH repair was skipped.")

    result = run_command((state.uv_path, "tool", "update-shell"))
    if result.returncode != 0:
        return StepResult("PATH", "failed", describe_completed_process(result))

    return StepResult("PATH", "success", "Updated shell startup files with uv tool update-shell.")


def maybe_install_sdk(console: Console, plan: SDKInstallPlan, *, interactive: bool, cwd: Path | None = None) -> StepResult:
    print_prompt_block(console, "Python SDK", plan.reason, command=plan.command)

    # Skip before gating on availability_error: a non-interactive run can't
    # ask the user anyway, so whether the SDK could have been installed is
    # academic. Reporting "failed" here would bump the whole installer's
    # exit code to 1 in CI environments that lack pip — misleading, since
    # the CLI install itself succeeded.
    if not interactive:
        return StepResult("SDK", "skipped", "Skipped SDK install because no interactive terminal is available.")

    if plan.availability_error:
        return StepResult("SDK", "failed", plan.availability_error)

    if not Confirm.ask("Install the Python SDK into this project?", default=plan.default, console=console):
        return StepResult("SDK", "skipped", "SDK install was skipped.")

    console.print(f"[{SLATE_TEXT}]| Running...[/]")
    result = run_command(plan.command, cwd=cwd or Path.cwd(), timeout=INSTALL_CMD_TIMEOUT)

    if result.returncode != 0:
        return StepResult("SDK", "failed", describe_completed_process(result))

    return StepResult("SDK", "success", f"Installed SDK with {format_command(plan.command)}.")


def format_auth_status(status: AuthStatus) -> str:
    if status.source == "env_var":
        return f"Using YUTORI_API_KEY ({status.masked_key})."
    if status.source == "config_file":
        return f"Using saved credentials ({status.masked_key})."
    return "Using existing credentials."


def maybe_authenticate(console: Console, *, interactive: bool) -> tuple[StepResult, bool]:
    if not resolve_api_key():
        if not interactive:
            print_prompt_block(console, "Authentication", "No interactive terminal detected.")
            console.print(f"[{SLATE_TEXT}]| Skipping auth. To finish setting up:[/]")
            console.print(f"[{SLATE_TEXT}]|   - Run `yutori auth login` on a machine with a browser[/]")
            console.print(
                f"[{SLATE_TEXT}]|   - Or set YUTORI_API_KEY (get one at https://platform.yutori.com/settings)[/]"
            )
            return (
                StepResult(
                    "Auth",
                    "skipped",
                    "Skipped auth because no interactive terminal is available.",
                ),
                False,
            )

        print_prompt_block(console, "Authentication", "Browser login saves credentials to ~/.yutori/config.json.")
        if not Confirm.ask("Log in to Yutori now?", default=True, console=console):
            return StepResult("Auth", "skipped", "Authentication was skipped."), False

        messages: dict[RegistrationState, str] = {
            "creating_account": "Creating account...",
            "logging_in": "Logging in...",
        }

        def on_registration_state(state: str) -> None:
            message = messages.get(state, state)  # type: ignore[arg-type]
            console.print(f"[{SLATE_TEXT}]| {message}[/]")

        result = run_login_flow(on_registration_state=on_registration_state)
        if not result.success:
            # The callback server has already been torn down, so reprinting
            # auth_url here would be misleading — it can't produce a credential.
            console.print(f"[{SLATE_TEXT}]| Run `yutori auth login` again from a terminal to retry.[/]")
            return StepResult("Auth", "failed", str(result.error or "Authentication failed.")), False

        return StepResult("Auth", "success", "Authenticated and saved credentials."), True

    auth_status = get_auth_status()
    if auth_status.authenticated:
        detail = format_auth_status(auth_status)
        print_prompt_block(console, "Authentication", detail)
        return StepResult("Auth", "success", detail), True
    return StepResult("Auth", "failed", "Authentication state is inconsistent."), False


def _summarize_cli_output(output: str) -> str:
    rejection_reason = parse_cli_field(output, "Rejection Reason")
    if rejection_reason:
        return rejection_reason

    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "Result:":
            result_lines = [candidate.strip() for candidate in lines[index + 1 :] if candidate.strip()]
            if result_lines:
                text = " ".join(result_lines)
                return text if len(text) <= 180 else f"{text[:177]}..."

    compact = " ".join(line.strip() for line in lines if line.strip())
    if not compact:
        return "(no output)"
    return compact if len(compact) <= 180 else f"{compact[:177]}..."


def run_verification(
    console: Console,
    *,
    interactive: bool,
    cli_path: Path,
) -> tuple[StepResult, bool]:
    """Run the canonical browsing task through the installed CLI.

    Returns ``(step_result, auth_failed)``. ``auth_failed`` is True only when
    the CLI output clearly indicates missing/rejected credentials; every other
    live-API failure leaves ``auth_failed=False`` so the installer still exits
    0 once the install/auth steps themselves succeeded.
    """
    if not interactive:
        return StepResult("Verification", "skipped", "Skipped verification because no interactive terminal is available."), False

    submit_command = (
        str(cli_path),
        "browse",
        "run",
        VERIFICATION_TASK,
        VERIFICATION_URL,
        "--max-steps",
        str(VERIFICATION_MAX_STEPS),
    )
    print_prompt_block(
        console,
        "Verification task",
        f"Runs the canonical browsing task against yutori.com with max_steps={VERIFICATION_MAX_STEPS}.",
        command=submit_command,
    )
    if not Confirm.ask("Run the verification browsing task now?", default=True, console=console):
        return StepResult("Verification", "skipped", "Verification task was skipped."), False

    submission = run_command(submit_command, timeout=INSTALL_CMD_TIMEOUT)
    submission_output = collect_process_output(submission)
    if submission.returncode != 0:
        auth_failed = looks_like_auth_failure(submission_output)
        detail = submission_output or describe_completed_process(submission)
        return StepResult("Verification", "failed", detail), auth_failed

    task_id = parse_cli_field(submission.stdout, "Task ID")
    if not task_id:
        detail = submission_output or "CLI did not print a task ID for the verification task."
        return StepResult("Verification", "failed", detail), False

    task_url = f"{VERIFICATION_TASK_DASHBOARD_BASE_URL}/{task_id}"
    deadline = time.monotonic() + VERIFICATION_POLL_BUDGET_SECONDS
    status = parse_cli_field(submission.stdout, "Status") or "queued"
    last_output = submission.stdout
    console.print(f"[{SLATE_TEXT}]| Task: {task_url}[/]")
    last_reported_status: str | None = None
    while status not in FINAL_TASK_STATUSES:
        if status != last_reported_status:
            console.print(f"[{SLATE_TEXT}]| Status: {status}[/]")
            last_reported_status = status
        if time.monotonic() >= deadline:
            detail = (
                "Verification timed out. View task: "
                f"{task_url}"
            )
            return StepResult("Verification", "failed", detail), False

        time.sleep(VERIFICATION_POLL_INTERVAL_SECONDS)
        poll = run_command((str(cli_path), "browse", "get", task_id))
        poll_output = collect_process_output(poll)
        if poll.returncode != 0:
            auth_failed = looks_like_auth_failure(poll_output)
            detail = poll_output or describe_completed_process(poll)
            return StepResult("Verification", "failed", f"{detail} View task: {task_url}"), auth_failed

        last_output = poll.stdout
        status = parse_cli_field(poll.stdout, "Status") or "queued"

    summary = _summarize_cli_output(last_output)
    console.print(f"[{SLATE_TEXT}]| Result: {summary}[/]")
    if status == "succeeded":
        return StepResult("Verification", "success", f"Verification succeeded. View task: {task_url}"), False

    return StepResult("Verification", "failed", f"Verification failed. View task: {task_url}"), False


def install_ui_command() -> None:
    """Interactive post-install flow.

    Install-step failures (CLI / PATH / SDK / auth) exit non-zero. A failed
    live-API verification does NOT — the install itself worked.
    """
    console = Console()
    interactive = is_interactive_terminal()
    if os.environ.get("YUTORI_INSTALLER_BOOTSTRAP_SHOWN") != "1":
        render_header(console, interactive=interactive)

    exit_code = 0
    cli_state, cli_result = inspect_cli_install()
    path_result: StepResult | None = None
    verification_result = StepResult("Verification", "skipped", "Verification was not attempted.")

    print_prompt_block(console, "CLI", cli_result.detail)

    if cli_result.status == "failed":
        exit_code = 1

    if cli_state is not None:
        path_result = maybe_repair_path(console, cli_state, interactive=interactive)
        if path_result.status == "failed":
            exit_code = 1

    sdk_result = maybe_install_sdk(console, detect_sdk_install_plan(), interactive=interactive)
    if sdk_result.status == "failed":
        exit_code = 1

    auth_result, authenticated = maybe_authenticate(console, interactive=interactive)
    if auth_result.status == "failed":
        exit_code = 1

    if not authenticated:
        verification_result = StepResult("Verification", "skipped", "Verification requires a valid API key.")
    elif cli_state is None:
        verification_result = StepResult("Verification", "skipped", "Verification requires a working CLI install.")
    else:
        verification_result, auth_failed = run_verification(console, interactive=interactive, cli_path=cli_state.cli_path)
        if auth_failed:
            auth_result = StepResult(
                "Auth", "failed", "Saved credentials were rejected by the API during verification."
            )
            exit_code = 1

    results: list[StepResult] = [cli_result]
    if path_result is not None:
        results.append(path_result)
    results.extend([sdk_result, auth_result, verification_result])
    summarize_results(console, results)

    raise typer.Exit(exit_code)
