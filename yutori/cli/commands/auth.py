"""Authentication commands for the Yutori CLI."""

from __future__ import annotations

import typer
from rich.console import Console

from yutori.auth import clear_config, get_auth_status, run_login_flow
from yutori.auth.credentials import load_config

app = typer.Typer(help="Manage authentication")
console = Console()


@app.command()
def login() -> None:
    """Authenticate with Yutori via browser.

    Opens your browser to log in with Clerk OAuth and saves an API key locally.
    """
    config = load_config()
    existing_key = config.get("api_key") if config else None
    if existing_key and isinstance(existing_key, str):
        console.print("[yellow]You are already authenticated.[/yellow]")
        console.print("Run [bold]yutori auth logout[/bold] first to re-authenticate.")
        raise typer.Exit(1)

    console.print("\n[bold]Opening browser for authentication...[/bold]")
    console.print("[dim]Waiting for authentication...[/dim]\n")

    result = run_login_flow()

    if result.success:
        console.print("[green]Successfully authenticated![/green]")
        console.print("You can now use the Yutori CLI and SDK.")
    else:
        console.print(f"\n[red]Authentication failed: {result.error}[/red]")
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
