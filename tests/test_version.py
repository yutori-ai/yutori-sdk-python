"""Tests for the shared installed-version resolution used by __init__, the CLI
installer, and the macOS driver transport (see yutori/_version.py)."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError

from yutori._version import installed_yutori_version


def test_returns_the_installed_distribution_version():
    assert installed_yutori_version() != "0.0.0+unknown"


def test_falls_back_when_the_distribution_is_not_installed(monkeypatch):
    def missing(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr("yutori._version.version", missing)

    assert installed_yutori_version() == "0.0.0+unknown"
    assert installed_yutori_version(default="unknown") == "unknown"
