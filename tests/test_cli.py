"""Tests for CLI entrypoint behavior."""

from unittest.mock import patch

from typer.testing import CliRunner

from yutori import __version__
from yutori.auth.types import LoginResult
from yutori.cli.main import app

runner = CliRunner()


def test_root_version_option():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"yutori {__version__}"


def test_version_subcommand():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"yutori {__version__}"


def test_auth_login_reports_account_creation(monkeypatch):
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    with (
        patch("yutori.cli.commands.auth.load_config", return_value=None),
        patch(
            "yutori.cli.commands.auth.run_login_flow",
            return_value=LoginResult(success=True, api_key="yt-key", account_created=True),
        ) as mock_run_login_flow,
    ):
        result = runner.invoke(app, ["auth", "login"])

    assert result.exit_code == 0
    assert "Account created and authenticated!" in result.stdout
    mock_run_login_flow.assert_called_once_with()


def test_auth_register_uses_register_flow(monkeypatch):
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    with (
        patch("yutori.cli.commands.auth.load_config", return_value=None),
        patch(
            "yutori.cli.commands.auth.run_register_flow",
            return_value=LoginResult(success=True, api_key="yt-key", account_created=True),
        ) as mock_run_register_flow,
    ):
        result = runner.invoke(app, ["auth", "register"])

    assert result.exit_code == 0
    assert "Successfully registered and authenticated!" in result.stdout
    mock_run_register_flow.assert_called_once_with()


def test_auth_register_without_account_created_signal_reports_authentication(monkeypatch):
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    with (
        patch("yutori.cli.commands.auth.load_config", return_value=None),
        patch(
            "yutori.cli.commands.auth.run_register_flow",
            return_value=LoginResult(success=True, api_key="yt-key", account_created=None),
        ),
    ):
        result = runner.invoke(app, ["auth", "register"])

    assert result.exit_code == 0
    assert "Successfully authenticated!" in result.stdout
    assert "Successfully registered and authenticated!" not in result.stdout


def test_auth_register_rejects_when_env_var_is_set(monkeypatch):
    monkeypatch.setenv("YUTORI_API_KEY", "yt-env-key")

    result = runner.invoke(app, ["auth", "register"])

    assert result.exit_code == 1
    assert "YUTORI_API_KEY environment variable is set" in result.stdout


def test_auth_register_surfaces_failure_with_auth_url(monkeypatch):
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    with (
        patch("yutori.cli.commands.auth.load_config", return_value=None),
        patch(
            "yutori.cli.commands.auth.run_register_flow",
            return_value=LoginResult(
                success=False,
                error="bad things happened",
                auth_url="https://auth.example/register",
            ),
        ),
    ):
        result = runner.invoke(app, ["auth", "register"])

    assert result.exit_code == 1
    assert "Registration failed: bad things happened" in result.stdout
    assert "https://auth.example/register" in result.stdout
