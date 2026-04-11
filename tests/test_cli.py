"""Tests for CLI entrypoint behavior."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from yutori import __version__
from yutori.cli.commands import format_interval
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


def test_browse_run_forwards_local_browser_and_auth():
    client = MagicMock()
    client.browsing.create.return_value = {"task_id": "task-123", "status": "queued"}

    with patch("yutori.cli.commands.browse.get_authenticated_client", return_value=client):
        result = runner.invoke(
            app,
            ["browse", "run", "log in and continue", "https://example.com/login", "--browser", "local", "--require-auth"],
        )

    assert result.exit_code == 0
    client.browsing.create.assert_called_once_with(
        task="log in and continue",
        start_url="https://example.com/login",
        max_steps=None,
        agent=None,
        require_auth=True,
        browser="local",
    )
    client.close.assert_called_once()
    assert "Browsing task submitted" in result.stdout
    assert "Rejection Reason" not in result.stdout


def test_research_run_forwards_local_browser():
    client = MagicMock()
    client.research.create.return_value = {"task_id": "research-123", "status": "queued"}

    with patch("yutori.cli.commands.research.get_authenticated_client", return_value=client):
        result = runner.invoke(
            app,
            ["research", "run", "latest AI announcements", "--browser", "local", "--timezone", "America/Los_Angeles"],
        )

    assert result.exit_code == 0
    client.research.create.assert_called_once_with(
        query="latest AI announcements",
        user_timezone="America/Los_Angeles",
        user_location=None,
        browser="local",
    )
    client.close.assert_called_once()
    assert "Research task submitted" in result.stdout
    assert "Rejection Reason" not in result.stdout


def test_browse_run_handles_failed_create_response():
    client = MagicMock()
    client.browsing.create.return_value = {
        "task_id": "task-123",
        "status": "failed",
        "rejection_reason": "billing_limit_reached",
    }

    with patch("yutori.cli.commands.browse.get_authenticated_client", return_value=client):
        result = runner.invoke(
            app,
            ["browse", "run", "click the button", "https://example.com"],
        )

    assert result.exit_code == 0
    assert "Browsing task failed to start" in result.stdout
    assert "Rejection Reason: billing_limit_reached" in result.stdout
    client.close.assert_called_once()


def test_research_run_handles_failed_create_response():
    client = MagicMock()
    client.research.create.return_value = {
        "task_id": "r-1",
        "status": "failed",
    }

    with patch("yutori.cli.commands.research.get_authenticated_client", return_value=client):
        result = runner.invoke(
            app,
            ["research", "run", "latest AI announcements"],
        )

    assert result.exit_code == 0
    assert "Research task failed to start" in result.stdout
    assert "Rejection Reason" not in result.stdout
    client.close.assert_called_once()


def test_browse_get_shows_rejection_reason():
    client = MagicMock()
    client.browsing.get.return_value = {
        "task_id": "task-123",
        "status": "failed",
        "rejection_reason": "billing_limit_reached",
    }

    with patch("yutori.cli.commands.browse.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["browse", "get", "task-123"])

    assert result.exit_code == 0
    assert "Rejection Reason: billing_limit_reached" in result.stdout
    client.close.assert_called_once()


def test_research_get_shows_rejection_reason():
    client = MagicMock()
    client.research.get.return_value = {
        "task_id": "research-123",
        "status": "failed",
        "rejection_reason": "rate_limit_exceeded",
    }

    with patch("yutori.cli.commands.research.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["research", "get", "research-123"])

    assert result.exit_code == 0
    assert "Rejection Reason: rate_limit_exceeded" in result.stdout
    client.close.assert_called_once()


def test_scouts_get_shows_rejection_reason():
    client = MagicMock()
    client.scouts.get.return_value = {
        "id": "scout-123",
        "query": "monitor releases",
        "status": "paused",
        "rejection_reason": "invalid_query",
    }

    with patch("yutori.cli.commands.scouts.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["scouts", "get", "scout-123"])

    assert result.exit_code == 0
    assert "Rejection Reason: invalid_query" in result.stdout
    client.close.assert_called_once()


def test_scouts_list_shows_rejection_reason_column():
    client = MagicMock()
    client.scouts.list.return_value = {
        "scouts": [
            {
                "id": "scout-123",
                "query": "monitor releases",
                "status": "paused",
                "output_interval": 3600,
                "rejection_reason": "invalid_query",
            }
        ]
    }

    with patch("yutori.cli.commands.scouts.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["scouts", "list"])

    assert result.exit_code == 0
    assert "invalid_query" in result.stdout
    client.close.assert_called_once()


@pytest.mark.parametrize(
    "seconds, compact, expected",
    [
        (86400, True, "1d"),
        (86400, False, "1 day(s)"),
        (172800, True, "2d"),
        (172800, False, "2 day(s)"),
        (3600, True, "1h"),
        (3600, False, "1 hour(s)"),
        (7200, True, "2h"),
        (7200, False, "2 hour(s)"),
        (60, True, "1m"),
        (60, False, "1 minute(s)"),
        (300, True, "5m"),
        (300, False, "5 minute(s)"),
        (0, True, "0m"),
        (0, False, "0 minute(s)"),
    ],
)
def test_format_interval(seconds: int, compact: bool, expected: str) -> None:
    assert format_interval(seconds, compact=compact) == expected
