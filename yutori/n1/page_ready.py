"""Compatibility shim for yutori.navigator.page_ready."""

from __future__ import annotations

from ._compat import warn_renamed

warn_renamed(__name__)

from yutori.navigator.page_ready import *  # noqa: E402,F401,F403
