"""Research commands for the Yutori CLI."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.markup import escape

from yutori.cli.commands import (
    get_authenticated_client,
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
    client = get_authenticated_client()

    try:
        result = client.research.create(
            query=query,
            user_timezone=timezone,
            user_location=location,
            browser=browser,
        )

        print_task_submission_result(console, "Research", result)
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

        print_task_get_header(console, "Research", task_id, result)

        if result.get("query"):
            console.print(f"  Query: {escape(result['query'])}")
        if result.get("created_at"):
            console.print(f"  Created: {result['created_at']}")

        print_task_result_output(console, result)
    finally:
        client.close()
