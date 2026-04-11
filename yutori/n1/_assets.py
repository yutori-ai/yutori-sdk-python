"""Compatibility shim for yutori.navigator._assets."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn("yutori.n1._assets has been renamed to yutori.navigator._assets.", DeprecationWarning, stacklevel=2)

from yutori.navigator._assets import *  # noqa: F401,F403
