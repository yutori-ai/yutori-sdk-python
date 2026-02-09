"""Browse commands for the Yutori CLI."""

from __future__ import annotations

import typer
from rich.console import Console

from yutori.cli.commands import get_authenticated_client

app = typer.Typer(help="Run and manage browsing tasks")
console = Console()


@app.command()
def run(
    task: str = typer.Argument(help="Natural language description of the browsing task"),
    start_url: str = typer.Argument(help="URL to start browsing from"),
    max_steps: int = typer.Option(None, "--max-steps", help="Maximum number of agent steps"),
    agent: str = typer.Option(None, "--agent", help="Agent to use"),
    require_auth: bool = typer.Option(None, "--require-auth", help="Use auth-optimized browser for login flows"),
) -> None:
    """Start a new browsing task."""
    client = get_authenticated_client()

    try:
        result = client.browsing.create(
            task=task,
            start_url=start_url,
            max_steps=max_steps,
            agent=agent,
            require_auth=require_auth,
        )

        console.print("\n[green]Browsing task created![/green]")
        console.print(f"  Task ID: {result.get('task_id', 'N/A')}")
        console.print(f"  Status: {result.get('status', 'N/A')}")
    finally:
        client.close()


@app.command()
def get(
    task_id: str = typer.Argument(help="The browsing task ID"),
) -> None:
    """Get the status and result of a browsing task."""
    client = get_authenticated_client()

    try:
        result = client.browsing.get(task_id)

        console.print(f"\n[bold]Browsing Task: {result.get('task_id', task_id)}[/bold]\n")
        console.print(f"  Status: {result.get('status', 'N/A')}")

        if result.get("start_url"):
            console.print(f"  Start URL: {result['start_url']}")
        if result.get("agent"):
            console.print(f"  Agent: {result['agent']}")
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
