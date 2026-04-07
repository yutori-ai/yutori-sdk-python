"""Utilities for building agents with the Yutori n1 API.

Provides reusable helpers for common patterns in n1 agent loops:
- Screenshot preparation: capture and encode screenshots as optimized WebP data URLs
- Payload management: trim old screenshots to stay within API size limits
- Loop helpers: create trimmed requests without mutating caller state
"""

from __future__ import annotations

from .content import extract_text_content
from .hooks import RunHooksBase
from .images import (
    aplaywright_screenshot_to_data_url,
    playwright_screenshot_to_data_url,
    screenshot_to_data_url,
)
from .loop import acreate_trimmed, create_trimmed
from .payload import estimate_messages_size_bytes, trim_images_to_fit, trimmed_messages_to_fit

__all__ = [
    "acreate_trimmed",
    "aplaywright_screenshot_to_data_url",
    "extract_text_content",
    "create_trimmed",
    "playwright_screenshot_to_data_url",
    "RunHooksBase",
    "screenshot_to_data_url",
    "estimate_messages_size_bytes",
    "trim_images_to_fit",
    "trimmed_messages_to_fit",
]
