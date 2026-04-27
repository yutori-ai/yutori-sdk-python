"""Scouts commands for the Yutori CLI."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from yutori.cli.commands import (
    format_interval,
    get_authenticated_client,
    print_optional_field,
    print_rejection_reason,
)

app = typer.Typer(help="Manage scouts")
console = Console()


@app.command("list")
def list_scouts(
    limit: int = typer.Option(None, help="Maximum number of scouts to return"),
    status: str = typer.Option(None, help="Filter by status: active, paused, done"),
) -> None:
    """List your scouts."""
    with get_authenticated_client() as client:
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
        table.add_column("Reason", max_width=32)

        for scout in scouts:
            interval_str = format_interval(scout.get("output_interval") or 0, short=True)

            query = scout.get("query", "")
            if len(query) > 47:
                query = query[:47] + "..."

            table.add_row(
                scout.get("id", ""),
                query,
                scout.get("status", "unknown"),
                interval_str,
                escape(scout.get("rejection_reason") or ""),
            )

        console.print(table)


@app.command()
def get(
    scout_id: str = typer.Argument(help="The scout ID"),
) -> None:
    """Get details of a specific scout."""
    with get_authenticated_client() as client:
        scout = client.scouts.get(scout_id)

        console.print(f"\n[bold]Scout: {scout.get('id', scout_id)}[/bold]\n")
        console.print(f"  Query: {escape(scout.get('query', 'N/A'))}")
        console.print(f"  Status: {scout.get('status', 'N/A')}")
        print_rejection_reason(console, scout)

        interval_str = format_interval(scout.get("output_interval") or 0)
        console.print(f"  Interval: {interval_str}")

        print_optional_field(console, scout, "user_timezone", "Timezone")
        print_optional_field(console, scout, "created_at", "Created")
        print_optional_field(console, scout, "next_run_at", "Next Run")


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
    output_interval = interval_map.get(interval.lower())
    if output_interval is None:
        console.print(f"[red]Invalid interval '{escape(interval)}'. Choose from: hourly, daily, weekly[/red]")
        raise typer.Exit(1)

    with get_authenticated_client() as client:
        result = client.scouts.create(
            query=query,
            output_interval=output_interval,
            user_timezone=timezone,
        )

        status = result.get("status", "N/A")
        if status == "failed":
            console.print("\n[red]Scout creation failed.[/red]")
        else:
            console.print("\n[green]Scout created successfully![/green]")
        console.print(f"  ID: {result.get('id', 'N/A')}")
        console.print(f"  Query: {escape(result.get('query', query))}")
        console.print(f"  Status: {status}")
        print_rejection_reason(console, result)


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

    with get_authenticated_client() as client:
        client.scouts.delete(scout_id)
        console.print(f"[green]Scout {scout_id} deleted.[/green]")
