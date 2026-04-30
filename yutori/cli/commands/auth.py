"""Authentication commands for the Yutori CLI."""

from __future__ import annotations

import os

import typer
from rich.console import Console
from rich.markup import escape

from yutori.auth.credentials import _is_real_key, clear_config, get_stored_api_key, load_config
from yutori.auth.flow import get_auth_status, run_login_flow

app = typer.Typer(help="Manage authentication")
console = Console()


def _print_registration_state(state: str) -> None:
    if state == "creating_account":
        console.print("[dim]Creating account...[/dim]")
    else:
        console.print("[dim]Logging in...[/dim]")


@app.command()
def login() -> None:
    """Authenticate with Yutori via browser.

    Opens your browser to log in with Clerk OAuth and saves an API key locally.
    """
    if _is_real_key(os.environ.get("YUTORI_API_KEY")):
        console.print(
            "[yellow]YUTORI_API_KEY environment variable is set — it takes precedence over saved credentials.[/yellow]"
        )
        console.print("Unset it first if you want to use browser login.")
        raise typer.Exit(1)

    if get_stored_api_key() is not None:
        console.print("[yellow]You are already authenticated.[/yellow]")
        console.print("Run [bold]yutori auth logout[/bold] first to re-authenticate.")
        raise typer.Exit(1)

    console.print("\n[bold]Opening browser for authentication...[/bold]")
    console.print("[dim]Waiting for authentication...[/dim]\n")

    result = run_login_flow(on_registration_state=_print_registration_state)

    if result.success:
        console.print("[green]Successfully authenticated![/green]")
        console.print("You can now use the Yutori CLI and SDK.")
    else:
        console.print(f"\n[red]Authentication failed: {escape(str(result.error))}[/red]")
        if result.auth_url:
            console.print(f"\n[dim]If the browser didn't open, visit:[/dim]\n  {result.auth_url}")
        raise typer.Exit(1)


@app.command()
def logout() -> None:
    """Remove stored credentials."""
    config = load_config()
    if config and config.get("api_key"):
        clear_config()
        console.print("[green]Successfully logged out.[/green]")
    else:
        console.print("[yellow]No credentials found.[/yellow]")


@app.command()
def status() -> None:
    """Show current authentication status."""
    auth_status = get_auth_status()

    if not auth_status.authenticated:
        console.print("[yellow]Not authenticated.[/yellow]")
        console.print("Run [bold]yutori auth login[/bold] to authenticate.")
        raise typer.Exit(1)

    console.print("[green]Authenticated[/green]")
    console.print(f"  API Key: {auth_status.masked_key}")

    if auth_status.source == "config_file":
        console.print(f"  Source: {auth_status.config_path}")
    elif auth_status.source == "env_var":
        console.print("  Source: YUTORI_API_KEY environment variable")
