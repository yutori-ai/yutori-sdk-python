"""Internal helper for the yutori.n1 -> yutori.navigator deprecation shims.

Each submodule under :mod:`yutori.n1` is a thin compatibility wrapper that
emits a :class:`DeprecationWarning` at import time and re-exports everything
from the corresponding ``yutori.navigator`` submodule. :func:`warn_renamed`
centralizes the warning text and ``stacklevel`` accounting so each shim only
declares its own name and the star-import.
"""

from __future__ import annotations

import warnings


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
