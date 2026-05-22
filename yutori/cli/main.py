"""Main entry point for the Yutori CLI."""

from __future__ import annotations

try:
    import typer
except ImportError:
    import sys

    print("Yutori CLI dependencies are missing. Reinstall with: pip install yutori")
    sys.exit(1)

from .commands import auth, browse, research, scouts, usage
from .commands.install_flow import install_flow_command

app = typer.Typer(
    name="yutori",
    help="Yutori CLI - Manage your scouts and web automation",
    no_args_is_help=True,
)

app.add_typer(auth.app, name="auth")
app.add_typer(browse.app, name="browse")
app.add_typer(research.app, name="research")
app.add_typer(scouts.app, name="scouts")
app.add_typer(usage.app, name="usage")


def _version_callback(value: bool) -> None:
    """Handle --version and exit early."""
    if value:
        from yutori import __version__

        typer.echo(f"yutori {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the CLI version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Yutori CLI root callback."""


@app.command()
def version() -> None:
    """Show the CLI version."""
    from yutori import __version__

    typer.echo(f"yutori {__version__}")


app.command("__install_flow", hidden=True)(install_flow_command)
# Backward-compat alias for cached install.sh artifacts that still invoke
# the old subcommand name. install.sh upgrades yutori via `uv tool install
# --force --upgrade` before invoking the subcommand, so a stale cached
# install.sh paired with a freshly upgraded CLI would fail without this.
# Safe to remove one SDK release after the rename has propagated.
app.command("__install_ui", hidden=True)(install_flow_command)


if __name__ == "__main__":
    app()
