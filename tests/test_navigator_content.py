from __future__ import annotations

from types import SimpleNamespace

from yutori.navigator import extract_text_content


class TestExtractTextContent:
    def test_none_returns_none(self) -> None:
        assert extract_text_content(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_text_content("   ") is None

    def test_string_is_stripped(self) -> None:
        assert extract_text_content("  hello world  ") == "hello world"

    def test_list_of_dict_blocks_joins_text_blocks(self) -> None:
        content = [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            {"type": "text", "text": "world"},
        ]
        assert extract_text_content(content) == "hello\nworld"

    def test_list_of_objects_joins_text_blocks(self) -> None:
        content = [
            SimpleNamespace(type="text", text="alpha"),
            SimpleNamespace(type="image_url", text="ignored"),
            SimpleNamespace(type="text", text="beta"),
        ]
        assert extract_text_content(content) == "alpha\nbeta"

    def test_mixed_blocks_are_supported(self) -> None:
        content = [
            {"type": "text", "text": "first"},
            SimpleNamespace(type="text", text="second"),
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            SimpleNamespace(type="text", text="third"),
        ]
        assert extract_text_content(content) == "first\nsecond\nthird"

    def test_text_attribute_on_non_list_content_is_supported(self) -> None:
        content = SimpleNamespace(text="  structured content  ")
        assert extract_text_content(content) == "structured content"
