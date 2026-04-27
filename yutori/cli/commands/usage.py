"""Usage command for the Yutori CLI."""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from yutori.cli.commands import get_authenticated_client, print_aligned_fields

app = typer.Typer(help="View usage statistics")
console = Console()

# Width of the longest rate-limit label ("Requests today"). Used as
# ``min_label_width`` so blocks that conditionally drop rows (e.g. when
# only "Resets at:" prints) still align to the same column.
_RATE_LIMIT_LABEL_WIDTH = len("Requests today")


@app.callback(invoke_without_command=True)
def usage(
    ctx: typer.Context,
    period: str = typer.Option("24h", help="Activity period: 24h, 7d, 30d, or 90d"),
) -> None:
    """Show API usage statistics."""
    if ctx.invoked_subcommand is not None:
        return

    with get_authenticated_client() as client:
        data = client.get_usage(period=period)

        console.print("\n[bold]Usage Statistics[/bold]\n")

        # Active scouts
        num_active = data.get("num_active_scouts", 0)
        console.print(f"  Active Scouts: {num_active}")
        active_ids = data.get("active_scout_ids", [])
        if active_ids:
            for sid in active_ids[:5]:
                console.print(f"    - {sid}")
            if len(active_ids) > 5:
                console.print(f"    ... and {len(active_ids) - 5} more")

        # Rate limits
        rate_limits = data.get("rate_limits", {})
        if rate_limits:
            console.print(f"\n  [bold]API Rate Limits[/bold] ({rate_limits.get('status', 'unknown')})")
            rate_fields: list[tuple[str, Any]] = []
            if rate_limits.get("status") == "available":
                rate_fields.extend(
                    [
                        ("Requests today", rate_limits.get("requests_today", "N/A")),
                        ("Daily limit", rate_limits.get("daily_limit", "N/A")),
                        ("Remaining", rate_limits.get("remaining_requests", "N/A")),
                    ]
                )
            rate_fields.append(("Resets at", rate_limits.get("reset_at", "N/A")))
            print_aligned_fields(console, rate_fields, min_label_width=_RATE_LIMIT_LABEL_WIDTH)

        # Navigator API rate limits (falls back to the deprecated ``n1_rate_limits`` key on older servers)
        navigator_limits = data.get("navigator_rate_limits") or data.get("n1_rate_limits") or {}
        if navigator_limits:
            console.print("\n  [bold]Navigator API Rate Limits[/bold]")
            print_aligned_fields(
                console,
                [
                    ("Requests today", navigator_limits.get("requests_today", "N/A")),
                    ("Daily limit", navigator_limits.get("daily_limit", "N/A")),
                    ("Remaining", navigator_limits.get("remaining_requests", "N/A")),
                    ("Per-second", navigator_limits.get("per_second_limit", "N/A")),
                    ("Resets at", navigator_limits.get("reset_at", "N/A")),
                ],
                min_label_width=_RATE_LIMIT_LABEL_WIDTH,
            )

        # Activity counts
        activity = data.get("activity", {})
        if activity:
            p = activity.get("period", period)
            console.print(f"\n  [bold]Activity ({p})[/bold]")

            # `navigator_calls` is the canonical key; `n1_calls` is the deprecated alias.
            navigator_calls = activity.get("navigator_calls", activity.get("n1_calls", 0))
            table = Table(show_header=True, padding=(0, 2))
            table.add_column("Metric")
            table.add_column("Count", justify="right")
            table.add_row("Scout runs", str(activity.get("scout_runs", 0)))
            table.add_row("Browsing tasks", str(activity.get("browsing_tasks", 0)))
            table.add_row("Research tasks", str(activity.get("research_tasks", 0)))
            table.add_row("Navigator API calls", str(navigator_calls))
            console.print(table)

        console.print()
