"""CLI command modules."""

from __future__ import annotations

import contextlib
from typing import Any, Iterator

import httpx
import typer
from rich.console import Console
from rich.markup import escape

from yutori.auth.credentials import resolve_api_key
from yutori.exceptions import APIError, AuthenticationError

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
    "print_aligned_fields",
    "print_creation_result",
    "print_optional_field",
    "print_rejection_reason",
    "print_task_get_header",
    "print_task_result_output",
    "print_task_submission_result",
    "safe_str",
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


def safe_str(value: Any) -> str:
    """Stringify and Rich-escape a data value so it renders literally.

    Every value that reaches a markup-enabled print must go through this (or
    a helper that calls it): API and subprocess strings can carry tokens like
    ``[beta]`` (silently eaten as markup) or ``[/x]`` (raises MarkupError),
    and non-string values would crash ``escape`` itself.
    """
    return escape(str(value))


def get_authenticated_client() -> Any:
    """Get an authenticated YutoriClient, or exit with an error message."""
    from yutori.client import YutoriClient

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
    from yutori.auth.flow import get_auth_status

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
    except httpx.HTTPError as exc:
        _console.print(f"[red]Network error: {safe_str(exc)}[/red]")
        _console.print("Check your connection and try again.")
        raise typer.Exit(1) from exc


@contextlib.contextmanager
def cli_client() -> Iterator[Any]:
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
