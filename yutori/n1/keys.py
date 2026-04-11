"""Compatibility shim for yutori.navigator.keys."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn("yutori.n1.keys has been renamed to yutori.navigator.keys.", DeprecationWarning, stacklevel=2)

from yutori.navigator.keys import *  # noqa: F401,F403
