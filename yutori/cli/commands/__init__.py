"""CLI command modules."""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console

from yutori.auth.credentials import resolve_api_key

_console = Console()


def get_authenticated_client() -> Any:
    """Get an authenticated YutoriClient, or exit with an error message."""
    from yutori.client import YutoriClient

    api_key = resolve_api_key()
    if not api_key:
        _console.print("[red]Not authenticated. Run 'yutori auth login' first.[/red]")
        raise typer.Exit(1)

    return YutoriClient(api_key=api_key)
