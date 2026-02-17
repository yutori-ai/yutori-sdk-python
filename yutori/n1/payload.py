"""Payload management utilities for n1 agent loops.

When using the n1 API in an agentic loop, the message history grows with each
step because every tool response includes a new screenshot. These utilities
help keep the total payload under the API's size limit by selectively removing
old screenshots while preserving recent context.

Adapted from the n1-brightdata project (https://github.com/meirk-brd/n1-brightdata).
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_MAX_REQUEST_BYTES = 9_500_000
DEFAULT_KEEP_RECENT_SCREENSHOTS = 6


def estimate_messages_size_bytes(messages: list[dict[str, Any]]) -> int:
    """Estimate the JSON-serialized byte size of a messages list."""
    return len(json.dumps(messages, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def message_has_image(message: dict[str, Any]) -> bool:
    """Return True if *message* contains at least one ``image_url`` content block."""
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(part, dict) and part.get("type") == "image_url" for part in content)


def _strip_one_image(message: dict[str, Any]) -> bool:
    """Remove the first ``image_url`` block from *message* in place.

    If removing the image leaves the message with no text content, a
    placeholder text block is inserted so the message remains valid.

    Returns True if an image was removed.
    """
    content = message.get("content")
    if not isinstance(content, list):
        return False

    removed = False
    new_content: list[dict[str, Any]] = []
    for part in content:
        if not removed and isinstance(part, dict) and part.get("type") == "image_url":
            removed = True
            continue
        new_content.append(part)

    if not removed:
        return False

    has_text = any(isinstance(p, dict) and p.get("type") == "text" for p in new_content)
    if not has_text:
        new_content.append({"type": "text", "text": "Screenshot omitted to stay under request size limit."})

    message["content"] = new_content
    return True


def trim_images_to_fit(
    messages: list[dict[str, Any]],
    *,
    max_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    keep_recent: int = DEFAULT_KEEP_RECENT_SCREENSHOTS,
) -> tuple[int, int]:
    """Remove old screenshots from *messages* until the payload fits within *max_bytes*.

    The most recent *keep_recent* screenshots are protected from removal (the
    very last screenshot is always kept). If the payload is already within
    limits, no changes are made.

    Args:
        messages: The mutable messages list (modified in place).
        max_bytes: Target maximum payload size in bytes.
        keep_recent: Number of recent screenshots to protect from removal.

    Returns:
        A ``(current_size_bytes, images_removed)`` tuple.
    """
    size_bytes = estimate_messages_size_bytes(messages)
    if size_bytes <= max_bytes:
        return size_bytes, 0

    image_indices = [i for i, msg in enumerate(messages) if message_has_image(msg)]
    if not image_indices:
        return size_bytes, 0

    keep_recent = max(1, keep_recent)
    protected = set(image_indices[-keep_recent:])
    removed = 0

    # Phase 1: remove old images outside the protected window
    for idx in image_indices:
        if size_bytes <= max_bytes:
            break
        if idx in protected:
            continue
        if _strip_one_image(messages[idx]):
            removed += 1
            size_bytes = estimate_messages_size_bytes(messages)

    # Phase 2: if still over limit, remove from protected set (except the latest)
    if size_bytes > max_bytes:
        for idx in image_indices:
            if size_bytes <= max_bytes:
                break
            if idx == image_indices[-1]:
                continue
            if _strip_one_image(messages[idx]):
                removed += 1
                size_bytes = estimate_messages_size_bytes(messages)

    return size_bytes, removed
