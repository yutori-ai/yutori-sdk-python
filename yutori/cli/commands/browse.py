"""Browse commands for the Yutori CLI."""

from __future__ import annotations

import typer
from rich.console import Console

from yutori.cli.commands import (
    cli_client,
    print_optional_field,
    print_task_get_header,
    print_task_list,
    print_task_result_output,
    print_task_submission_result,
)

app = typer.Typer(help="Run and manage browsing tasks")
console = Console()


@app.command("list")
def list_tasks(
    limit: int = typer.Option(None, help="Maximum number of tasks to return"),
    status: str = typer.Option(None, help="Filter by status: running, succeeded, failed"),
    cursor: str = typer.Option(None, help="Pagination cursor from a previous response"),
) -> None:
    """List your browsing tasks."""
    with cli_client() as client:
        result = client.browsing.list(limit=limit, status=status, cursor=cursor)
        print_task_list(console, "Browsing", result)


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
    with cli_client() as client:
        result = client.browsing.create(
            task=task,
            start_url=start_url,
            max_steps=max_steps,
            agent=agent,
            require_auth=require_auth,
            browser=browser,
        )

        if not print_task_submission_result(console, "Browsing", result):
            raise typer.Exit(1)


@app.command()
def get(
    task_id: str = typer.Argument(help="The browsing task ID"),
) -> None:
    """Get the status and result of a browsing task."""
    with cli_client() as client:
        result = client.browsing.get(task_id)

        print_task_get_header(console, "Browsing", task_id, result)

        print_optional_field(console, result, "start_url", "Start URL")
        print_optional_field(console, result, "agent", "Agent")
        print_optional_field(console, result, "created_at", "Created")

        print_task_result_output(console, result)
