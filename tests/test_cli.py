"""Tests for CLI entrypoint behavior."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from yutori import __version__
from yutori.cli.commands import truncate_for_display
from yutori.cli.main import app

runner = CliRunner()


def test_truncate_for_display_default_ellipsis_extends_length():
    # Default mode (used by browse/research/scouts list output) appends "..."
    # after the max_len-char prefix, so the result can exceed max_len by 3.
    result = truncate_for_display("a" * 100, 47)
    assert result == "a" * 47 + "..."
    assert len(result) == 50


def test_truncate_for_display_budget_includes_ellipsis_is_a_hard_cap():
    # Install-flow summaries need a hard cap: the ellipsis counts against the budget.
    result = truncate_for_display("a" * 200, 180, budget_includes_ellipsis=True)
    assert result == "a" * 177 + "..."
    assert len(result) == 180


def test_truncate_for_display_no_truncation_when_within_max_len():
    assert truncate_for_display("short", 47) == "short"
    assert truncate_for_display("short", 47, budget_includes_ellipsis=True) == "short"


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

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(
            app,
            [
                "browse",
                "run",
                "log in and continue",
                "https://example.com/login",
                "--browser",
                "local",
                "--require-auth",
            ],
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

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
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

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(
            app,
            ["browse", "run", "click the button", "https://example.com"],
        )

    # A rejected create must exit non-zero so scripts don't treat it as success.
    assert result.exit_code == 1
    assert "Browsing task failed to start" in result.stdout
    assert "Rejection Reason: billing_limit_reached" in result.stdout
    client.close.assert_called_once()


def test_research_run_handles_failed_create_response():
    client = _make_client_mock()
    client.research.create.return_value = {
        "task_id": "r-1",
        "status": "failed",
    }

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(
            app,
            ["research", "run", "latest AI announcements"],
        )

    # A rejected create must exit non-zero so scripts don't treat it as success.
    assert result.exit_code == 1
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

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
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

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
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

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
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

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["scouts", "list"])

    assert result.exit_code == 0
    assert "invalid_query" in result.stdout
    client.close.assert_called_once()


def test_browse_list_renders_tasks_and_summary():
    client = _make_client_mock()
    client.browsing.list.return_value = {
        "tasks": [
            {
                "task_id": "task-1",
                "query": "extract employees",
                "status": "succeeded",
                "created_at": "2026-06-25T21:13:08+00:00",
            }
        ],
        "total": 1,
        "summary": {"running": 0, "succeeded": 1, "failed": 0},
        "has_more": False,
    }

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["browse", "list", "--status", "succeeded"])

    assert result.exit_code == 0
    client.browsing.list.assert_called_once_with(limit=None, status="succeeded", cursor=None)
    assert "task-1" in result.stdout
    # Assert the full summary line, not just a substring that could appear elsewhere.
    assert "1 total: 0 running, 1 succeeded, 0 failed." in result.stdout
    client.close.assert_called_once()


def test_research_list_forwards_limit_and_cursor():
    client = _make_client_mock()
    client.research.list.return_value = {"tasks": []}

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["research", "list", "--limit", "5", "--cursor", "cur-2"])

    assert result.exit_code == 0
    client.research.list.assert_called_once_with(limit=5, status=None, cursor="cur-2")
    assert "No research tasks found" in result.stdout


def test_browse_list_empty_filter_still_shows_summary():
    # A status filter with no matches should still surface the account totals,
    # not just a bare "no tasks found".
    client = _make_client_mock()
    client.browsing.list.return_value = {
        "tasks": [],
        "total": 163,
        "summary": {"running": 0, "succeeded": 162, "failed": 1},
    }

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["browse", "list", "--status", "running"])

    assert result.exit_code == 0
    assert "No browsing tasks found" in result.stdout
    assert "163 total: 0 running, 162 succeeded, 1 failed." in result.stdout


def test_browse_list_shows_next_cursor_when_more_results():
    client = _make_client_mock()
    client.browsing.list.return_value = {
        "tasks": [{"task_id": "t1", "query": "q", "status": "running"}],
        "total": 2,
        "summary": {"running": 2, "succeeded": 0, "failed": 0},
        "has_more": True,
        "next_cursor": "next-cur",
    }

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["browse", "list", "--limit", "1"])

    assert result.exit_code == 0
    assert "next-cur" in result.stdout


def test_browse_list_without_summary_omits_totals_line():
    # The summary/totals line is gated behind `if summary:`; a response with tasks
    # but no summary must still render the table without a misleading totals line.
    client = _make_client_mock()
    client.browsing.list.return_value = {
        "tasks": [{"task_id": "task-9", "query": "q", "status": "running"}]
    }

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["browse", "list"])

    assert result.exit_code == 0
    assert "task-9" in result.stdout
    assert " total:" not in result.stdout


# ---------------------------------------------------------------------------
# Rich markup safety: API/user strings must render literally, never parse.
# ---------------------------------------------------------------------------


def test_browse_list_renders_markup_like_queries_literally():
    client = _make_client_mock()
    client.browsing.list.return_value = {
        "tasks": [
            {"task_id": "t1", "query": "watch [/b] page", "status": "running"},
        ]
    }

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["browse", "list"])

    # "[/b]" used to crash the whole listing with MarkupError.
    assert result.exit_code == 0
    assert "[/b]" in result.stdout


def test_browse_list_renders_markup_like_cursor_literally():
    # next_cursor is printed on a markup-enabled console.print line (not a table cell),
    # so an untrusted cursor containing markup must be escaped, not parsed.
    client = _make_client_mock()
    client.browsing.list.return_value = {
        "tasks": [{"task_id": "t1", "query": "q", "status": "running"}],
        "has_more": True,
        "next_cursor": "abc[/b]def",
    }

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["browse", "list", "--limit", "1"])

    assert result.exit_code == 0
    assert "abc[/b]def" in result.stdout


def test_scouts_list_renders_markup_like_queries_literally():
    client = _make_client_mock()
    client.scouts.list.return_value = {
        "scouts": [
            {
                "id": "scout-1",
                "query": "watch [/b] releases",
                "status": "active",
                "output_interval": 86400,
            },
            {
                "id": "scout-2",
                "query": "monitor [beta] pages",
                "status": "active",
                "output_interval": 86400,
            },
        ]
    }

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["scouts", "list"])

    # "[/b]" used to crash the whole listing with MarkupError; "[beta]" used
    # to be silently deleted by markup parsing.
    assert result.exit_code == 0
    assert "[/b]" in result.stdout
    assert "[beta]" in result.stdout


def test_scouts_list_stringifies_non_string_fields_before_escaping():
    client = _make_client_mock()
    client.scouts.list.return_value = {
        "scouts": [
            {
                "id": 123,
                "query": 456,
                "status": None,
                "output_interval": 86400,
                "rejection_reason": {"code": "[/b]"},
            }
        ]
    }

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["scouts", "list"])

    assert result.exit_code == 0
    assert "123" in result.stdout
    assert "456" in result.stdout
    assert "[/b]" in result.stdout


def test_browse_get_renders_markup_like_start_url_literally():
    client = _make_client_mock()
    client.browsing.get.return_value = {
        "task_id": "task-123",
        "status": "completed",
        "start_url": "https://example.com/[beta]/page",
    }

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["browse", "get", "task-123"])

    assert result.exit_code == 0
    assert "[beta]" in result.stdout


# ---------------------------------------------------------------------------
# Friendly API error handling: no tracebacks for routine failures.
# ---------------------------------------------------------------------------


def test_browse_get_api_error_prints_message_not_traceback():
    from yutori.exceptions import APIError

    client = _make_client_mock()
    client.browsing.get.side_effect = APIError("task not found", status_code=404)

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["browse", "get", "nope"])

    assert result.exit_code == 1
    assert "APIError" in result.stdout
    assert "task not found" in result.stdout
    assert "Traceback" not in result.stdout
    client.close.assert_called_once()


def test_usage_rejected_key_prints_auth_guidance_not_traceback():
    from yutori.exceptions import AuthenticationError

    client = _make_client_mock()
    client.get_usage.side_effect = AuthenticationError("Invalid API key or insufficient permissions (401)")

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["usage"])

    assert result.exit_code == 1
    # "AuthenticationError" must stay in the output: the installer's
    # AUTH_FAILURE_MARKERS classify verification failures by grepping it.
    assert "AuthenticationError" in result.stdout
    # Normalize: Rich wraps the hint at terminal width. Every variant of the
    # source-tailored hint mentions 'yutori auth login'.
    assert "yutori auth login" in " ".join(result.stdout.split())
    assert "Traceback" not in result.stdout


def test_scouts_list_network_error_prints_message_not_traceback():
    import httpx

    client = _make_client_mock()
    client.scouts.list.side_effect = httpx.ConnectError("connection refused")

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["scouts", "list"])

    assert result.exit_code == 1
    assert "Network error" in result.stdout
    assert "Traceback" not in result.stdout


def test_usage_renders_stats_from_api_response():
    from ._usage_fixtures import USAGE_RESPONSE

    client = _make_client_mock()
    client.get_usage.return_value = USAGE_RESPONSE

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["usage", "--period", "7d"])

    assert result.exit_code == 0
    client.get_usage.assert_called_once_with(period="7d")
    assert "Usage Statistics" in result.stdout
    assert "Active Scouts: 2" in result.stdout
    assert "Navigator API Rate Limits" in result.stdout
    assert "Navigator API calls" in result.stdout
    client.close.assert_called_once()


def test_browse_run_missing_task_id_fails():
    # An empty 2xx body becomes {} at the SDK layer; the CLI must not report
    # a task that cannot be polled as submitted.
    client = _make_client_mock()
    client.browsing.create.return_value = {}

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["browse", "run", "do something", "https://example.com"])

    assert result.exit_code == 1
    assert "returned no task ID" in result.stdout


def test_scouts_create_failed_status_exits_nonzero():
    client = _make_client_mock()
    client.scouts.create.return_value = {
        "id": "scout-9",
        "query": "watch things",
        "status": "failed",
        "rejection_reason": "billing_limit_reached",
    }

    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        result = runner.invoke(app, ["scouts", "create", "-q", "watch things"])

    assert result.exit_code == 1
    assert "Scout creation failed" in result.stdout
    assert "billing_limit_reached" in result.stdout
