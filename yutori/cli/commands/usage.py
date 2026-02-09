"""Usage command for the Yutori CLI."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from yutori.cli.commands import get_authenticated_client

app = typer.Typer(help="View usage statistics")
console = Console()


@app.callback(invoke_without_command=True)
def usage(ctx: typer.Context) -> None:
    """Show API usage statistics."""
    if ctx.invoked_subcommand is not None:
        return

    client = get_authenticated_client()

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

                scout_id = scout.get("id", "")
                table.add_row(
                    scout_id,
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
