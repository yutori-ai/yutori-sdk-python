"""Compatibility shim for yutori.navigator.replay."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn("yutori.n1.replay has been renamed to yutori.navigator.replay.", DeprecationWarning, stacklevel=2)

from yutori.navigator.replay import *  # noqa: F401,F403
