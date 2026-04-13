"""Compatibility shim for yutori.navigator.stop."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn("yutori.n1.stop has been renamed to yutori.navigator.stop.", DeprecationWarning, stacklevel=2)

from yutori.navigator.stop import *  # noqa: F401,F403
