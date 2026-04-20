"""Browse commands for the Yutori CLI."""

from __future__ import annotations

import typer
from rich.console import Console

from yutori.cli.commands import (
    get_authenticated_client,
    print_task_get_header,
    print_task_result_output,
    print_task_submission_result,
)

app = typer.Typer(help="Run and manage browsing tasks")
console = Console()


@app.command()
def run(
    task: str = typer.Argument(help="Natural language description of the browsing task"),
    start_url: str = typer.Argument(help="URL to start browsing from"),
    max_steps: int = typer.Option(None, "--max-steps", help="Maximum number of agent steps"),
    agent: str = typer.Option(None, "--agent", help="Agent to use"),
    require_auth: bool = typer.Option(None, "--require-auth", help="Use auth-optimized browser for login flows"),
    browser: str = typer.Option(None, "--browser", help="Browser backend: cloud or local"),
) -> None:
    """Start a new browsing task."""
    with get_authenticated_client() as client:
        result = client.browsing.create(
            task=task,
            start_url=start_url,
            max_steps=max_steps,
            agent=agent,
            require_auth=require_auth,
            browser=browser,
        )

        print_task_submission_result(console, "Browsing", result)


@app.command()
def get(
    task_id: str = typer.Argument(help="The browsing task ID"),
) -> None:
    """Get the status and result of a browsing task."""
    with get_authenticated_client() as client:
        result = client.browsing.get(task_id)

        print_task_get_header(console, "Browsing", task_id, result)

        if result.get("start_url"):
            console.print(f"  Start URL: {result['start_url']}")
        if result.get("agent"):
            console.print(f"  Agent: {result['agent']}")
        if result.get("created_at"):
            console.print(f"  Created: {result['created_at']}")

        print_task_result_output(console, result)
