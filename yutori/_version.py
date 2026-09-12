"""Shared installed-package-version resolution.

The SDK's public ``__version__``, the CLI installer's banner, and the macOS
driver transport's MCP handshake all need the version of the installed
``yutori`` distribution, with the same fallback when it is not installed (e.g.
running from a source checkout without ``pip install -e .``). Centralizing
the lookup here avoids those call sites drifting out of sync -- one of them,
``yutori/navigator/macos/transport.py``, previously carried a hand-written
version string that had already gone stale.

This module deliberately imports nothing from the rest of ``yutori``: it is
loaded from deep inside the package (``yutori.navigator.macos.transport``,
reached while ``yutori/__init__.py`` itself is still executing, before
``yutori.__version__`` exists), so pulling in any yutori import here would
risk a circular import.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def installed_yutori_version(default: str = "0.0.0+unknown") -> str:
    """Return the installed ``yutori`` distribution version, or ``default``."""
    try:
        return version("yutori")
    except PackageNotFoundError:
        return default
