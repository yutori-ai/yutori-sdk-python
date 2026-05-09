"""Compatibility shim for the renamed Yutori navigator package."""

from __future__ import annotations

from ._compat import warn_renamed

warn_renamed(__name__, suffix="Update imports to 'from yutori.navigator import ...'.")

from yutori.navigator import *  # noqa: E402,F401,F403
from yutori.navigator import __all__  # noqa: E402,F401
