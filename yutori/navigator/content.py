"""Content normalization helpers for Yutori Navigator loops."""

from __future__ import annotations

from typing import Any


def extract_text_content(content: Any) -> str | None:
    """Extract normalized text from chat-completions style content blocks."""

    if content is None:
        return None
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text = block.get("text", "")
                    parts.append(text if isinstance(text, str) else str(text))
            elif getattr(block, "type", None) == "text":
                text = getattr(block, "text", "")
                parts.append(text if isinstance(text, str) else str(text))
        normalized = "\n".join(part for part in parts if part).strip()
        return normalized or None

    text_attr = getattr(content, "text", None)
    if isinstance(text_attr, str):
        return text_attr.strip() or None
    return str(content).strip() or None
