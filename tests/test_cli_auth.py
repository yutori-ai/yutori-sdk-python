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
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    def fake_run_login_flow(*args, **kwargs):
        kwargs["on_registration_state"]("creating_account")
        return LoginResult(
            success=False,
            error="This CLI version requires backend support for /client/register-api.",
            auth_url="https://example.com/auth",
        )

    with (
        patch("yutori.cli.commands.auth.load_config", return_value=None),
        patch("yutori.cli.commands.auth.run_login_flow", side_effect=fake_run_login_flow),
    ):
        result = runner.invoke(app, ["auth", "login"])

    assert result.exit_code == 1
    assert "Creating account..." in result.stdout
    assert "/client/register-api" in result.stdout
