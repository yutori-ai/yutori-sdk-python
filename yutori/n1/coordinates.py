"""Compatibility shim for yutori.navigator.coordinates."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "yutori.n1.coordinates has been renamed to yutori.navigator.coordinates.",
    DeprecationWarning,
    stacklevel=2,
)

from yutori.navigator.coordinates import *  # noqa: F401,F403
