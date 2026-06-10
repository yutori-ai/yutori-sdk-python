"""Compatibility shim for the renamed Yutori navigator package."""

from __future__ import annotations

import importlib

from ._compat import warn_renamed

warn_renamed(__name__, suffix="Update imports to 'from yutori.navigator import ...'.")

from yutori.navigator import *  # noqa: E402,F401,F403
from yutori.navigator import __all__  # noqa: E402,F401

_SUBMODULES = frozenset(
    {
        "_assets",
        "content",
        "context",
        "coordinates",
        "hooks",
        "images",
        "keys",
        "loop",
        "models",
        "page_ready",
        "payload",
        "replay",
        "stop",
    }
)


def __getattr__(name: str):
    # The pre-rename package imported most of its submodules eagerly, so
    # attribute access like ``yutori.n1.payload`` worked right after
    # ``import yutori.n1``. Import shim submodules on demand to keep that
    # working (for every submodule) without emitting one DeprecationWarning
    # per shim on package import.
    if name in _SUBMODULES:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    # PEP 562 companion to __getattr__: keep the lazily-imported submodules
    # discoverable via dir() / autocomplete, as they were pre-rename.
    return sorted(set(globals()) | _SUBMODULES)
