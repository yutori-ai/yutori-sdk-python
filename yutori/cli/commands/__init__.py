"""CLI command modules."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator
from html.parser import HTMLParser
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from yutori.auth.credentials import resolve_api_key
from yutori.auth.flow import get_auth_status
from yutori.client import YutoriClient
from yutori.exceptions import APIConnectionError, APIError, AuthenticationError

__all__ = [
    "INTERVAL_PRESETS",
    "SECONDS_PER_DAY",
    "SECONDS_PER_HOUR",
    "SECONDS_PER_MINUTE",
    "SECONDS_PER_WEEK",
    "cli_api_errors",
    "cli_client",
    "format_interval",
    "get_authenticated_client",
    "html_to_text",
    "print_aligned_fields",
    "print_creation_result",
    "print_optional_field",
    "print_rejection_reason",
    "list_and_render_tasks",
    "print_task_get_header",
    "print_task_list",
    "print_task_result_output",
    "print_task_submission_result",
    "render_entity_table",
    "safe_str",
    "truncate_for_display",
]

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE
SECONDS_PER_DAY = 24 * SECONDS_PER_HOUR
SECONDS_PER_WEEK = 7 * SECONDS_PER_DAY

INTERVAL_PRESETS: dict[str, int] = {
    "hourly": SECONDS_PER_HOUR,
    "daily": SECONDS_PER_DAY,
    "weekly": SECONDS_PER_WEEK,
}

_console = Console()


def truncate_for_display(text: str, max_len: int = 47, *, budget_includes_ellipsis: bool = False) -> str:
    """Truncate ``text`` to ``max_len`` chars, appending ``...`` when truncated.

    By default the ``...`` is appended after the ``max_len``-char prefix, so a
    truncated result can be up to 3 characters longer than ``max_len`` (this
    matches the table/field truncation used by ``browse``/``research``/``scouts``
    list output). Pass ``budget_includes_ellipsis=True`` when the caller needs a
    hard cap — the ellipsis is then counted against the budget, so the result
    never exceeds ``max_len`` characters (used for the install flow's one-line
    command-output summaries).
    """
    if len(text) <= max_len:
        return text
    if budget_includes_ellipsis:
        return f"{text[: max_len - 3]}..."
    return text[:max_len] + "..."


def safe_str(value: Any) -> str:
    """Stringify and Rich-escape a data value so it renders literally.

    Every value that reaches a markup-enabled print must go through this (or
    a helper that calls it): API and subprocess strings can carry tokens like
    ``[beta]`` (silently eaten as markup) or ``[/x]`` (raises MarkupError),
    and non-string values would crash ``escape`` itself.
    """
    return escape(str(value))


def get_authenticated_client() -> YutoriClient:
    """Get an authenticated YutoriClient, or exit with an error message."""
    api_key = resolve_api_key()
    if not api_key:
        _console.print("[red]Not authenticated. Run 'yutori auth login' first.[/red]")
        raise typer.Exit(1)

    return YutoriClient(api_key=api_key)


def _auth_recovery_hint() -> str:
    """Recovery instruction for a rejected key, tailored to where it came from.

    'Run yutori auth login' is wrong advice for an env-var key: login refuses
    to run while YUTORI_API_KEY is set, and the env var would keep overriding
    saved credentials anyway. Every variant mentions 'yutori auth login' so
    the output stays stable for callers grepping it.
    """
    source = get_auth_status().source
    if source == "env_var":
        return (
            "YUTORI_API_KEY is set but was rejected — update or unset it "
            "(while set, it overrides 'yutori auth login' credentials)."
        )
    if source == "config_file":
        return "Your saved API key was rejected. Run 'yutori auth logout', then 'yutori auth login'."
    return "Your API key was rejected. Run 'yutori auth login' to refresh credentials."


@contextlib.contextmanager
def cli_api_errors() -> Iterator[None]:
    """Convert SDK and network errors into friendly messages and exit code 1.

    Without this, a rejected key, an unknown task ID, or being offline dumps
    a multi-screen Typer traceback. The AuthenticationError class name stays
    in the output because the installer's AUTH_FAILURE_MARKERS
    (yutori/cli/commands/install_flow.py) classify failures by grepping it.

    ``APIConnectionError`` is the type real client calls actually raise for
    network failures — ``yutori/_http.py`` wraps every ``httpx.HTTPError``
    into one before it leaves the SDK. The raw ``httpx.HTTPError`` catch is
    kept alongside it as a defensive fallback for the same friendly message
    in case a network error ever reaches this layer unwrapped.
    """
    try:
        yield
    except AuthenticationError as exc:
        _console.print(f"[red]AuthenticationError: {safe_str(exc)}[/red]")
        _console.print(_auth_recovery_hint())
        raise typer.Exit(1) from exc
    except APIError as exc:
        _console.print(f"[red]APIError: {safe_str(exc)}[/red]")
        raise typer.Exit(1) from exc
    except (APIConnectionError, httpx.HTTPError) as exc:
        _console.print(f"[red]Network error: {safe_str(exc)}[/red]")
        _console.print("Check your connection and try again.")
        raise typer.Exit(1) from exc


@contextlib.contextmanager
def cli_client() -> Iterator[YutoriClient]:
    """Authenticated client with CLI error handling — the one entry point
    for API-calling commands.

    Bundles :func:`cli_api_errors` with :func:`get_authenticated_client` so a
    new command cannot accidentally take the client without the friendly
    error handling (forgetting it regresses to multi-screen tracebacks).
    """
    with cli_api_errors(), get_authenticated_client() as client:
        yield client


def print_rejection_reason(console: Console, result: dict[str, Any]) -> None:
    """Print rejection_reason from an API response if present."""
    reason = result.get("rejection_reason")
    if reason:
        console.print(f"  Rejection Reason: {safe_str(reason)}")


def print_optional_field(
    console: Console,
    data: dict[str, Any],
    key: str,
    label: str,
) -> None:
    """Print ``  {label}: {data[key]}`` only when ``data[key]`` is truthy.

    Values render literally (Rich-escaped) — no caller passes markup as data.
    """
    value = data.get(key)
    if not value:
        return
    console.print(f"  {label}: {safe_str(value)}")


def print_aligned_fields(
    console: Console,
    fields: list[tuple[str, Any]],
    *,
    indent: int = 4,
    min_label_width: int = 0,
) -> None:
    """Print ``{indent}{label}: {value}`` rows with labels padded to a common width.

    The column width is the longer of ``min_label_width`` and the longest label
    in ``fields``. Use ``min_label_width`` when only a subset of a logical block
    is rendered (e.g. one row out of several) but you still want it to line up
    with the full block elsewhere.
    """
    if not fields:
        return
    label_width = max(min_label_width, *(len(label) for label, _ in fields))
    indent_str = " " * indent
    for label, value in fields:
        console.print(f"{indent_str}{(label + ':').ljust(label_width + 2)}{safe_str(value)}")


def print_creation_result(
    console: Console,
    result: dict[str, Any],
    *,
    success_message: str,
    failure_message: str,
    fields: list[tuple[str, Any]] | None = None,
) -> bool:
    """Print a creation response: colored header, optional fields, status, rejection reason.

    The header is red on ``status == "failed"`` and green otherwise so failed
    creates do not display a misleading success banner. Each ``(label, value)``
    in ``fields`` is rendered as ``  label: value`` between the header and the
    ``Status`` line, mirroring the existing per-command output ordering.
    Field values render literally (Rich-escaped) — pass them raw.

    Returns False when the response reports a failed status, so callers can
    exit non-zero — scripts must not see a rejected create as success.
    """
    status = result.get("status", "N/A")
    failed = status == "failed"
    if failed:
        console.print(f"\n[red]{failure_message}[/red]")
    else:
        console.print(f"\n[green]{success_message}[/green]")
    for label, value in fields or []:
        console.print(f"  {label}: {safe_str(value)}")
    console.print(f"  Status: {safe_str(status)}")
    print_rejection_reason(console, result)
    return not failed


def print_task_submission_result(console: Console, task_type: str, result: dict[str, Any]) -> bool:
    """Print a task creation response; returns False when the create failed.

    A non-failed response without a ``task_id`` is also a failure: the task
    cannot be polled, so reporting success would strand the user (and any
    script keying off the exit code) with nothing to do next.
    """
    task_id = result.get("task_id")
    has_task_id = str(task_id or "").strip() not in ("", "N/A")
    if not has_task_id and result.get("status") != "failed":
        console.print(f"\n[red]{task_type} task was accepted but the API returned no task ID.[/red]")
        console.print(f"  Status: {safe_str(result.get('status', 'N/A'))}")
        print_rejection_reason(console, result)
        return False
    return print_creation_result(
        console,
        result,
        success_message=f"{task_type} task submitted.",
        failure_message=f"{task_type} task failed to start.",
        fields=[("Task ID", task_id if has_task_id else "N/A")],
    )


def print_task_get_header(console: Console, task_type: str, task_id: str, result: dict[str, Any]) -> None:
    """Print the common header for a task-get response: title, status, and rejection reason."""
    console.print(f"\n[bold]{task_type} Task: {safe_str(result.get('task_id', task_id))}[/bold]\n")
    console.print(f"  Status: {safe_str(result.get('status', 'N/A'))}")
    print_rejection_reason(console, result)


# Matches an opening or closing HTML tag. A bare "<" in prose ("a < b",
# "<YOUR_KEY>" is *not* matched because the tag name must be followed by a
# closing ">" on the same run) keeps plain-text results untouched.
_HTML_TAG_RE = re.compile(r"<(?:/?[A-Za-z][A-Za-z0-9-]*)(?:\s[^<>]*)?/?>")
# Block-level tags separate paragraphs (blank line between) when the document
# is read as text; line-level tags start a new line. Inline tags (<b>, <code>,
# <a>, ...) are dropped, keeping only their content.
_HTML_PARAGRAPH_TAGS = frozenset(
    {
        "article",
        "blockquote",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "ul",
    }
)
_HTML_LINE_TAGS = frozenset({"br", "li", "tr"})
_HTML_CELL_TAGS = frozenset({"td", "th"})
_HTML_SKIPPED_TAGS = frozenset({"script", "style"})


class _HTMLTextExtractor(HTMLParser):
    """Collect an HTML document's text with block structure turned into line breaks.

    Breaks are recorded as *pending* rather than written immediately: adjacent
    boundaries (``</li><li>``, ``</h3>\n  <p>``) collapse to the larger request
    instead of stacking, and the indentation whitespace between tags is
    dropped while a break is pending. Whitespace inside content -- markdown in
    a ``<pre>`` block, say -- is kept verbatim.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._pending_breaks = 0
        self._skip_depth = 0
        self._suppress_layout_ws = False

    def _request_break(self, tag: str) -> None:
        if tag in _HTML_PARAGRAPH_TAGS:
            self._pending_breaks = max(self._pending_breaks, 2)
        elif tag in _HTML_LINE_TAGS:
            self._pending_breaks = max(self._pending_breaks, 1)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HTML_SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag in _HTML_CELL_TAGS:
            if self._chunks and not self._pending_breaks:
                self._chunks.append("  ")  # separate cells on the same row
            self._suppress_layout_ws = True
        else:
            self._request_break(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _HTML_SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _HTML_CELL_TAGS:
            self._suppress_layout_ws = True
        else:
            self._request_break(tag)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if (self._pending_breaks or self._suppress_layout_ws) and not data.strip():
            return  # layout whitespace between tags, including around td/th cells
        if self._chunks and self._pending_breaks:
            self._chunks.append("\n" * self._pending_breaks)
        self._pending_breaks = 0
        self._suppress_layout_ws = False
        self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks).strip()


def html_to_text(text: str) -> str:
    """Render task-result HTML as terminal text; return non-HTML input unchanged.

    The API stores task results as HTML for the web dashboard: browsing
    results arrive as entity-escaped markdown inside ``<pre>``, research
    results as full ``<article>`` markup. In a terminal that shows up as
    literal ``<pre>`` and ``&#x27;``. Unescape entities, keep one line per
    block element, and drop the tags themselves.
    """
    if not _HTML_TAG_RE.search(text):
        return text
    extractor = _HTMLTextExtractor()
    extractor.feed(text)
    extractor.close()
    return extractor.text()


def print_task_result_output(console: Console, result: dict[str, Any], *, max_length: int = 2000) -> None:
    """Print the ``result``/``output`` body of a task as text, truncated to ``max_length`` chars."""
    output = result.get("result") or result.get("output")
    if not output:
        return
    console.print("\n[bold]Result:[/bold]")
    text = html_to_text(str(output))
    if len(text) > max_length:
        text = text[:max_length] + "\n... (truncated)"
    console.print(text, markup=False)


def render_entity_table(
    console: Console,
    title: str,
    items: list[dict[str, Any]],
    *,
    id_key: str,
    id_label: str,
    fourth_column_label: str,
    fourth_column_fn: Callable[[dict[str, Any]], Any],
) -> None:
    """Render the 5-column Rich table shared by task-list and scout-list output.

    Column shape: ``{id_label}`` / Query / Status / ``{fourth_column_label}`` / Reason.
    ``id_key`` selects each item's identifier field (e.g. ``"task_id"`` for
    browse/research, ``"id"`` for scouts); ``fourth_column_fn`` derives the 4th
    column's value per item (e.g. a truncated created-at date, or a formatted
    interval). Every cell goes through ``safe_str`` so API strings render
    literally. Shared by ``print_task_list`` (browse/research) and
    ``yutori scouts list``, which otherwise built structurally identical
    tables that only differed in these four spots.
    """
    table = Table(title=title)
    table.add_column(id_label, style="cyan", no_wrap=True)
    table.add_column("Query", max_width=50)
    table.add_column("Status", style="green")
    table.add_column(fourth_column_label)
    table.add_column("Reason", max_width=32)

    for item in items:
        # The list endpoints return the prompt under `query` for every entity
        # type (browse create takes it as `task`).
        query = truncate_for_display(str(item.get("query", "")))

        table.add_row(
            safe_str(item.get(id_key, "")),
            safe_str(query),
            safe_str(item.get("status", "unknown")),
            safe_str(fourth_column_fn(item)),
            safe_str(item.get("rejection_reason") or ""),
        )

    console.print(table)


def _created_at_date(item: dict[str, Any]) -> str:
    """Return just the YYYY-MM-DD date from an ISO-8601 ``created_at`` timestamp."""
    return str(item.get("created_at") or "")[:10]


def print_task_list(console: Console, task_type: str, result: dict[str, Any]) -> None:
    """Render a browsing/research task-list response as a Rich table.

    Shared by ``yutori browse list`` and ``yutori research list``: both render
    the identical task-list response shape, differing only by the title. The
    status-count totals and the ``--cursor`` hint print even when the page is
    empty, so a status filter with no matches still surfaces the account's
    totals in other statuses (rather than a bare "no tasks found"). Every cell
    goes through ``safe_str`` so API strings render literally.
    """
    tasks = result.get("tasks", [])

    if tasks:
        render_entity_table(
            console,
            f"Your {task_type} Tasks",
            tasks,
            id_key="task_id",
            id_label="Task ID",
            fourth_column_label="Created",
            fourth_column_fn=_created_at_date,
        )
    else:
        console.print(f"[yellow]No {task_type.lower()} tasks found.[/yellow]")

    summary = result.get("summary") or {}
    if summary:
        total = result.get("total", len(tasks))
        console.print(
            f"\n{safe_str(total)} total: "
            f"{safe_str(summary.get('running', 0))} running, "
            f"{safe_str(summary.get('succeeded', 0))} succeeded, "
            f"{safe_str(summary.get('failed', 0))} failed."
        )

    next_cursor = result.get("next_cursor")
    if result.get("has_more") and next_cursor:
        console.print(f"More results available. Re-run with --cursor {safe_str(next_cursor)}")


def list_and_render_tasks(
    console: Console,
    client: YutoriClient,
    namespace: str,
    task_type: str,
    *,
    limit: int | None,
    status: str | None,
    cursor: str | None,
) -> None:
    """Fetch and render a task list -- the shared body of ``browse list`` and ``research list``.

    ``namespace`` selects the client attribute to call ``.list(...)`` on (e.g.
    ``"browsing"`` or ``"research"``); ``task_type`` is the display title passed
    through to ``print_task_list``.
    """
    result = getattr(client, namespace).list(limit=limit, status=status, cursor=cursor)
    print_task_list(console, task_type, result)


def format_interval(seconds: int, *, short: bool = False) -> str:
    """Format an interval in seconds as a human-readable string.

    Picks the coarsest unit (days/hours/minutes) that fits and truncates.

    Args:
        seconds: Interval length in seconds.
        short: If True, use compact form (e.g. ``"1d"``). Otherwise use the
            verbose form (e.g. ``"1 day(s)"``).
    """
    if seconds >= SECONDS_PER_DAY:
        value, unit_short, unit_long = seconds // SECONDS_PER_DAY, "d", "day(s)"
    elif seconds >= SECONDS_PER_HOUR:
        value, unit_short, unit_long = seconds // SECONDS_PER_HOUR, "h", "hour(s)"
    else:
        value, unit_short, unit_long = seconds // SECONDS_PER_MINUTE, "m", "minute(s)"
    return f"{value}{unit_short}" if short else f"{value} {unit_long}"
