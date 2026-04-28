"""Utilities for building agents with the Yutori Navigator API.

Provides reusable helpers for common patterns in Navigator n1 and
Navigator n1.5 agent loops:

- Screenshot preparation: capture and encode screenshots as optimized WebP data URLs
- Coordinate conversion: map the 1000x1000 tool-call space to viewport pixels
- Payload management: trim old screenshots to stay within API size limits
- Loop helpers: create trimmed requests without mutating caller state
- Key mapping: convert Navigator n1.5 lowercase key names to Playwright-compatible names
- Model constants: canonical model identifiers and tool set names
"""

from __future__ import annotations

from .content import extract_text_content
from .context import format_task_with_context, format_user_context
from .coordinates import (
    N1_COORDINATE_SCALE,
    NAVIGATOR_COORDINATE_SCALE,
    denormalize_coordinates,
    normalize_coordinates,
)
from .hooks import RunHooksBase
from .images import (
    aplaywright_screenshot_to_data_url,
    playwright_screenshot_to_data_url,
    screenshot_to_data_url,
)
from .keys import map_key_to_playwright, map_keys_individual
from .loop import acreate_trimmed, create_trimmed
from .models import (
    N1_5_MODEL,
    N1_MODEL,
    NAVIGATOR_N1_5_MODEL,
    NAVIGATOR_N1_MODEL,
    TOOL_SET_CORE,
    TOOL_SET_EXPANDED,
)
from .payload import estimate_messages_size_bytes, trim_images_to_fit, trimmed_messages_to_fit
from .stop import format_stop_and_summarize

__all__ = [
    "N1_5_MODEL",
    "N1_COORDINATE_SCALE",
    "N1_MODEL",
    "NAVIGATOR_COORDINATE_SCALE",
    "NAVIGATOR_N1_5_MODEL",
    "NAVIGATOR_N1_MODEL",
    "RunHooksBase",
    "TOOL_SET_CORE",
    "TOOL_SET_EXPANDED",
    "acreate_trimmed",
    "aplaywright_screenshot_to_data_url",
    "create_trimmed",
    "denormalize_coordinates",
    "estimate_messages_size_bytes",
    "extract_text_content",
    "format_stop_and_summarize",
    "format_task_with_context",
    "format_user_context",
    "map_key_to_playwright",
    "map_keys_individual",
    "normalize_coordinates",
    "playwright_screenshot_to_data_url",
    "screenshot_to_data_url",
    "trim_images_to_fit",
    "trimmed_messages_to_fit",
]
