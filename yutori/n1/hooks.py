"""Compatibility shim for yutori.navigator.hooks."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn("yutori.n1.hooks has been renamed to yutori.navigator.hooks.", DeprecationWarning, stacklevel=2)

from yutori.navigator.hooks import *  # noqa: F401,F403
