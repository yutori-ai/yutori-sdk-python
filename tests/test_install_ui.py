from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from yutori.auth.types import LoginResult
from yutori.cli.commands.install_ui import (
    MCP_SERVER_INSTALL_COMMAND,
    MCP_SKILLS_INSTALL_COMMAND,
    VERIFICATION_MAX_STEPS,
    VERIFICATION_TASK,
    VERIFICATION_TASK_DASHBOARD_BASE_URL,
    VERIFICATION_URL,
    CLIInstallState,
    SDKInstallPlan,
    StepResult,
    detect_sdk_install_plan,
    inspect_cli_install,
    looks_like_auth_failure,
    maybe_authenticate,
    maybe_install_mcp_server,
    maybe_install_mcp_skills,
    maybe_install_sdk,
    maybe_repair_path,
    resolve_npx_path,
    resolve_uv_path,
    run_interactive_command,
    run_verification,
)
from yutori.cli.main import app

runner = CliRunner()


def test_hidden_install_ui_not_in_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "__install_ui" not in result.stdout


def test_detect_sdk_install_plan_prefers_pyproject(tmp_path: Path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with patch("yutori.cli.commands.install_ui.resolve_uv_path", return_value="/usr/bin/uv"):
        plan = detect_sdk_install_plan()

    assert plan.command == ("/usr/bin/uv", "add", "yutori")
    assert plan.default is True
    assert plan.availability_error is None


def test_detect_sdk_install_plan_uses_bootstrap_uv_env_var(tmp_path: Path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with patch("yutori.cli.commands.install_ui.resolve_uv_path", return_value="/tmp/uv"):
        plan = detect_sdk_install_plan()

    assert plan.command == ("/tmp/uv", "add", "yutori")
    assert plan.availability_error is None


def test_detect_sdk_install_plan_uses_active_virtualenv(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))

    def fake_which(command: str, path: str | None = None) -> str | None:
        return "/usr/bin/python" if command == "python" else None

    with patch("yutori.cli.commands.install_ui.shutil.which", side_effect=fake_which):
        plan = detect_sdk_install_plan()

    assert plan.command == ("/usr/bin/python", "-m", "pip", "install", "yutori")
    assert plan.default is True
    assert "active virtual environment" in plan.reason


def test_detect_sdk_install_plan_prefers_virtualenv_python_path(tmp_path: Path, monkeypatch):
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
    venv_python.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))

    with (
        patch("yutori.cli.commands.install_ui.python_has_pip", return_value=True),
        patch("yutori.cli.commands.install_ui.shutil.which", return_value="/usr/bin/python"),
    ):
        plan = detect_sdk_install_plan()

    assert plan.command == (str(venv_python), "-m", "pip", "install", "yutori")


def test_detect_sdk_install_plan_defaults_to_user_install(tmp_path: Path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("requests\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    with patch("yutori.cli.commands.install_ui.shutil.which", return_value="/usr/bin/python3"):
        plan = detect_sdk_install_plan()

    assert plan.command == ("/usr/bin/python3", "-m", "pip", "install", "--user", "yutori")
    assert plan.default is False
    assert "requirements.txt" in plan.reason


def test_detect_sdk_install_plan_flags_missing_pip(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))

    def fake_which(command: str, path: str | None = None) -> str | None:
        return "/usr/bin/python" if command == "python" else None

    with (
        patch("yutori.cli.commands.install_ui.shutil.which", side_effect=fake_which),
        patch("yutori.cli.commands.install_ui.python_has_pip", return_value=False),
    ):
        plan = detect_sdk_install_plan()

    assert plan.availability_error == "`python -m pip` is not available in the active environment."


def test_resolve_uv_path_uses_installer_env_var(monkeypatch):
    monkeypatch.setenv("YUTORI_UV_BIN", "/tmp/custom-uv")

    with patch("yutori.cli.commands.install_ui._is_executable", return_value=True):
        resolved = resolve_uv_path()

    assert resolved == "/tmp/custom-uv"


def test_resolve_uv_path_rejects_non_executable_env_var(monkeypatch):
    monkeypatch.setenv("YUTORI_UV_BIN", "/tmp/not-executable-uv")
    # File exists but is not executable — should fall through to other lookups.
    with (
        patch("yutori.cli.commands.install_ui._is_executable", return_value=False),
        patch("yutori.cli.commands.install_ui.shutil.which", return_value="/opt/uv"),
    ):
        resolved = resolve_uv_path()

    assert resolved == "/opt/uv"


def test_resolve_npx_path_uses_path_lookup():
    with patch("yutori.cli.commands.install_ui.shutil.which", return_value="/usr/local/bin/npx") as mock_which:
        resolved = resolve_npx_path({"PATH": "/usr/local/bin"})

    assert resolved == "/usr/local/bin/npx"
    mock_which.assert_called_once_with("npx", path="/usr/local/bin")


def test_install_ui_noninteractive_skips_optional_steps():
    cli_state = CLIInstallState(
        cli_path=Path("/tmp/yutori"),
        bin_dir=Path("/tmp"),
        uv_path="/usr/bin/uv",
        version="yutori 0.7.0",
        on_path=True,
    )
    sdk_plan = SDKInstallPlan(
        reason="Detected pyproject.toml in the current directory.",
        command=("uv", "add", "yutori"),
        default=True,
    )

    with (
        patch(
            "yutori.cli.commands.install_ui.inspect_cli_install",
            return_value=(cli_state, StepResult("CLI", "success", "ok")),
        ),
        patch("yutori.cli.commands.install_ui.detect_sdk_install_plan", return_value=sdk_plan),
        patch("yutori.cli.commands.install_ui.is_interactive_terminal", return_value=False),
        patch(
            "yutori.cli.commands.install_ui.maybe_authenticate",
            return_value=(
                StepResult("Auth", "skipped", "Skipped auth because no interactive terminal is available."),
                False,
            ),
        ),
    ):
        result = runner.invoke(app, ["__install_ui"])

    assert result.exit_code == 0
    assert "Non-interactive terminal detected." in result.stdout
    assert "Skipped SDK install because no interactive" in result.stdout
    assert "Skipped auth because no interactive" in result.stdout
    # Two MCP rows render. In this fixture auth was skipped (no interactive
    # terminal), so MCP/skills hit the not-authenticated gate before the
    # non-interactive gate inside the helpers themselves.
    assert "MCP server" in result.stdout
    assert "MCP skills" in result.stdout
    assert "Authenticate first" in result.stdout
    assert "Verification requires a valid API key." in result.stdout
    # Each step should render in the summary table, including PATH (it was
    # already on PATH for this fixture).
    assert "PATH" in result.stdout
    assert "CLI" in result.stdout


def test_install_ui_exits_nonzero_when_cli_verification_fails():
    sdk_plan = SDKInstallPlan(
        reason="Detected pyproject.toml in the current directory.",
        command=("uv", "add", "yutori"),
        default=True,
    )

    with (
        patch(
            "yutori.cli.commands.install_ui.inspect_cli_install",
            return_value=(None, StepResult("CLI", "failed", "uv is not available")),
        ),
        patch("yutori.cli.commands.install_ui.is_interactive_terminal", return_value=False),
        patch("yutori.cli.commands.install_ui.detect_sdk_install_plan", return_value=sdk_plan),
        patch("yutori.cli.commands.install_ui.maybe_install_sdk", return_value=StepResult("SDK", "skipped", "skip")),
        patch(
            "yutori.cli.commands.install_ui.maybe_authenticate",
            return_value=(
                StepResult("Auth", "skipped", "Skipped auth because no interactive terminal is available."),
                False,
            ),
        ),
    ):
        result = runner.invoke(app, ["__install_ui"])

    assert result.exit_code == 1
    assert "uv is not available" in result.stdout


def test_install_ui_marks_auth_failure_when_verification_rejects_credentials():
    cli_state = CLIInstallState(
        cli_path=Path("/tmp/yutori"),
        bin_dir=Path("/tmp"),
        uv_path="/usr/bin/uv",
        version="yutori 0.7.0",
        on_path=True,
    )
    sdk_plan = SDKInstallPlan(
        reason="Detected pyproject.toml in the current directory.",
        command=("uv", "add", "yutori"),
        default=True,
    )

    with (
        patch(
            "yutori.cli.commands.install_ui.inspect_cli_install",
            return_value=(cli_state, StepResult("CLI", "success", "ok")),
        ),
        patch("yutori.cli.commands.install_ui.detect_sdk_install_plan", return_value=sdk_plan),
        patch("yutori.cli.commands.install_ui.is_interactive_terminal", return_value=False),
        patch("yutori.cli.commands.install_ui.maybe_install_sdk", return_value=StepResult("SDK", "skipped", "skip")),
        patch(
            "yutori.cli.commands.install_ui.maybe_authenticate",
            return_value=(StepResult("Auth", "success", "ok"), True),
        ),
        patch(
            "yutori.cli.commands.install_ui.run_verification",
            return_value=(StepResult("Verification", "failed", "Authentication failed during verification."), True),
        ),
    ):
        result = runner.invoke(app, ["__install_ui"])

    assert result.exit_code == 1
    assert "Saved credentials were rejected by the API" in result.stdout
    assert "Authentication failed during verification." in result.stdout


def test_maybe_repair_path_reports_shadowed_binary():
    cli_state = CLIInstallState(
        cli_path=Path("/tmp/yutori"),
        bin_dir=Path("/tmp"),
        uv_path="/usr/bin/uv",
        version="yutori 0.7.0",
        on_path=False,
        shell_cli_path=Path("/usr/local/bin/yutori"),
    )

    result = maybe_repair_path(Console(), cli_state, interactive=True)

    assert result.status == "failed"
    assert "Reorder PATH" in result.detail


def _cli_state(cli_path: Path, *, on_path: bool = True) -> CLIInstallState:
    return CLIInstallState(
        cli_path=cli_path,
        bin_dir=cli_path.parent,
        uv_path="/tmp/uv",
        version="yutori 0.7.3",
        on_path=on_path,
        shell_cli_path=cli_path if on_path else None,
    )


def test_run_verification_succeeds_via_cli(tmp_path: Path):
    cli_path = tmp_path / "yutori"
    responses = [
        subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="\nBrowsing task submitted.\n  Task ID: task-123\n  Status: queued\n",
            stderr="",
        ),
        subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="\nBrowsing Task: task-123\n\n  Status: succeeded\n\nResult:\nFound 5 team members.\n",
            stderr="",
        ),
    ]

    with (
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_command", side_effect=responses) as mock_run,
        patch("yutori.cli.commands.install_ui.time.sleep", return_value=None),
    ):
        result, auth_failed = run_verification(Console(), interactive=True, cli_state=_cli_state(cli_path))

    assert auth_failed is False
    assert result.status == "success"
    assert f"{VERIFICATION_TASK_DASHBOARD_BASE_URL}/task-123" in result.detail
    assert "succeeded" in result.detail.lower()
    # on_path=True → bare `yutori`, matching what the user would type in their shell.
    assert mock_run.call_args_list[0].args[0] == (
        "yutori",
        "browse",
        "run",
        VERIFICATION_TASK,
        VERIFICATION_URL,
        "--max-steps",
        str(VERIFICATION_MAX_STEPS),
    )
    assert mock_run.call_args_list[1].args[0] == ("yutori", "browse", "get", "task-123")


def test_run_verification_uses_absolute_path_when_not_on_path(tmp_path: Path):
    """When `yutori` is not on PATH, bare `yutori` would fail — fall back to
    the absolute cli_path for both display and execution."""
    cli_path = tmp_path / "yutori"
    response = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="\nBrowsing task submitted.\n  Task ID: task-xyz\n  Status: succeeded\n",
        stderr="",
    )
    with (
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_command", return_value=response) as mock_run,
    ):
        result, auth_failed = run_verification(
            Console(), interactive=True, cli_state=_cli_state(cli_path, on_path=False)
        )

    assert result.status == "success"
    assert auth_failed is False
    assert mock_run.call_args_list[0].args[0][0] == str(cli_path)


def test_run_verification_classifies_auth_error_as_auth_failure(tmp_path: Path):
    cli_path = tmp_path / "yutori"
    failure = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="AuthenticationError: Invalid or missing API key",
    )
    with (
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_command", return_value=failure),
    ):
        result, auth_failed = run_verification(Console(), interactive=True, cli_state=_cli_state(cli_path))

    assert auth_failed is True
    assert result.status == "failed"
    assert "Invalid or missing API key" in result.detail


def test_run_verification_non_auth_api_error_returns_auth_failed_false(tmp_path: Path):
    cli_path = tmp_path / "yutori"
    failure = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="502: Bad gateway")
    with (
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_command", return_value=failure),
    ):
        result, auth_failed = run_verification(Console(), interactive=True, cli_state=_cli_state(cli_path))

    assert auth_failed is False
    assert result.status == "failed"
    assert "502" in result.detail


def test_install_ui_skips_header_when_bootstrap_already_rendered():
    cli_state = CLIInstallState(
        cli_path=Path("/tmp/yutori"),
        bin_dir=Path("/tmp"),
        uv_path="/usr/bin/uv",
        version="yutori 0.7.0",
        on_path=True,
    )
    sdk_plan = SDKInstallPlan(reason="ok", command=("uv", "add", "yutori"), default=True)

    with (
        patch(
            "yutori.cli.commands.install_ui.inspect_cli_install",
            return_value=(cli_state, StepResult("CLI", "success", "ok")),
        ),
        patch("yutori.cli.commands.install_ui.detect_sdk_install_plan", return_value=sdk_plan),
        patch("yutori.cli.commands.install_ui.maybe_install_sdk", return_value=StepResult("SDK", "skipped", "skip")),
        patch("yutori.cli.commands.install_ui.maybe_repair_path", return_value=StepResult("PATH", "success", "ok")),
        patch(
            "yutori.cli.commands.install_ui.maybe_authenticate",
            return_value=(StepResult("Auth", "skipped", "skip"), False),
        ),
    ):
        result = runner.invoke(app, ["__install_ui"], env={"YUTORI_INSTALLER_BOOTSTRAP_SHOWN": "1"})

    assert result.exit_code == 0
    assert "> Yutori installer" not in result.stdout


# ---------------------------------------------------------------------------
# Exit-code contract (plan §4): non-auth verification failures exit 0.
# ---------------------------------------------------------------------------


def test_install_ui_exits_zero_when_verification_fails_for_non_auth_reason():
    cli_state = CLIInstallState(
        cli_path=Path("/tmp/yutori"),
        bin_dir=Path("/tmp"),
        uv_path="/usr/bin/uv",
        version="yutori 0.7.0",
        on_path=True,
    )
    sdk_plan = SDKInstallPlan(reason="ok", command=("uv", "add", "yutori"), default=True)

    with (
        patch(
            "yutori.cli.commands.install_ui.inspect_cli_install",
            return_value=(cli_state, StepResult("CLI", "success", "ok")),
        ),
        patch("yutori.cli.commands.install_ui.detect_sdk_install_plan", return_value=sdk_plan),
        patch("yutori.cli.commands.install_ui.maybe_install_sdk", return_value=StepResult("SDK", "skipped", "skip")),
        patch(
            "yutori.cli.commands.install_ui.maybe_authenticate",
            return_value=(StepResult("Auth", "success", "ok"), True),
        ),
        patch(
            "yutori.cli.commands.install_ui.run_verification",
            return_value=(StepResult("Verification", "failed", "yutori.com unreachable"), False),
        ),
    ):
        result = runner.invoke(app, ["__install_ui"])

    # The install itself worked — CLI/SDK/auth all OK. The verification task
    # failing against the live API is not a bootstrap failure.
    assert result.exit_code == 0
    assert "yutori.com unreachable" in result.stdout


# ---------------------------------------------------------------------------
# maybe_authenticate direct tests
# ---------------------------------------------------------------------------


def test_maybe_authenticate_tty_runs_login_flow():
    with (
        patch("yutori.cli.commands.install_ui.resolve_api_key", return_value=None),
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch(
            "yutori.cli.commands.install_ui.run_login_flow",
            return_value=LoginResult(success=True, api_key="yt-new-key"),
        ) as mock_flow,
    ):
        result, authenticated = maybe_authenticate(Console(), interactive=True)

    assert authenticated is True
    assert result.status == "success"
    assert mock_flow.called


def test_maybe_authenticate_tty_user_declines():
    with (
        patch("yutori.cli.commands.install_ui.resolve_api_key", return_value=None),
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=False),
        patch("yutori.cli.commands.install_ui.run_login_flow") as mock_flow,
    ):
        result, authenticated = maybe_authenticate(Console(), interactive=True)

    assert authenticated is False
    assert result.status == "skipped"
    mock_flow.assert_not_called()


def test_maybe_authenticate_tty_login_failure():
    with (
        patch("yutori.cli.commands.install_ui.resolve_api_key", return_value=None),
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch(
            "yutori.cli.commands.install_ui.run_login_flow",
            return_value=LoginResult(success=False, error="Browser timed out", auth_url="https://example/auth"),
        ),
    ):
        result, authenticated = maybe_authenticate(Console(), interactive=True)

    assert authenticated is False
    assert result.status == "failed"
    assert "Browser timed out" in result.detail


def test_maybe_authenticate_noninteractive_skips_without_calling_flow():
    with (
        patch("yutori.cli.commands.install_ui.resolve_api_key", return_value=None),
        patch("yutori.cli.commands.install_ui.run_login_flow") as mock_flow,
    ):
        result, authenticated = maybe_authenticate(Console(), interactive=False)

    assert authenticated is False
    assert result.status == "skipped"
    mock_flow.assert_not_called()


# ---------------------------------------------------------------------------
# inspect_cli_install direct tests
# ---------------------------------------------------------------------------


def test_inspect_cli_install_fails_when_uv_missing():
    with patch("yutori.cli.commands.install_ui.resolve_uv_path", return_value=None):
        state, result = inspect_cli_install()

    assert state is None
    assert result.status == "failed"
    assert "uv is not available" in result.detail


def test_inspect_cli_install_fails_when_bin_dir_missing_cli(tmp_path: Path):
    uv_dir_stdout = f"{tmp_path}\n"
    with (
        patch("yutori.cli.commands.install_ui.resolve_uv_path", return_value="/usr/bin/uv"),
        patch(
            "yutori.cli.commands.install_ui.run_command",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=uv_dir_stdout, stderr=""),
        ),
    ):
        state, result = inspect_cli_install()

    assert state is None
    assert result.status == "failed"
    assert "not found" in result.detail


def test_inspect_cli_install_success_when_shell_resolves_to_install(tmp_path: Path):
    cli_binary = tmp_path / "yutori"
    cli_binary.write_text("#!/bin/sh\necho stub\n")
    cli_binary.chmod(0o755)

    responses = [
        # uv tool dir --bin
        subprocess.CompletedProcess(args=[], returncode=0, stdout=f"{tmp_path}\n", stderr=""),
        # yutori --version
        subprocess.CompletedProcess(args=[], returncode=0, stdout="yutori 0.7.0\n", stderr=""),
    ]
    with (
        patch("yutori.cli.commands.install_ui.resolve_uv_path", return_value="/usr/bin/uv"),
        patch("yutori.cli.commands.install_ui.run_command", side_effect=responses),
        patch("yutori.cli.commands.install_ui.shutil.which", return_value=str(cli_binary)),
    ):
        state, result = inspect_cli_install()

    assert state is not None
    assert state.on_path is True
    assert state.cli_path == cli_binary
    assert result.status == "success"


# ---------------------------------------------------------------------------
# maybe_install_sdk Yes-path tests
# ---------------------------------------------------------------------------


def test_maybe_install_sdk_success_path():
    plan = SDKInstallPlan(reason="Detected pyproject.toml", command=("/tmp/uv", "add", "yutori"), default=True)
    success = subprocess.CompletedProcess(args=[], returncode=0, stdout="Installed.\n", stderr="")

    with (
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_command", return_value=success) as mock_run,
    ):
        result = maybe_install_sdk(Console(), plan, interactive=True)

    assert result.status == "success"
    assert mock_run.called
    assert mock_run.call_args.args[0] == ("/tmp/uv", "add", "yutori")


def test_maybe_install_sdk_propagates_command_failure():
    plan = SDKInstallPlan(reason="ok", command=("/tmp/uv", "add", "yutori"), default=True)
    failure = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="network down")

    with (
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_command", return_value=failure),
    ):
        result = maybe_install_sdk(Console(), plan, interactive=True)

    assert result.status == "failed"
    assert "network down" in result.detail


def test_maybe_install_sdk_respects_availability_error():
    plan = SDKInstallPlan(
        reason="ok",
        command=("python3", "-m", "pip", "install", "--user", "yutori"),
        default=False,
        availability_error="`python3 -m pip` is required.",
    )
    with (
        patch("yutori.cli.commands.install_ui.Confirm.ask") as mock_ask,
        patch("yutori.cli.commands.install_ui.run_command") as mock_run,
    ):
        result = maybe_install_sdk(Console(), plan, interactive=True)

    assert result.status == "failed"
    mock_ask.assert_not_called()
    mock_run.assert_not_called()


def test_maybe_install_sdk_skipped_beats_availability_error_when_noninteractive():
    # Regression: availability_error used to short-circuit `failed` before
    # the non-interactive skip check, so CI installs without pip bumped the
    # whole installer's exit code to 1 even though nothing would have been
    # installed anyway. Non-interactive runs must always return `skipped`.
    plan = SDKInstallPlan(
        reason="ok",
        command=("python3", "-m", "pip", "install", "--user", "yutori"),
        default=False,
        availability_error="`python3 -m pip` is required.",
    )
    result = maybe_install_sdk(Console(), plan, interactive=False)
    assert result.status == "skipped"


# ---------------------------------------------------------------------------
# MCP setup branches
# ---------------------------------------------------------------------------


def test_maybe_install_mcp_server_noninteractive_skips():
    result = maybe_install_mcp_server(Console(), interactive=False)

    assert result.status == "skipped"
    assert "no interactive terminal" in result.detail
    assert "npx add-mcp" in result.detail


def test_maybe_install_mcp_server_skips_when_npx_missing():
    with (
        patch("yutori.cli.commands.install_ui.resolve_npx_path", return_value=None),
        patch("yutori.cli.commands.install_ui.Confirm.ask") as mock_ask,
        patch("yutori.cli.commands.install_ui.run_interactive_command") as mock_run,
    ):
        result = maybe_install_mcp_server(Console(), interactive=True)

    assert result.status == "skipped"
    assert "Node.js/npx" in result.detail
    mock_ask.assert_not_called()
    mock_run.assert_not_called()


def test_maybe_install_mcp_server_runs_add_mcp_on_consent():
    success = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with (
        patch("yutori.cli.commands.install_ui.resolve_npx_path", return_value="/usr/local/bin/npx"),
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_interactive_command", return_value=success) as mock_run,
    ):
        result = maybe_install_mcp_server(Console(), interactive=True)

    assert result.status == "success"
    assert mock_run.call_args.args[0] == ("/usr/local/bin/npx", *MCP_SERVER_INSTALL_COMMAND[1:])


def test_maybe_install_mcp_server_failure_includes_retry_hint():
    # run_interactive_command inherits stdio so stdout/stderr are None in
    # production. The failure detail should still be useful — a retry hint
    # with the original (npx-prefixed) command.
    failure = subprocess.CompletedProcess(args=[], returncode=1, stdout=None, stderr=None)

    with (
        patch("yutori.cli.commands.install_ui.resolve_npx_path", return_value="/usr/local/bin/npx"),
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_interactive_command", return_value=failure),
    ):
        result = maybe_install_mcp_server(Console(), interactive=True)

    assert result.status == "failed"
    assert "status 1" in result.detail
    assert "npx add-mcp" in result.detail


def test_maybe_install_mcp_server_failure_surfaces_timeout():
    timed_out = subprocess.CompletedProcess(args=[], returncode=124, stdout=None, stderr=None)

    with (
        patch("yutori.cli.commands.install_ui.resolve_npx_path", return_value="/usr/local/bin/npx"),
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_interactive_command", return_value=timed_out),
    ):
        result = maybe_install_mcp_server(Console(), interactive=True)

    assert result.status == "failed"
    assert "Timed out" in result.detail
    assert "npx add-mcp" in result.detail


def test_maybe_install_mcp_skills_runs_global_skills_on_consent():
    success = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with (
        patch("yutori.cli.commands.install_ui.resolve_npx_path", return_value="/usr/local/bin/npx"),
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_interactive_command", return_value=success) as mock_run,
    ):
        result = maybe_install_mcp_skills(Console(), interactive=True)

    assert result.status == "success"
    assert mock_run.call_args.args[0] == ("/usr/local/bin/npx", *MCP_SKILLS_INSTALL_COMMAND[1:])


def test_maybe_install_mcp_skills_user_declines():
    with (
        patch("yutori.cli.commands.install_ui.resolve_npx_path", return_value="/usr/local/bin/npx"),
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=False),
        patch("yutori.cli.commands.install_ui.run_interactive_command") as mock_run,
    ):
        result = maybe_install_mcp_skills(Console(), interactive=True)

    assert result.status == "skipped"
    mock_run.assert_not_called()


def test_maybe_install_mcp_skills_failure_includes_skills_specific_retry_hint():
    failure = subprocess.CompletedProcess(args=[], returncode=1, stdout=None, stderr=None)

    with (
        patch("yutori.cli.commands.install_ui.resolve_npx_path", return_value="/usr/local/bin/npx"),
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_interactive_command", return_value=failure),
    ):
        result = maybe_install_mcp_skills(Console(), interactive=True)

    assert result.status == "failed"
    assert "skills add yutori-ai/yutori-mcp -g" in result.detail


def test_maybe_install_mcp_server_user_cancels_with_ctrl_c():
    cancelled = subprocess.CompletedProcess(args=[], returncode=130, stdout=None, stderr=None)

    with (
        patch("yutori.cli.commands.install_ui.resolve_npx_path", return_value="/usr/local/bin/npx"),
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_interactive_command", return_value=cancelled),
    ):
        result = maybe_install_mcp_server(Console(), interactive=True)

    assert result.status == "skipped"
    assert "Cancelled by user" in result.detail


# ---------------------------------------------------------------------------
# run_interactive_command exception mapping
# ---------------------------------------------------------------------------


def test_run_interactive_command_maps_timeout_to_124():
    expired = subprocess.TimeoutExpired(cmd="npx", timeout=1)
    with patch("yutori.cli.commands.install_ui.subprocess.run", side_effect=expired):
        result = run_interactive_command(("npx", "add-mcp", "uvx yutori-mcp"), timeout=1)

    assert result.returncode == 124
    assert result.stdout is None
    assert result.stderr is None


def test_run_interactive_command_maps_missing_binary_to_127():
    with patch(
        "yutori.cli.commands.install_ui.subprocess.run",
        side_effect=FileNotFoundError("no such file: 'npx'"),
    ):
        result = run_interactive_command(("npx", "add-mcp", "uvx yutori-mcp"))

    assert result.returncode == 127


def test_run_interactive_command_maps_oserror_to_127():
    # Bare OSError covers ENOEXEC, EMFILE, ENOMEM, etc. — fork/exec failures
    # that aren't FileNotFoundError or PermissionError. They all collapse to
    # "could not start the child" from the user's perspective.
    with patch("yutori.cli.commands.install_ui.subprocess.run", side_effect=OSError("ENOMEM")):
        result = run_interactive_command(("npx", "add-mcp", "uvx yutori-mcp"))

    assert result.returncode == 127


def test_run_interactive_command_maps_keyboard_interrupt_to_130():
    with patch("yutori.cli.commands.install_ui.subprocess.run", side_effect=KeyboardInterrupt()):
        result = run_interactive_command(("npx", "add-mcp", "uvx yutori-mcp"))

    assert result.returncode == 130


# ---------------------------------------------------------------------------
# install_ui_command: failed MCP/skills must not bump exit_code
# ---------------------------------------------------------------------------


def test_install_ui_failed_mcp_does_not_bump_exit_code():
    cli_state = CLIInstallState(
        cli_path=Path("/tmp/yutori"),
        bin_dir=Path("/tmp"),
        uv_path="/usr/bin/uv",
        version="yutori 0.7.0",
        on_path=True,
    )
    sdk_plan = SDKInstallPlan(reason="ok", command=("uv", "add", "yutori"), default=True)

    with (
        patch(
            "yutori.cli.commands.install_ui.inspect_cli_install",
            return_value=(cli_state, StepResult("CLI", "success", "ok")),
        ),
        patch("yutori.cli.commands.install_ui.detect_sdk_install_plan", return_value=sdk_plan),
        patch("yutori.cli.commands.install_ui.maybe_install_sdk", return_value=StepResult("SDK", "skipped", "skip")),
        patch(
            "yutori.cli.commands.install_ui.maybe_authenticate",
            return_value=(StepResult("Auth", "success", "ok"), True),
        ),
        patch(
            "yutori.cli.commands.install_ui.maybe_install_mcp_server",
            return_value=StepResult("MCP server", "failed", "Command exited with status 1."),
        ),
        patch(
            "yutori.cli.commands.install_ui.maybe_install_mcp_skills",
            return_value=StepResult("MCP skills", "failed", "Command exited with status 1."),
        ),
        patch(
            "yutori.cli.commands.install_ui.run_verification",
            return_value=(StepResult("Verification", "success", "ok"), False),
        ),
    ):
        result = runner.invoke(app, ["__install_ui"])

    # MCP/skills are optional; their failure surfaces in the table but
    # leaves exit_code at 0 since CLI/SDK/auth/verification all succeeded.
    assert result.exit_code == 0
    assert "MCP server" in result.stdout
    assert "MCP skills" in result.stdout
    assert "FAIL" in result.stdout


def test_install_ui_skips_mcp_when_not_authenticated():
    cli_state = CLIInstallState(
        cli_path=Path("/tmp/yutori"),
        bin_dir=Path("/tmp"),
        uv_path="/usr/bin/uv",
        version="yutori 0.7.0",
        on_path=True,
    )
    sdk_plan = SDKInstallPlan(reason="ok", command=("uv", "add", "yutori"), default=True)

    with (
        patch(
            "yutori.cli.commands.install_ui.inspect_cli_install",
            return_value=(cli_state, StepResult("CLI", "success", "ok")),
        ),
        patch("yutori.cli.commands.install_ui.detect_sdk_install_plan", return_value=sdk_plan),
        patch("yutori.cli.commands.install_ui.maybe_install_sdk", return_value=StepResult("SDK", "skipped", "skip")),
        patch(
            "yutori.cli.commands.install_ui.maybe_authenticate",
            return_value=(StepResult("Auth", "skipped", "User declined."), False),
        ),
        patch("yutori.cli.commands.install_ui.maybe_install_mcp_server") as mock_mcp,
        patch("yutori.cli.commands.install_ui.maybe_install_mcp_skills") as mock_skills,
    ):
        result = runner.invoke(app, ["__install_ui"])

    assert result.exit_code == 0
    # Neither npx step should be invoked when the user isn't authenticated —
    # registering a server that needs YUTORI_API_KEY is futile.
    mock_mcp.assert_not_called()
    mock_skills.assert_not_called()
    assert "Authenticate first" in result.stdout


# ---------------------------------------------------------------------------
# maybe_repair_path branches
# ---------------------------------------------------------------------------


def test_maybe_repair_path_happy_path():
    state = CLIInstallState(
        cli_path=Path("/tmp/yutori"),
        bin_dir=Path("/tmp"),
        uv_path="/usr/bin/uv",
        version="yutori 0.7.0",
        on_path=True,
    )

    result = maybe_repair_path(Console(), state, interactive=True)

    assert result.status == "success"


def test_maybe_repair_path_noninteractive_skips():
    state = CLIInstallState(
        cli_path=Path("/tmp/yutori"),
        bin_dir=Path("/tmp"),
        uv_path="/usr/bin/uv",
        version="yutori 0.7.0",
        on_path=False,
        shell_cli_path=None,
    )

    result = maybe_repair_path(Console(), state, interactive=False)

    assert result.status == "skipped"


def test_maybe_repair_path_runs_update_shell_on_consent():
    state = CLIInstallState(
        cli_path=Path("/tmp/yutori"),
        bin_dir=Path("/tmp"),
        uv_path="/usr/bin/uv",
        version="yutori 0.7.0",
        on_path=False,
        shell_cli_path=None,
    )
    success = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")

    with (
        patch("yutori.cli.commands.install_ui.Confirm.ask", return_value=True),
        patch("yutori.cli.commands.install_ui.run_command", return_value=success) as mock_run,
    ):
        result = maybe_repair_path(Console(), state, interactive=True)

    assert result.status == "success"
    assert mock_run.call_args.args[0] == ("/usr/bin/uv", "tool", "update-shell")


@pytest.mark.parametrize(
    "output",
    [
        "Not authenticated. Run 'yutori auth login' first.",
        "NOT AUTHENTICATED",
        "  Invalid or missing API key (401)",
        "invalid or missing api key",
        "AuthenticationError: credentials rejected",
        "HTTP 401 Unauthorized",
        "HTTP 403 Forbidden",
        "Error: 403 Forbidden when accessing resource",
        "Authentication failed: expired token detected",
        "Your key was revoked by the workspace admin",
    ],
)
def test_looks_like_auth_failure_detects_auth_signals(output: str) -> None:
    assert looks_like_auth_failure(output) is True


@pytest.mark.parametrize(
    "output",
    [
        "yutori.com: connection timed out",
        "HTTP 502 bad gateway",
        "Task rejected: not enough credits",
        "Rate limit exceeded (429)",  # 429 not in markers — different failure class
        "",
    ],
)
def test_looks_like_auth_failure_ignores_non_auth_errors(output: str) -> None:
    assert looks_like_auth_failure(output) is False
