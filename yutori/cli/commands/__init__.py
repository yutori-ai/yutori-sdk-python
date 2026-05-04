"""CLI command modules."""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.markup import escape

from yutori.auth.credentials import resolve_api_key

__all__ = [
    "INTERVAL_PRESETS",
    "SECONDS_PER_DAY",
    "SECONDS_PER_HOUR",
    "SECONDS_PER_MINUTE",
    "SECONDS_PER_WEEK",
    "format_interval",
    "get_authenticated_client",
    "print_aligned_fields",
    "print_creation_result",
    "print_optional_field",
    "print_rejection_reason",
    "print_task_get_header",
    "print_task_result_output",
    "print_task_submission_result",
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


def get_authenticated_client() -> Any:
    """Get an authenticated YutoriClient, or exit with an error message."""
    from yutori.client import YutoriClient

    api_key = resolve_api_key()
    if not api_key:
        _console.print("[red]Not authenticated. Run 'yutori auth login' first.[/red]")
        raise typer.Exit(1)

    return YutoriClient(api_key=api_key)


def print_rejection_reason(console: Console, result: dict[str, Any]) -> None:
    """Print rejection_reason from an API response if present."""
    reason = result.get("rejection_reason")
    if reason:
        console.print(f"  Rejection Reason: {escape(str(reason))}")


def print_optional_field(
    console: Console,
    data: dict[str, Any],
    key: str,
    label: str,
    *,
    escape_value: bool = False,
) -> None:
    """Print ``  {label}: {data[key]}`` only when ``data[key]`` is truthy.

    Set ``escape_value=True`` for user-supplied strings that may contain
    Rich markup (e.g. ``[red]``) so they render literally.
    """
    value = data.get(key)
    if not value:
        return
    rendered = escape(str(value)) if escape_value else value
    console.print(f"  {label}: {rendered}")


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
        console.print(f"{indent_str}{(label + ':').ljust(label_width + 2)}{value}")


def print_creation_result(
    console: Console,
    result: dict[str, Any],
    *,
    success_message: str,
    failure_message: str,
    fields: list[tuple[str, Any]] | None = None,
) -> None:
    """Print a creation response: colored header, optional fields, status, rejection reason.

    The header is red on ``status == "failed"`` and green otherwise so failed
    creates do not display a misleading success banner. Each ``(label, value)``
    in ``fields`` is rendered as ``  label: value`` between the header and the
    ``Status`` line, mirroring the existing per-command output ordering.
    """
    status = result.get("status", "N/A")
    if status == "failed":
        console.print(f"\n[red]{failure_message}[/red]")
    else:
        console.print(f"\n[green]{success_message}[/green]")
    for label, value in fields or []:
        console.print(f"  {label}: {value}")
    console.print(f"  Status: {status}")
    print_rejection_reason(console, result)


def print_task_submission_result(console: Console, task_type: str, result: dict[str, Any]) -> None:
    """Print a task creation response without implying success on failed creates."""
    print_creation_result(
        console,
        result,
        success_message=f"{task_type} task submitted.",
        failure_message=f"{task_type} task failed to start.",
        fields=[("Task ID", result.get("task_id", "N/A"))],
    )


def print_task_get_header(console: Console, task_type: str, task_id: str, result: dict[str, Any]) -> None:
    """Print the common header for a task-get response: title, status, and rejection reason."""
    console.print(f"\n[bold]{task_type} Task: {result.get('task_id', task_id)}[/bold]\n")
    console.print(f"  Status: {result.get('status', 'N/A')}")
    print_rejection_reason(console, result)


def print_task_result_output(
    console: Console, result: dict[str, Any], *, max_length: int = 2000
) -> None:
    """Print the ``result``/``output`` body of a task, truncated to ``max_length`` chars."""
    output = result.get("result") or result.get("output")
    if not output:
        return
    console.print("\n[bold]Result:[/bold]")
    text = str(output)
    if len(text) > max_length:
        text = text[:max_length] + "\n... (truncated)"
    console.print(text, markup=False)


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
