"""Research commands for the Yutori CLI."""

from __future__ import annotations

import typer
from rich.console import Console

from yutori.cli.commands import get_authenticated_client

app = typer.Typer(help="Run and manage research tasks")
console = Console()


@app.command()
def run(
    query: str = typer.Argument(help="Natural language research query"),
    timezone: str = typer.Option(None, "--timezone", "-tz", help="e.g., America/Los_Angeles"),
    location: str = typer.Option(None, "--location", help="e.g., San Francisco, CA, US"),
) -> None:
    """Start a new research task."""
    client = get_authenticated_client()

    try:
        result = client.research.create(
            query=query,
            user_timezone=timezone,
            user_location=location,
        )

        console.print("\n[green]Research task created![/green]")
        console.print(f"  Task ID: {result.get('task_id', 'N/A')}")
        console.print(f"  Status: {result.get('status', 'N/A')}")
    finally:
        client.close()


@app.command()
def get(
    task_id: str = typer.Argument(help="The research task ID"),
) -> None:
    """Get the status and result of a research task."""
    client = get_authenticated_client()

    try:
        result = client.research.get(task_id)

        console.print(f"\n[bold]Research Task: {result.get('task_id', task_id)}[/bold]\n")
        console.print(f"  Status: {result.get('status', 'N/A')}")

        if result.get("query"):
            console.print(f"  Query: {result['query']}")
        if result.get("created_at"):
            console.print(f"  Created: {result['created_at']}")

        output = result.get("result") or result.get("output")
        if output:
            console.print("\n[bold]Result:[/bold]")
            text = str(output)
            if len(text) > 2000:
                text = text[:2000] + "\n... (truncated)"
            console.print(text)
    finally:
        client.close()
