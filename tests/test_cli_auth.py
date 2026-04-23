from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from yutori.auth.types import LoginResult
from yutori.cli.main import app

runner = CliRunner()


def test_auth_login_prints_creating_account_message(monkeypatch):
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    def fake_run_login_flow(*args, **kwargs):
        kwargs["on_registration_state"]("creating_account")
        return LoginResult(success=True, api_key="yt-key")

    with (
        patch("yutori.cli.commands.auth.load_config", return_value=None),
        patch("yutori.cli.commands.auth.run_login_flow", side_effect=fake_run_login_flow),
    ):
        result = runner.invoke(app, ["auth", "login"])

    assert result.exit_code == 0
    assert "Creating account..." in result.stdout
    assert "Successfully authenticated!" in result.stdout


def test_auth_login_prints_logging_in_message(monkeypatch):
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    def fake_run_login_flow(*args, **kwargs):
        kwargs["on_registration_state"]("logging_in")
        return LoginResult(success=True, api_key="yt-key")

    with (
        patch("yutori.cli.commands.auth.load_config", return_value=None),
        patch("yutori.cli.commands.auth.run_login_flow", side_effect=fake_run_login_flow),
    ):
        result = runner.invoke(app, ["auth", "login"])

    assert result.exit_code == 0
    assert "Logging in..." in result.stdout
    assert "Successfully authenticated!" in result.stdout


def test_auth_login_surfaces_backend_incompatibility(monkeypatch):
    from yutori.auth.flow import REGISTER_INCOMPATIBLE_ERROR

    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    def fake_run_login_flow(*args, **kwargs):
        kwargs["on_registration_state"]("creating_account")
        return LoginResult(
            success=False,
            error=REGISTER_INCOMPATIBLE_ERROR,
            auth_url="https://example.com/auth",
        )

    with (
        patch("yutori.cli.commands.auth.load_config", return_value=None),
        patch("yutori.cli.commands.auth.run_login_flow", side_effect=fake_run_login_flow),
    ):
        result = runner.invoke(app, ["auth", "login"])

    assert result.exit_code == 1
    assert "Creating account..." in result.stdout
    # Rich may wrap long error lines, so substring-match on the normalized
    # stdout rather than the literal message. Source the message from the
    # module so renaming/rewording doesn't leave this test mocking a
    # hypothetical error the CLI never actually emits.
    normalized_stdout = " ".join(result.stdout.split())
    normalized_error = " ".join(REGISTER_INCOMPATIBLE_ERROR.split())
    assert normalized_error in normalized_stdout
    assert "out of sync" in normalized_stdout


def test_auth_login_ignores_placeholder_env_var(monkeypatch):
    monkeypatch.setenv("YUTORI_API_KEY", "YOUR_API_KEY")

    with (
        patch("yutori.cli.commands.auth.load_config", return_value=None),
        patch("yutori.cli.commands.auth.run_login_flow", return_value=LoginResult(success=True, api_key="yt-key")),
    ):
        result = runner.invoke(app, ["auth", "login"])

    assert result.exit_code == 0
    assert "Successfully authenticated!" in result.stdout


def test_auth_login_ignores_placeholder_config_key(monkeypatch):
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    with (
        patch("yutori.cli.commands.auth.load_config", return_value={"api_key": "YOUR_API_KEY"}),
        patch("yutori.cli.commands.auth.run_login_flow", return_value=LoginResult(success=True, api_key="yt-key")),
    ):
        result = runner.invoke(app, ["auth", "login"])

    assert result.exit_code == 0
    assert "Successfully authenticated!" in result.stdout
