"""Utilities for building agents with the Yutori n1 API.

Provides reusable helpers for common patterns in n1 agent loops:
- Payload management: trim old screenshots to stay within API size limits
"""

from __future__ import annotations

from .payload import estimate_messages_size_bytes, trim_images_to_fit

__all__ = [
    "estimate_messages_size_bytes",
    "trim_images_to_fit",
]
