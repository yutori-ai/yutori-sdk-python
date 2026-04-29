"""Tests for CLI entrypoint behavior."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from yutori import __version__
from yutori.cli.main import app

runner = CliRunner()


def _make_client_mock() -> MagicMock:
    """MagicMock that mimics ``YutoriClient`` as a context manager.

    The CLI wraps the client in ``with ... as client:``; the real client's
    ``__exit__`` calls ``close()`` and returns ``None`` so exceptions
    propagate. Mirror that here — returning the MagicMock from ``close()``
    would be truthy and would silently swallow exceptions in the ``with``
    block.
    """
    client = MagicMock()
    client.__enter__.return_value = client

    def _exit(*exc_info: object) -> None:
        client.close()

    client.__exit__.side_effect = _exit
    return client


def test_root_version_option():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"yutori {__version__}"


def test_version_subcommand():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"yutori {__version__}"


def test_browse_run_forwards_local_browser_and_auth():
    client = _make_client_mock()
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


def test_research_run_basic():
    client = _make_client_mock()
    client.research.create.return_value = {"task_id": "research-123", "status": "queued"}

    with patch("yutori.cli.commands.research.get_authenticated_client", return_value=client):
        result = runner.invoke(
            app,
            ["research", "run", "latest AI announcements", "--timezone", "America/Los_Angeles"],
        )

    assert result.exit_code == 0
    client.research.create.assert_called_once_with(
        query="latest AI announcements",
        user_timezone="America/Los_Angeles",
        user_location=None,
    )
    client.close.assert_called_once()
    assert "Research task submitted" in result.stdout
    assert "Rejection Reason" not in result.stdout


def test_browse_run_handles_failed_create_response():
    client = _make_client_mock()
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
    client = _make_client_mock()
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
    client = _make_client_mock()
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
    client = _make_client_mock()
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
    client = _make_client_mock()
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
    client = _make_client_mock()
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
