"""Main entry point for the Yutori CLI."""

from __future__ import annotations

import typer

from .commands import auth, scouts, usage

app = typer.Typer(
    name="yutori",
    help="Yutori CLI - Manage your scouts and web automation",
    no_args_is_help=True,
)

app.add_typer(auth.app, name="auth")
app.add_typer(scouts.app, name="scouts")
app.add_typer(usage.app, name="usage")


@app.command()
def version() -> None:
    """Show the CLI version."""
    from yutori import __version__

    typer.echo(f"yutori {__version__}")


if __name__ == "__main__":
    app()
