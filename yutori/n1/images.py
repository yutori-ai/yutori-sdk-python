"""Screenshot preparation helpers for n1 image inputs."""

from __future__ import annotations

import base64
import io
from typing import Protocol

DEFAULT_SCREENSHOT_SIZE = (1280, 800)
DEFAULT_PLAYWRIGHT_SCREENSHOT_TYPE = "jpeg"
DEFAULT_PLAYWRIGHT_SCREENSHOT_QUALITY = 75
DEFAULT_WEBP_QUALITY = 90
DEFAULT_WEBP_QUALITY_FOR_PNG = 30


class SupportsSyncScreenshot(Protocol):
    """A sync screenshot producer such as a Playwright sync page."""

    def screenshot(self, *, type: str | None = None, quality: int | None = None) -> bytes:
        """Capture a screenshot and return image bytes."""


class SupportsAsyncScreenshot(Protocol):
    """An async screenshot producer such as a Playwright async page."""

    async def screenshot(self, *, type: str | None = None, quality: int | None = None) -> bytes:
        """Capture a screenshot and return image bytes."""


def _default_webp_quality(source_format: str | None) -> int:
    normalized = (source_format or "").upper()
    if normalized == "PNG":
        return DEFAULT_WEBP_QUALITY_FOR_PNG
    return DEFAULT_WEBP_QUALITY


def _require_pillow():
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImportError("n1 screenshot helpers require Pillow. Install with: pip install 'yutori[n1]'") from exc
    return Image


def screenshot_to_data_url(
    image_bytes: bytes,
    *,
    resize_to: tuple[int, int] = DEFAULT_SCREENSHOT_SIZE,
    source_format: str | None = None,
    webp_quality: int | None = None,
) -> str:
    """Convert screenshot bytes into a WebP data URL optimized for n1."""

    Image = _require_pillow()

    with Image.open(io.BytesIO(image_bytes)) as img:
        detected_format = (source_format or img.format or "").upper()
        if img.size != resize_to:
            img = img.resize(resize_to, Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(
            buffer,
            format="WEBP",
            quality=webp_quality if webp_quality is not None else _default_webp_quality(detected_format),
        )

    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/webp;base64,{encoded}"


def playwright_screenshot_to_data_url(
    page: SupportsSyncScreenshot,
    *,
    resize_to: tuple[int, int] = DEFAULT_SCREENSHOT_SIZE,
    webp_quality: int | None = None,
) -> str:
    """Capture and convert a sync Playwright-style screenshot for n1."""

    screenshot_bytes = page.screenshot(
        type=DEFAULT_PLAYWRIGHT_SCREENSHOT_TYPE,
        quality=DEFAULT_PLAYWRIGHT_SCREENSHOT_QUALITY,
    )
    return screenshot_to_data_url(
        screenshot_bytes,
        resize_to=resize_to,
        source_format=DEFAULT_PLAYWRIGHT_SCREENSHOT_TYPE,
        webp_quality=webp_quality,
    )


async def aplaywright_screenshot_to_data_url(
    page: SupportsAsyncScreenshot,
    *,
    resize_to: tuple[int, int] = DEFAULT_SCREENSHOT_SIZE,
    webp_quality: int | None = None,
) -> str:
    """Capture and convert an async Playwright-style screenshot for n1."""

    screenshot_bytes = await page.screenshot(
        type=DEFAULT_PLAYWRIGHT_SCREENSHOT_TYPE,
        quality=DEFAULT_PLAYWRIGHT_SCREENSHOT_QUALITY,
    )
    return screenshot_to_data_url(
        screenshot_bytes,
        resize_to=resize_to,
        source_format=DEFAULT_PLAYWRIGHT_SCREENSHOT_TYPE,
        webp_quality=webp_quality,
    )
