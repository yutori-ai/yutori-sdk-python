"""CLI command modules."""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.markup import escape

from yutori.auth.credentials import resolve_api_key

__all__ = [
    "get_authenticated_client",
    "print_rejection_reason",
    "print_task_submission_result",
]

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


def print_task_submission_result(console: Console, task_type: str, result: dict[str, Any]) -> None:
    """Print a task creation response without implying success on failed creates."""
    status = result.get("status", "N/A")
    if status == "failed":
        console.print(f"\n[red]{task_type} task failed to start.[/red]")
    else:
        console.print(f"\n[green]{task_type} task submitted.[/green]")

    console.print(f"  Task ID: {result.get('task_id', 'N/A')}")
    console.print(f"  Status: {status}")
    print_rejection_reason(console, result)
