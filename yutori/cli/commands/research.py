"""Research commands for the Yutori CLI."""

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

app = typer.Typer(help="Run and manage research tasks")
console = Console()


@app.command("list")
def list_tasks(
    limit: int = typer.Option(None, help="Maximum number of tasks to return"),
    status: str = typer.Option(None, help="Filter by status: running, succeeded, failed"),
    cursor: str = typer.Option(None, help="Pagination cursor from a previous response"),
) -> None:
    """List your research tasks."""
    with cli_client() as client:
        result = client.research.list(limit=limit, status=status, cursor=cursor)
        print_task_list(console, "Research", result)


@app.command()
def run(
    query: str = typer.Argument(help="Natural language research query"),
    timezone: str = typer.Option(None, "--timezone", "-tz", help="e.g., America/Los_Angeles"),
    location: str = typer.Option(None, "--location", help="e.g., San Francisco, CA, US"),
) -> None:
    """Start a new research task."""
    with cli_client() as client:
        result = client.research.create(
            query=query,
            user_timezone=timezone,
            user_location=location,
        )

        if not print_task_submission_result(console, "Research", result):
            raise typer.Exit(1)


@app.command()
def get(
    task_id: str = typer.Argument(help="The research task ID"),
) -> None:
    """Get the status and result of a research task."""
    with cli_client() as client:
        result = client.research.get(task_id)

        print_task_get_header(console, "Research", task_id, result)

        print_optional_field(console, result, "query", "Query")
        print_optional_field(console, result, "created_at", "Created")

        print_task_result_output(console, result)
