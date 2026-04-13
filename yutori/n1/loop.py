"""Compatibility shim for yutori.navigator.loop."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn("yutori.n1.loop has been renamed to yutori.navigator.loop.", DeprecationWarning, stacklevel=2)

from yutori.navigator.loop import *  # noqa: F401,F403
