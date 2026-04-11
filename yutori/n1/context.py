"""Compatibility shim for yutori.navigator.context."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn("yutori.n1.context has been renamed to yutori.navigator.context.", DeprecationWarning, stacklevel=2)

from yutori.navigator.context import *  # noqa: F401,F403
