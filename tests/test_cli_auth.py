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


def test_auth_login_surfaces_backend_error(monkeypatch):
    # Generic "backend rejected the login" path — any LoginResult failure
    # message gets surfaced to the user. This replaces the old test for
    # /client/register-api unavailability, which the backend no longer
    # exercises (endpoint has been live since ENG-4003 landed 2026-03).
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    def fake_run_login_flow(*args, **kwargs):
        kwargs["on_registration_state"]("creating_account")
        return LoginResult(
            success=False,
            error="Authentication failed (500): backend exploded",
            auth_url="https://example.com/auth",
        )

    with (
        patch("yutori.cli.commands.auth.load_config", return_value=None),
        patch("yutori.cli.commands.auth.run_login_flow", side_effect=fake_run_login_flow),
    ):
        result = runner.invoke(app, ["auth", "login"])

    assert result.exit_code == 1
    assert "Creating account..." in result.stdout
    normalized_stdout = " ".join(result.stdout.split())
    assert "Authentication failed (500): backend exploded" in normalized_stdout


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
