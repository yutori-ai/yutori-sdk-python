"""Tests for yutori.n1.payload – payload management utilities."""

from yutori.n1.payload import (
    DEFAULT_KEEP_RECENT_SCREENSHOTS,
    DEFAULT_MAX_REQUEST_BYTES,
    _strip_one_image,
    estimate_messages_size_bytes,
    message_has_image,
    trim_images_to_fit,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IMG_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg"


def _text_msg(role: str, text: str) -> dict:
    return {"role": role, "content": text}


def _image_msg(role: str, url: str = IMG_URL, text: str | None = None) -> dict:
    content: list[dict] = [{"type": "image_url", "image_url": {"url": url}}]
    if text:
        content.insert(0, {"type": "text", "text": text})
    return {"role": role, "content": content}


# ---------------------------------------------------------------------------
# estimate_messages_size_bytes
# ---------------------------------------------------------------------------


class TestEstimateMessagesSizeBytes:
    def test_empty_list(self):
        assert estimate_messages_size_bytes([]) == 2  # "[]"

    def test_single_text_message(self):
        msgs = [_text_msg("user", "hello")]
        size = estimate_messages_size_bytes(msgs)
        assert size > 0
        assert isinstance(size, int)

    def test_size_grows_with_content(self):
        small = [_text_msg("user", "a")]
        large = [_text_msg("user", "a" * 1000)]
        assert estimate_messages_size_bytes(large) > estimate_messages_size_bytes(small)


# ---------------------------------------------------------------------------
# message_has_image
# ---------------------------------------------------------------------------


class TestMessageHasImage:
    def test_text_message_no_image(self):
        assert message_has_image(_text_msg("user", "hi")) is False

    def test_string_content_no_image(self):
        assert message_has_image({"role": "user", "content": "just text"}) is False

    def test_image_message(self):
        assert message_has_image(_image_msg("user")) is True

    def test_image_with_text(self):
        assert message_has_image(_image_msg("user", text="caption")) is True

    def test_empty_content_list(self):
        assert message_has_image({"role": "user", "content": []}) is False

    def test_no_content_key(self):
        assert message_has_image({"role": "user"}) is False


# ---------------------------------------------------------------------------
# _strip_one_image
# ---------------------------------------------------------------------------


class TestStripOneImage:
    def test_strips_image(self):
        msg = _image_msg("user", text="caption")
        assert _strip_one_image(msg) is True
        # image should be gone, text should remain
        assert not any(p.get("type") == "image_url" for p in msg["content"])
        assert any(p.get("type") == "text" and p["text"] == "caption" for p in msg["content"])

    def test_adds_placeholder_when_no_text(self):
        msg = _image_msg("user")
        assert _strip_one_image(msg) is True
        assert any("omitted" in p.get("text", "").lower() for p in msg["content"])

    def test_no_image_returns_false(self):
        msg = _text_msg("user", "no image here")
        assert _strip_one_image(msg) is False

    def test_string_content_returns_false(self):
        msg = {"role": "user", "content": "plain string"}
        assert _strip_one_image(msg) is False

    def test_only_removes_first_image(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "img1"}},
                {"type": "image_url", "image_url": {"url": "img2"}},
            ],
        }
        _strip_one_image(msg)
        images = [p for p in msg["content"] if p.get("type") == "image_url"]
        assert len(images) == 1
        assert images[0]["image_url"]["url"] == "img2"


# ---------------------------------------------------------------------------
# trim_images_to_fit
# ---------------------------------------------------------------------------


class TestTrimImagesToFit:
    def test_already_under_limit(self):
        msgs = [_text_msg("user", "hi")]
        size, removed = trim_images_to_fit(msgs, max_bytes=10_000)
        assert removed == 0

    def test_no_images_over_limit(self):
        msgs = [_text_msg("user", "x" * 1000)]
        size, removed = trim_images_to_fit(msgs, max_bytes=10)
        assert removed == 0

    def test_removes_old_images_first(self):
        big_data = "A" * 5000
        msgs = [
            _image_msg("assistant", url=big_data),
            _image_msg("assistant", url=big_data),
            _image_msg("assistant", url=big_data),
            _image_msg("assistant", url=big_data),
        ]
        size, removed = trim_images_to_fit(msgs, max_bytes=15_000, keep_recent=2)
        assert removed > 0
        # The last 2 messages should still have images (protected)
        assert message_has_image(msgs[-1])
        assert message_has_image(msgs[-2])

    def test_last_image_always_kept(self):
        big_data = "A" * 5000
        msgs = [
            _image_msg("assistant", url=big_data),
            _image_msg("assistant", url=big_data),
        ]
        # Set max_bytes very low so it would want to remove everything
        size, removed = trim_images_to_fit(msgs, max_bytes=100, keep_recent=1)
        # The very last image must be kept
        assert message_has_image(msgs[-1])

    def test_keep_recent_minimum_is_1(self):
        big_data = "A" * 5000
        msgs = [
            _image_msg("assistant", url=big_data),
            _image_msg("assistant", url=big_data),
        ]
        # keep_recent=0 should be clamped to 1
        size, removed = trim_images_to_fit(msgs, max_bytes=100, keep_recent=0)
        assert message_has_image(msgs[-1])

    def test_returns_tuple(self):
        msgs = [_text_msg("user", "hello")]
        result = trim_images_to_fit(msgs)
        assert isinstance(result, tuple)
        assert len(result) == 2
        size, removed = result
        assert isinstance(size, int)
        assert isinstance(removed, int)

    def test_defaults(self):
        assert DEFAULT_MAX_REQUEST_BYTES == 9_500_000
        assert DEFAULT_KEEP_RECENT_SCREENSHOTS == 6

    def test_messages_mutated_in_place(self):
        big_data = "A" * 5000
        msgs = [
            _image_msg("assistant", url=big_data),
            _image_msg("assistant", url=big_data),
            _image_msg("assistant", url=big_data),
        ]
        original_id = id(msgs)
        trim_images_to_fit(msgs, max_bytes=10_000, keep_recent=1)
        assert id(msgs) == original_id  # same list object

    def test_phase2_removes_protected_except_latest(self):
        big_data = "A" * 5000
        msgs = [
            _image_msg("assistant", url=big_data),
            _image_msg("assistant", url=big_data),
            _image_msg("assistant", url=big_data),
        ]
        # Very low limit forces phase 2
        size, removed = trim_images_to_fit(msgs, max_bytes=100, keep_recent=3)
        # Only the very last image should survive
        assert message_has_image(msgs[-1])
        assert removed >= 2
