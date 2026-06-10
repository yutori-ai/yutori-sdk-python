"""Internal helpers for the yutori.n1 -> yutori.navigator deprecation shims.

Each submodule under :mod:`yutori.n1` is a thin compatibility wrapper that
emits a :class:`DeprecationWarning` at import time and re-exports everything
from the corresponding ``yutori.navigator`` submodule. :func:`warn_renamed`
centralizes the warning text and ``stacklevel`` accounting;
:func:`alias_module_contents` does the re-export.
"""

from __future__ import annotations

import warnings
from types import ModuleType
from typing import Any


def warn_renamed(old_name: str, *, suffix: str = "") -> None:
    """Emit a DeprecationWarning announcing the ``yutori.n1`` rename.

    ``old_name`` is the importing shim's ``__name__`` (e.g.
    ``"yutori.n1.replay"`` or ``"yutori.n1"`` for the package shim itself).
    The replacement is derived by swapping the ``yutori.n1`` prefix for
    ``yutori.navigator``. ``stacklevel=3`` walks past this helper and the
    shim module body so the warning surfaces at the user's import statement.

    ``suffix`` is appended after the rename sentence; the package-level
    shim uses it to nudge callers toward updating their imports.
    """
    new_name = old_name.replace("yutori.n1", "yutori.navigator", 1)
    message = f"{old_name} has been renamed to {new_name}."
    if suffix:
        message = f"{message} {suffix}"
    warnings.warn(message, DeprecationWarning, stacklevel=3)


# Attributes that identify the shim module itself and must not be replaced
# by the target's values.
_MODULE_IDENTITY_ATTRS = frozenset(
    {
        "__name__",
        "__loader__",
        "__spec__",
        "__package__",
        "__file__",
        "__path__",
        "__builtins__",
        "__cached__",
        "__doc__",
    }
)


def alias_module_contents(shim_globals: dict[str, Any], target: ModuleType) -> None:
    """Copy every attribute of ``target`` into a shim module's namespace.

    A bare ``from target import *`` is not enough for a rename shim: it skips
    underscore-prefixed names and anything excluded by the target's
    ``__all__``, both of which were importable from the pre-rename
    ``yutori.n1`` modules (e.g. ``SupportsAsyncPageReady`` in ``page_ready``,
    which ``__all__`` omits). Copying ``vars(target)`` — including
    ``__all__`` itself, so star-imports from the shim keep the target's
    export list — preserves the original import surface exactly.
    """
    shim_globals.update(
        {name: value for name, value in vars(target).items() if name not in _MODULE_IDENTITY_ATTRS}
    )
