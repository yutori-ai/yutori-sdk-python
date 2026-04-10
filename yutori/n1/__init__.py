"""Utilities for building agents with the Yutori n1 and n1.5 APIs.

Provides reusable helpers for common patterns in n1/n1.5 agent loops:
- Screenshot preparation: capture and encode screenshots as optimized WebP data URLs
- Coordinate conversion: map n1's 1000x1000 tool-call space to viewport pixels
- Payload management: trim old screenshots to stay within API size limits
- Page readiness: wait for Playwright pages to stabilize between agent steps
- Loop helpers: create trimmed requests without mutating caller state
- Key mapping: convert n1.5 lowercase key names to Playwright-compatible names
- Model constants: canonical model identifiers and tool set names
"""

from __future__ import annotations

from .content import extract_text_content
from .coordinates import N1_COORDINATE_SCALE, denormalize_coordinates, normalize_coordinates
from .hooks import RunHooksBase
from .images import (
    aplaywright_screenshot_to_data_url,
    playwright_screenshot_to_data_url,
    screenshot_to_data_url,
)
from .keys import map_key_to_playwright, map_keys_individual
from .loop import acreate_trimmed, create_trimmed
from .models import N1_5_MODEL, N1_MODEL, TOOL_SET_CORE, TOOL_SET_EXPANDED
from .page_ready import NoOpPageReadyChecker, PageReadyChecker
from .payload import estimate_messages_size_bytes, trim_images_to_fit, trimmed_messages_to_fit

__all__ = [
    "acreate_trimmed",
    "aplaywright_screenshot_to_data_url",
    "denormalize_coordinates",
    "extract_text_content",
    "create_trimmed",
    "map_key_to_playwright",
    "map_keys_individual",
    "N1_5_MODEL",
    "N1_COORDINATE_SCALE",
    "N1_MODEL",
    "NoOpPageReadyChecker",
    "normalize_coordinates",
    "PageReadyChecker",
    "playwright_screenshot_to_data_url",
    "RunHooksBase",
    "screenshot_to_data_url",
    "estimate_messages_size_bytes",
    "TOOL_SET_CORE",
    "TOOL_SET_EXPANDED",
    "trim_images_to_fit",
    "trimmed_messages_to_fit",
]
