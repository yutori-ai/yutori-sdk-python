"""Compatibility shim for yutori.navigator.models."""

from __future__ import annotations

import warnings as _warnings

_warnings.warn("yutori.n1.models has been renamed to yutori.navigator.models.", DeprecationWarning, stacklevel=2)

from yutori.navigator.models import *  # noqa: F401,F403
