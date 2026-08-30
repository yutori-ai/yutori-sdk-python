from __future__ import annotations

from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner, Result

from yutori.auth.types import AuthStatus, LoginResult
from yutori.cli.main import app

runner = CliRunner()


def _invoke_login(*, config: dict[str, Any] | None = None, **run_login_flow_kwargs: Any) -> Result:
    """Invoke ``yutori auth login`` with ``load_config``/``run_login_flow`` patched.

    ``config`` is the stored-config dict returned by ``load_config`` (``None``
    for "no saved credentials"). ``run_login_flow_kwargs`` are forwarded to
    ``patch(...)`` for ``run_login_flow`` -- pass ``return_value=...`` or
    ``side_effect=...`` depending on what the test needs to simulate.
    """
    with (
        patch("yutori.auth.credentials.load_config", return_value=config),
        patch("yutori.cli.commands.auth.run_login_flow", **run_login_flow_kwargs),
    ):
        return runner.invoke(app, ["auth", "login"])


def test_auth_login_prints_creating_account_message(monkeypatch):
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    def fake_run_login_flow(*args, **kwargs):
        kwargs["on_registration_state"]("creating_account")
        return LoginResult(success=True, api_key="yt-key")

    result = _invoke_login(side_effect=fake_run_login_flow)

    assert result.exit_code == 0
    assert "Creating account..." in result.stdout
    assert "Successfully authenticated!" in result.stdout


def test_auth_login_prints_logging_in_message(monkeypatch):
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    def fake_run_login_flow(*args, **kwargs):
        kwargs["on_registration_state"]("logging_in")
        return LoginResult(success=True, api_key="yt-key")

    result = _invoke_login(side_effect=fake_run_login_flow)

    assert result.exit_code == 0
    assert "Logging in..." in result.stdout
    assert "Successfully authenticated!" in result.stdout


def test_auth_login_surfaces_backend_error(monkeypatch):
    # Generic "backend rejected the login" path — any LoginResult failure
    # message gets surfaced to the user.
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    def fake_run_login_flow(*args, **kwargs):
        kwargs["on_registration_state"]("creating_account")
        return LoginResult(
            success=False,
            error="Authentication failed (500): backend exploded",
            auth_url="https://example.com/auth",
        )

    result = _invoke_login(side_effect=fake_run_login_flow)

    assert result.exit_code == 1
    assert "Creating account..." in result.stdout
    normalized_stdout = " ".join(result.stdout.split())
    assert "Authentication failed (500): backend exploded" in normalized_stdout
    # The callback server is shut down by the time login fails, so the auth
    # URL must not be reprinted as a (dead) recovery link; the retry hint
    # replaces it.
    assert "https://example.com/auth" not in result.stdout
    assert "yutori auth login" in normalized_stdout


def test_auth_login_ignores_placeholder_env_var(monkeypatch):
    monkeypatch.setenv("YUTORI_API_KEY", "YOUR_API_KEY")

    result = _invoke_login(return_value=LoginResult(success=True, api_key="yt-key"))

    assert result.exit_code == 0
    assert "Successfully authenticated!" in result.stdout


def test_auth_login_ignores_placeholder_config_key(monkeypatch):
    monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    result = _invoke_login(
        config={"api_key": "YOUR_API_KEY"},
        return_value=LoginResult(success=True, api_key="yt-key"),
    )

    assert result.exit_code == 0
    assert "Successfully authenticated!" in result.stdout


def test_auth_status_distinguishes_configured_from_validated_key() -> None:
    configured = AuthStatus(
        authenticated=True,
        masked_key="yt-...fake",
        source="env_var",
    )

    with patch("yutori.cli.commands.auth.get_auth_status", return_value=configured):
        result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    assert "API key configured" in result.stdout
    assert "not validated" in result.stdout
    assert "yutori usage" in result.stdout
    assert "Authenticated" not in result.stdout
