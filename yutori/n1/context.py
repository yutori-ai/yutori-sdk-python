"""Compatibility shim for yutori.navigator.context."""

from __future__ import annotations

from ._compat import warn_renamed

warn_renamed(__name__)

from yutori.navigator.context import *  # noqa: E402,F401,F403
