"""Research commands for the Yutori CLI."""

from __future__ import annotations

import typer
from rich.console import Console

from yutori.cli.commands import (
    get_authenticated_client,
    print_optional_field,
    print_task_get_header,
    print_task_result_output,
    print_task_submission_result,
)

app = typer.Typer(help="Run and manage research tasks")
console = Console()


@app.command()
def run(
    query: str = typer.Argument(help="Natural language research query"),
    timezone: str = typer.Option(None, "--timezone", "-tz", help="e.g., America/Los_Angeles"),
    location: str = typer.Option(None, "--location", help="e.g., San Francisco, CA, US"),
    browser: str = typer.Option(None, "--browser", help="Browser backend: cloud or local"),
) -> None:
    """Start a new research task."""
    with get_authenticated_client() as client:
        result = client.research.create(
            query=query,
            user_timezone=timezone,
            user_location=location,
            browser=browser,
        )

        print_task_submission_result(console, "Research", result)


@app.command()
def get(
    task_id: str = typer.Argument(help="The research task ID"),
) -> None:
    """Get the status and result of a research task."""
    with get_authenticated_client() as client:
        result = client.research.get(task_id)

        print_task_get_header(console, "Research", task_id, result)

        print_optional_field(console, result, "query", "Query", escape_value=True)
        print_optional_field(console, result, "created_at", "Created")

        print_task_result_output(console, result)
