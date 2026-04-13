"""Compatibility shim for the renamed Yutori navigator package."""

from __future__ import annotations

import warnings

warnings.warn(
    "yutori.n1 has been renamed to yutori.navigator. Update imports to 'from yutori.navigator import ...'.",
    DeprecationWarning,
    stacklevel=2,
)

from yutori.navigator import *  # noqa: F401,F403
from yutori.navigator import __all__  # noqa: F401
