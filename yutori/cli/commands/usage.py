"""Usage command for the Yutori CLI."""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from ..auth.credentials import get_credentials

app = typer.Typer(help="View usage statistics")
console = Console()


def _get_client() -> Any:
    """Get an authenticated YutoriClient."""
    from yutori import YutoriClient

    creds = get_credentials()
    if not creds or not creds.get("api_key"):
        console.print("[red]Not authenticated. Run 'yutori auth login' first.[/red]")
        raise typer.Exit(1)

    return YutoriClient(api_key=creds["api_key"])


@app.callback(invoke_without_command=True)
def usage(ctx: typer.Context) -> None:
    """Show API usage statistics."""
    if ctx.invoked_subcommand is not None:
        return

    client = _get_client()

    try:
        data = client.get_usage()

        console.print("\n[bold]Usage Statistics[/bold]\n")

        if data.get("user_id"):
            console.print(f"  User ID: {data['user_id']}")
        if data.get("api_key_id"):
            console.print(f"  API Key ID: {data['api_key_id']}")

        scouts = data.get("scouts", [])
        if scouts:
            console.print(f"\n  [bold]Scouts:[/bold] {len(scouts)}")

            table = Table()
            table.add_column("ID", style="cyan")
            table.add_column("Query", max_width=40)
            table.add_column("Status")
            table.add_column("Runs")

            for scout in scouts[:10]:
                query = scout.get("query", "")
                if len(query) > 37:
                    query = query[:37] + "..."

                table.add_row(
                    scout.get("id", "")[:8] + "...",
                    query,
                    scout.get("status", ""),
                    str(scout.get("run_count", 0)),
                )

            console.print(table)

            if len(scouts) > 10:
                console.print(f"  ... and {len(scouts) - 10} more")
        else:
            console.print("\n  No scouts yet.")
    finally:
        client.close()
