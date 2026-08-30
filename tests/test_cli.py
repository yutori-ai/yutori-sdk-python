"""Tests for CLI entrypoint behavior."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner, Result

from yutori import __version__
from yutori.cli.commands import html_to_text, truncate_for_display
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


def test_html_to_text_unwraps_browsing_pre_markdown():
    # Browsing results come back as entity-escaped markdown inside <pre>.
    raw = (
        "<pre>## Summary of Findings\n\n"
        "| **Links** | &quot;Learn more&quot; \u2192 `https://iana.org/domains/example` |\n"
        "I&#x27;m done.</pre>"
    )
    assert html_to_text(raw) == (
        '## Summary of Findings\n\n| **Links** | "Learn more" \u2192 `https://iana.org/domains/example` |\nI\'m done.'
    )


def test_html_to_text_flattens_research_article_markup():
    # Research results are full HTML documents.
    raw = (
        "<article>\n  <h3>Title</h3>\n  <p>First <b>bold</b> paragraph.</p>\n"
        "  <ul><li>one</li><li>two</li></ul>\n<hr/>\n  <p>Last &amp; final.</p>\n</article>"
    )
    assert html_to_text(raw) == "Title\n\nFirst bold paragraph.\n\none\ntwo\n\nLast & final."


def test_html_to_text_keeps_table_rows_on_lines_and_separates_cells():
    raw = "<table><tr><th>Name</th><th>Title</th></tr><tr><td>Ada</td><td>Eng</td></tr></table>"
    assert html_to_text(raw) == "Name  Title\nAda  Eng"


def test_html_to_text_drops_layout_whitespace_around_pretty_printed_cells():
    # Server-rendered tables may be pretty-printed with newlines/indentation between
    # tags; that layout whitespace must not leak into the cell separator/row break.
    raw = "<table>\n<tr>\n<td>Ada</td><td>Eng</td>\n</tr>\n<tr>\n<td>Grace</td><td>Navy</td>\n</tr>\n</table>"
    assert html_to_text(raw) == "Ada  Eng\nGrace  Navy"


def test_html_to_text_keeps_significant_space_between_inline_tags():
    # A whitespace-only text node between two inline tags (e.g. </b> and <i>) is a
    # real word separator, unlike the layout whitespace around td/th cells above.
    assert html_to_text("<b>Hello</b> <i>World</i>") == "Hello World"


def test_html_to_text_drops_script_and_style_bodies():
    raw = "<p>keep</p><style>p { color: red }</style><script>alert(1)</script><p>this</p>"
    assert html_to_text(raw) == "keep\n\nthis"


def test_html_to_text_leaves_plain_text_alone():
    for plain in ("No employee info", "a < b and b > c", "set <YOUR_KEY> here", "x &amp; y stays escaped"):
        assert html_to_text(plain) == plain


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


def _invoke_cli(client: MagicMock, args: list[str]) -> Result:
    """Invoke the CLI with ``get_authenticated_client`` patched to return ``client``."""
    with patch("yutori.cli.commands.get_authenticated_client", return_value=client):
        return runner.invoke(app, args)


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

    result = _invoke_cli(
        client,
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

    result = _invoke_cli(
        client,
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

    result = _invoke_cli(
        client,
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

    result = _invoke_cli(
        client,
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

    result = _invoke_cli(client, ["browse", "get", "task-123"])

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

    result = _invoke_cli(client, ["research", "get", "research-123"])

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

    result = _invoke_cli(client, ["scouts", "get", "scout-123"])

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

    result = _invoke_cli(client, ["scouts", "list"])

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

    result = _invoke_cli(client, ["browse", "list", "--status", "succeeded"])

    assert result.exit_code == 0
    client.browsing.list.assert_called_once_with(limit=None, status="succeeded", cursor=None)
    assert "task-1" in result.stdout
    # Assert the full summary line, not just a substring that could appear elsewhere.
    assert "1 total: 0 running, 1 succeeded, 0 failed." in result.stdout
    client.close.assert_called_once()


def test_research_list_forwards_limit_and_cursor():
    client = _make_client_mock()
    client.research.list.return_value = {"tasks": []}

    result = _invoke_cli(client, ["research", "list", "--limit", "5", "--cursor", "cur-2"])

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

    result = _invoke_cli(client, ["browse", "list", "--status", "running"])

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

    result = _invoke_cli(client, ["browse", "list", "--limit", "1"])

    assert result.exit_code == 0
    assert "next-cur" in result.stdout


def test_browse_list_without_summary_omits_totals_line():
    # The summary/totals line is gated behind `if summary:`; a response with tasks
    # but no summary must still render the table without a misleading totals line.
    client = _make_client_mock()
    client.browsing.list.return_value = {
        "tasks": [{"task_id": "task-9", "query": "q", "status": "running"}]
    }

    result = _invoke_cli(client, ["browse", "list"])

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

    result = _invoke_cli(client, ["browse", "list"])

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

    result = _invoke_cli(client, ["browse", "list", "--limit", "1"])

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

    result = _invoke_cli(client, ["scouts", "list"])

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

    result = _invoke_cli(client, ["scouts", "list"])

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

    result = _invoke_cli(client, ["browse", "get", "task-123"])

    assert result.exit_code == 0
    assert "[beta]" in result.stdout


# ---------------------------------------------------------------------------
# Friendly API error handling: no tracebacks for routine failures.
# ---------------------------------------------------------------------------


def test_browse_get_renders_html_result_as_text():
    client = _make_client_mock()
    client.browsing.get.return_value = {
        "task_id": "task-123",
        "status": "succeeded",
        "result": "<pre>Summary of findings: I&#x27;m trying to find a &quot;Team&quot; page.</pre>",
    }

    result = _invoke_cli(client, ["browse", "get", "task-123"])

    assert result.exit_code == 0
    assert "Result:" in result.stdout
    assert 'Summary of findings: I\'m trying to find a "Team" page.' in result.stdout
    assert "<pre>" not in result.stdout
    assert "&#x27;" not in result.stdout


def test_research_get_renders_html_result_as_text():
    client = _make_client_mock()
    client.research.get.return_value = {
        "task_id": "research-123",
        "status": "succeeded",
        "result": "<article><h3>Heading</h3><p>Body &amp; more.</p></article>",
    }

    result = _invoke_cli(client, ["research", "get", "research-123"])

    assert result.exit_code == 0
    assert "Heading" in result.stdout
    assert "Body & more." in result.stdout
    assert "<article>" not in result.stdout


def test_browse_get_api_error_prints_message_not_traceback():
    from yutori.exceptions import APIError

    client = _make_client_mock()
    client.browsing.get.side_effect = APIError("task not found", status_code=404)

    result = _invoke_cli(client, ["browse", "get", "nope"])

    assert result.exit_code == 1
    assert "APIError" in result.stdout
    assert "task not found" in result.stdout
    assert "Traceback" not in result.stdout
    client.close.assert_called_once()


def test_usage_rejected_key_prints_auth_guidance_not_traceback():
    from yutori.exceptions import AuthenticationError

    client = _make_client_mock()
    client.get_usage.side_effect = AuthenticationError("Invalid API key or insufficient permissions (401)")

    result = _invoke_cli(client, ["usage"])

    assert result.exit_code == 1
    # "AuthenticationError" must stay in the output: the installer's
    # AUTH_FAILURE_MARKERS classify verification failures by grepping it.
    assert "AuthenticationError" in result.stdout
    # Normalize: Rich wraps the hint at terminal width. Every variant of the
    # source-tailored hint mentions 'yutori auth login'.
    assert "yutori auth login" in " ".join(result.stdout.split())
    assert "Traceback" not in result.stdout


def test_scouts_list_connection_error_prints_message_not_traceback():
    # This is the exception type a real YutoriClient call actually raises for
    # network failures: yutori/_http.py wraps every httpx.HTTPError into an
    # APIConnectionError before it leaves the SDK, so this is what cli_api_errors
    # must catch for offline/network-failure CLI usage to show a friendly message.
    from yutori.exceptions import APIConnectionError

    client = _make_client_mock()
    client.scouts.list.side_effect = APIConnectionError("Network error calling the Yutori API (ConnectError): refused")

    result = _invoke_cli(client, ["scouts", "list"])

    assert result.exit_code == 1
    assert "Network error" in result.stdout
    assert "Traceback" not in result.stdout


def test_scouts_list_raw_httpx_error_prints_message_not_traceback():
    # Defensive fallback: cli_api_errors also catches a raw httpx.HTTPError
    # directly, in case a network error ever reaches this layer unwrapped.
    import httpx

    client = _make_client_mock()
    client.scouts.list.side_effect = httpx.ConnectError("connection refused")

    result = _invoke_cli(client, ["scouts", "list"])

    assert result.exit_code == 1
    assert "Network error" in result.stdout
    assert "Traceback" not in result.stdout


def test_usage_renders_stats_from_api_response():
    from ._client_fixtures import USAGE_RESPONSE

    client = _make_client_mock()
    client.get_usage.return_value = USAGE_RESPONSE

    result = _invoke_cli(client, ["usage", "--period", "7d"])

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

    result = _invoke_cli(client, ["browse", "run", "do something", "https://example.com"])

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

    result = _invoke_cli(client, ["scouts", "create", "-q", "watch things"])

    assert result.exit_code == 1
    assert "Scout creation failed" in result.stdout
    assert "billing_limit_reached" in result.stdout
