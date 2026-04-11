"""Compatibility shim for yutori.navigator.content."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn("yutori.n1.content has been renamed to yutori.navigator.content.", DeprecationWarning, stacklevel=2)

from yutori.navigator.content import *  # noqa: F401,F403
