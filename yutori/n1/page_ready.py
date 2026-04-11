"""Compatibility shim for yutori.navigator.page_ready."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "yutori.n1.page_ready has been renamed to yutori.navigator.page_ready.",
    DeprecationWarning,
    stacklevel=2,
)

from yutori.navigator.page_ready import *  # noqa: F401,F403
