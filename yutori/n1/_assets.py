"""Compatibility shim for yutori.navigator._assets."""

from __future__ import annotations

from ._compat import warn_renamed

warn_renamed(__name__)

from yutori.navigator._assets import *  # noqa: E402,F401,F403
