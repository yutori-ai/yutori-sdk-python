"""Compatibility shim for yutori.navigator.stop."""

from __future__ import annotations

from ._compat import warn_renamed

warn_renamed(__name__)

from yutori.navigator.stop import *  # noqa: E402,F401,F403
