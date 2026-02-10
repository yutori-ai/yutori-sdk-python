"""Tests for CLI entrypoint behavior."""

from typer.testing import CliRunner

from yutori import __version__
from yutori.cli.main import app

runner = CliRunner()


def test_root_version_option():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"yutori {__version__}"


def test_version_subcommand():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"yutori {__version__}"
