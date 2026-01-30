"""Authentication commands for the Yutori CLI."""

from __future__ import annotations

import webbrowser
from urllib.parse import urlencode

import typer
from rich.console import Console

from ..auth.callback_server import generate_state, start_callback_server
from ..auth.credentials import clear_credentials, get_credentials, save_credentials
from ..constants import CLI_AUTH_PATH, DEFAULT_CALLBACK_PORT, WEB_APP_BASE_URL

app = typer.Typer(help="Manage authentication")
console = Console()


@app.command()
def login(
    port: int = typer.Option(DEFAULT_CALLBACK_PORT, help="Local callback server port"),
) -> None:
    """Authenticate with Yutori via browser.

    Opens your browser to log in and authorize the CLI.
    """
    existing = get_credentials()
    if existing and existing.get("api_key"):
        console.print("[yellow]You are already authenticated.[/yellow]")
        console.print("Run [bold]yutori auth logout[/bold] first to re-authenticate.")
        raise typer.Exit(1)

    state = generate_state()

    params = urlencode({"state": state, "port": str(port)})
    auth_url = f"{WEB_APP_BASE_URL}{CLI_AUTH_PATH}?{params}"

    console.print("\n[bold]Opening browser for authentication...[/bold]")
    console.print(f"If it doesn't open, visit: {auth_url}\n")

    webbrowser.open(auth_url)

    console.print("[dim]Waiting for authentication...[/dim]")

    result = start_callback_server(state, port=port)

    if result.success and result.api_key:
        save_credentials(result.api_key, result.key_name or "CLI Key")
        console.print("\n[green]Successfully authenticated![/green]")
        console.print("You can now use the Yutori CLI.")
    else:
        console.print(f"\n[red]Authentication failed: {result.error}[/red]")
        raise typer.Exit(1)


@app.command()
def logout() -> None:
    """Remove stored credentials."""
    if clear_credentials():
        console.print("[green]Successfully logged out.[/green]")
    else:
        console.print("[yellow]No credentials found.[/yellow]")


@app.command()
def status() -> None:
    """Show current authentication status."""
    creds = get_credentials()

    if not creds or not creds.get("api_key"):
        console.print("[yellow]Not authenticated.[/yellow]")
        console.print("Run [bold]yutori auth login[/bold] to authenticate.")
        raise typer.Exit(1)

    api_key = creds["api_key"]
    masked_key = f"{api_key[:7]}...{api_key[-4:]}" if len(api_key) > 11 else "***"

    console.print("[green]Authenticated[/green]")
    console.print(f"  API Key: {masked_key}")

    if creds.get("key_name"):
        console.print(f"  Key Name: {creds['key_name']}")
    if creds.get("created_at"):
        console.print(f"  Created: {creds['created_at']}")

    try:
        from yutori import YutoriClient

        client = YutoriClient(api_key=api_key)
        usage = client.get_usage()
        client.close()

        if usage.get("user_id"):
            console.print(f"  User ID: {usage['user_id']}")
        console.print("\n[green]API key is valid.[/green]")
    except Exception as e:
        console.print(f"\n[yellow]Warning: Could not verify API key: {e}[/yellow]")
