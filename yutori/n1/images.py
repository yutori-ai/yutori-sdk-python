"""Compatibility shim for yutori.navigator.images."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn("yutori.n1.images has been renamed to yutori.navigator.images.", DeprecationWarning, stacklevel=2)

from yutori.navigator.images import *  # noqa: F401,F403
