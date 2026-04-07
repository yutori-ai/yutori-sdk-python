"""Tests for yutori.n1.images."""

import base64
import io

import pytest
from PIL import Image

from yutori.n1.images import (
    DEFAULT_PLAYWRIGHT_SCREENSHOT_QUALITY,
    DEFAULT_PLAYWRIGHT_SCREENSHOT_TYPE,
    _default_webp_quality,
    aplaywright_screenshot_to_data_url,
    playwright_screenshot_to_data_url,
    screenshot_to_data_url,
)


def _image_bytes(fmt: str, size: tuple[int, int] = (32, 20), color=(10, 20, 30)) -> bytes:
    image = Image.new("RGB", size, color=color)
    buffer = io.BytesIO()
    save_kwargs = {"format": fmt}
    if fmt.upper() in {"JPEG", "JPG"}:
        save_kwargs["quality"] = DEFAULT_PLAYWRIGHT_SCREENSHOT_QUALITY
    image.save(buffer, **save_kwargs)
    return buffer.getvalue()


class TestDefaultWebpQuality:
    def test_prefers_lower_quality_for_png_sources(self):
        assert _default_webp_quality("PNG") == 30

    def test_prefers_default_quality_for_jpeg_sources(self):
        assert _default_webp_quality("JPEG") == 90


class TestScreenshotToDataUrl:
    def test_converts_png_bytes_to_webp_data_url(self):
        image_url = screenshot_to_data_url(_image_bytes("PNG"), resize_to=(64, 40))

        assert image_url.startswith("data:image/webp;base64,")

        encoded = image_url.removeprefix("data:image/webp;base64,")
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as converted:
            assert converted.format == "WEBP"
            assert converted.size == (64, 40)

    def test_skips_resize_when_image_matches_target(self):
        target = (64, 40)
        image_url = screenshot_to_data_url(_image_bytes("PNG", size=target), resize_to=target)

        encoded = image_url.removeprefix("data:image/webp;base64,")
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as converted:
            assert converted.size == target

    def test_raises_when_pillow_missing(self, monkeypatch):
        import yutori.n1.images as images_mod

        original = images_mod._require_pillow

        def mock_require_pillow():
            raise ImportError("n1 screenshot helpers require Pillow. Install with: pip install 'yutori[n1]'")

        monkeypatch.setattr(images_mod, "_require_pillow", mock_require_pillow)
        with pytest.raises(ImportError, match="yutori\\[n1\\]"):
            screenshot_to_data_url(b"fake")
        monkeypatch.setattr(images_mod, "_require_pillow", original)


class TestPlaywrightScreenshotHelpers:
    def test_sync_helper_uses_sdk_capture_policy(self):
        class FakePage:
            def __init__(self):
                self.calls: list[dict[str, int | str | None]] = []

            def screenshot(self, *, type=None, quality=None) -> bytes:
                self.calls.append({"type": type, "quality": quality})
                return _image_bytes("JPEG")

        page = FakePage()
        image_url = playwright_screenshot_to_data_url(page, resize_to=(1280, 800))

        assert page.calls == [
            {
                "type": DEFAULT_PLAYWRIGHT_SCREENSHOT_TYPE,
                "quality": DEFAULT_PLAYWRIGHT_SCREENSHOT_QUALITY,
            }
        ]
        assert image_url.startswith("data:image/webp;base64,")

    @pytest.mark.asyncio
    async def test_async_helper_uses_sdk_capture_policy(self):
        class FakeAsyncPage:
            def __init__(self):
                self.calls: list[dict[str, int | str | None]] = []

            async def screenshot(self, *, type=None, quality=None) -> bytes:
                self.calls.append({"type": type, "quality": quality})
                return _image_bytes("JPEG")

        page = FakeAsyncPage()
        image_url = await aplaywright_screenshot_to_data_url(page, resize_to=(1280, 800))

        assert page.calls == [
            {
                "type": DEFAULT_PLAYWRIGHT_SCREENSHOT_TYPE,
                "quality": DEFAULT_PLAYWRIGHT_SCREENSHOT_QUALITY,
            }
        ]
        assert image_url.startswith("data:image/webp;base64,")
