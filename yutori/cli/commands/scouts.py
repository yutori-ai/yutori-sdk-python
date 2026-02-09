"""Scouts commands for the Yutori CLI."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from yutori.cli.commands import get_authenticated_client

app = typer.Typer(help="Manage scouts")
console = Console()


@app.command("list")
def list_scouts(
    limit: int = typer.Option(None, help="Maximum number of scouts to return"),
    status: str = typer.Option(None, help="Filter by status: active, paused, done"),
) -> None:
    """List your scouts."""
    client = get_authenticated_client()

    try:
        result = client.scouts.list(limit=limit, status=status)
        scouts = result.get("scouts", [])

        if not scouts:
            console.print("[yellow]No scouts found.[/yellow]")
            return

        table = Table(title="Your Scouts")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Query", max_width=50)
        table.add_column("Status", style="green")
        table.add_column("Interval")

        for scout in scouts:
            interval_secs = scout.get("output_interval", 0)
            if interval_secs >= 86400:
                interval_str = f"{interval_secs // 86400}d"
            elif interval_secs >= 3600:
                interval_str = f"{interval_secs // 3600}h"
            else:
                interval_str = f"{interval_secs // 60}m"

            query = scout.get("query", "")
            if len(query) > 47:
                query = query[:47] + "..."

            table.add_row(
                scout.get("id", ""),
                query,
                scout.get("status", "unknown"),
                interval_str,
            )

        console.print(table)
    finally:
        client.close()


@app.command()
def get(
    scout_id: str = typer.Argument(help="The scout ID"),
) -> None:
    """Get details of a specific scout."""
    client = get_authenticated_client()

    try:
        scout = client.scouts.get(scout_id)

        console.print(f"\n[bold]Scout: {scout.get('id', scout_id)}[/bold]\n")
        console.print(f"  Query: {scout.get('query', 'N/A')}")
        console.print(f"  Status: {scout.get('status', 'N/A')}")

        interval_secs = scout.get("output_interval", 0)
        if interval_secs >= 86400:
            interval_str = f"{interval_secs // 86400} day(s)"
        elif interval_secs >= 3600:
            interval_str = f"{interval_secs // 3600} hour(s)"
        else:
            interval_str = f"{interval_secs // 60} minute(s)"
        console.print(f"  Interval: {interval_str}")

        if scout.get("user_timezone"):
            console.print(f"  Timezone: {scout['user_timezone']}")
        if scout.get("created_at"):
            console.print(f"  Created: {scout['created_at']}")
        if scout.get("next_run_at"):
            console.print(f"  Next Run: {scout['next_run_at']}")
    finally:
        client.close()


@app.command()
def create(
    query: str = typer.Option(None, "--query", "-q", help="What to monitor"),
    interval: str = typer.Option("daily", "--interval", "-i", help="Run interval: hourly, daily, weekly"),
    timezone: str = typer.Option(None, "--timezone", "-tz", help="e.g., America/Los_Angeles"),
) -> None:
    """Create a new scout."""
    if not query:
        query = typer.prompt("What would you like to monitor?")

    interval_map = {"hourly": 3600, "daily": 86400, "weekly": 604800}
    output_interval = interval_map.get(interval.lower(), 86400)

    client = get_authenticated_client()

    try:
        result = client.scouts.create(
            query=query,
            output_interval=output_interval,
            user_timezone=timezone,
        )

        console.print("\n[green]Scout created successfully![/green]")
        console.print(f"  ID: {result.get('id', 'N/A')}")
        console.print(f"  Query: {result.get('query', query)}")
        console.print(f"  Status: {result.get('status', 'N/A')}")
    finally:
        client.close()


@app.command()
def delete(
    scout_id: str = typer.Argument(help="The scout ID to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Delete a scout."""
    if not force:
        confirm = typer.confirm(f"Are you sure you want to delete scout {scout_id}?")
        if not confirm:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)

    client = get_authenticated_client()

    try:
        client.scouts.delete(scout_id)
        console.print(f"[green]Scout {scout_id} deleted.[/green]")
    finally:
        client.close()
