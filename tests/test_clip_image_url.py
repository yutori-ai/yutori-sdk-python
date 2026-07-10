"""Characterization tests pinning the current behavior of the two independent
``_clip_image_url`` implementations:

- ``examples._common.BrowserAgentMixin._clip_image_url`` (instance method, max_len=50)
- ``yutori.navigator.replay._clip_image_url`` (module function, max_len=96)

The two are structurally near-identical but differ in constants (max length,
clip-after-prefix offset, and the suffix used for the "long but not a data
URL" branch). These tests exist to pin the exact current output of both
before any attempt to unify them, so a future unification can be checked
against this file for behavior parity.
"""

from __future__ import annotations

import pytest

from yutori.navigator.replay import _clip_image_url as replay_clip_image_url

from .conftest import require_examples_extra

require_examples_extra()
from examples._common import BrowserAgentMixin  # noqa: E402

_mixin = BrowserAgentMixin()


def _common_clip_image_url(url: str) -> str:
    return _mixin._clip_image_url(url)


class TestExamplesCommonClipImageUrl:
    """Pins examples._common.BrowserAgentMixin._clip_image_url (max_len=50, offset=20, suffix='...')."""

    def test_short_plain_string_is_unchanged(self) -> None:
        assert _common_clip_image_url("short") == "short"

    def test_plain_string_at_exact_max_len_is_unchanged(self) -> None:
        url = "x" * 50
        assert _common_clip_image_url(url) == url

    def test_plain_string_over_max_len_is_truncated_with_plain_ellipsis(self) -> None:
        url = "x" * 51
        assert _common_clip_image_url(url) == "x" * 50 + "..."

    def test_short_data_url_is_unchanged(self) -> None:
        url = "data:image/png;base64," + "A" * 10
        assert _common_clip_image_url(url) == url

    def test_long_data_url_is_clipped_after_prefix_with_offset_20(self) -> None:
        url = "data:image/png;base64," + "A" * 100
        prefix_end = url.find(",") + 1
        expected = url[: prefix_end + 20] + "...[clipped]"
        assert _common_clip_image_url(url) == expected
        assert expected == "data:image/png;base64,AAAAAAAAAAAAAAAAAAAA...[clipped]"

    def test_data_url_without_comma_falls_back_to_plain_handling(self) -> None:
        # No comma means prefix_end == 0, so the data-url branch never fires;
        # the string is short enough to pass through the trailing length check unchanged.
        url = "data:image;nocomma"
        assert _common_clip_image_url(url) == url

    def test_long_non_data_url_uses_plain_ellipsis_not_clipped_marker(self) -> None:
        url = "https://example.com/" + "y" * 90
        assert _common_clip_image_url(url) == url[:50] + "..."


class TestReplayClipImageUrl:
    """Pins yutori.navigator.replay._clip_image_url (max_len=96, offset=24, suffix='...[clipped]')."""

    def test_short_plain_string_is_unchanged(self) -> None:
        assert replay_clip_image_url("short") == "short"

    def test_plain_string_at_exact_max_len_is_unchanged(self) -> None:
        url = "x" * 96
        assert replay_clip_image_url(url) == url

    def test_plain_string_over_max_len_is_truncated_with_clipped_marker(self) -> None:
        url = "x" * 97
        assert replay_clip_image_url(url) == "x" * 96 + "...[clipped]"

    def test_short_data_url_is_unchanged(self) -> None:
        url = "data:image/png;base64," + "A" * 10
        assert replay_clip_image_url(url) == url

    def test_long_data_url_is_clipped_after_prefix_with_offset_24(self) -> None:
        url = "data:image/png;base64," + "A" * 100
        prefix_end = url.find(",") + 1
        expected = url[: prefix_end + 24] + "...[clipped]"
        assert replay_clip_image_url(url) == expected
        assert expected == "data:image/png;base64,AAAAAAAAAAAAAAAAAAAAAAAA...[clipped]"

    def test_data_url_without_comma_falls_back_to_plain_handling(self) -> None:
        url = "data:image;nocomma"
        assert replay_clip_image_url(url) == url

    def test_long_non_data_url_uses_clipped_marker(self) -> None:
        url = "https://example.com/" + "y" * 90
        assert replay_clip_image_url(url) == url[:96] + "...[clipped]"


@pytest.mark.parametrize(
    "url",
    [
        "short",
        "data:image/png;base64," + "A" * 10,
        "data:image;nocomma",
    ],
)
def test_both_implementations_agree_below_their_respective_thresholds(url: str) -> None:
    # Below both max_len thresholds (50 and 96), the two implementations must
    # produce identical output -- this is the invariant any future unification
    # needs to preserve exactly, while diverging correctly above the thresholds.
    assert _common_clip_image_url(url) == replay_clip_image_url(url) == url
