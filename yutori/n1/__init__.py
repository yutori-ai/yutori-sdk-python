"""Utilities for building agents with the Yutori n1 API.

Provides reusable helpers for common patterns in n1 agent loops:
- Payload management: trim old screenshots to stay within API size limits
- Loop helpers: create trimmed requests without mutating caller state
"""

from __future__ import annotations

from .loop import acreate_trimmed, create_trimmed
from .payload import estimate_messages_size_bytes, trim_images_to_fit, trimmed_messages_to_fit

__all__ = [
    "acreate_trimmed",
    "create_trimmed",
    "estimate_messages_size_bytes",
    "trim_images_to_fit",
    "trimmed_messages_to_fit",
]
