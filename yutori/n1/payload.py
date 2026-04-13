"""Compatibility shim for yutori.navigator.payload."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn("yutori.n1.payload has been renamed to yutori.navigator.payload.", DeprecationWarning, stacklevel=2)

from yutori.navigator.payload import *  # noqa: F401,F403
