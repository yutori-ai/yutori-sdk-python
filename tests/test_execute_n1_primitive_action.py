"""Characterization tests for ``examples._common.execute_n1_primitive_action``.

Pins the exact Playwright calls and denormalized coordinates for each of
Navigator n1's browser primitives before/after extracting this dispatch out
of ``navigator_n1.py`` and ``navigator_n1_memo.py`` (which previously
duplicated the same ~100-line if/elif chain verbatim).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

pytest.importorskip("loguru")
from examples._common import execute_n1_primitive_action  # noqa: E402
from yutori.navigator import denormalize_coordinates  # noqa: E402

VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800


def make_page() -> MagicMock:
    page = MagicMock()
    page.mouse = MagicMock()
    page.mouse.click = AsyncMock()
    page.mouse.dblclick = AsyncMock()
    page.mouse.move = AsyncMock()
    page.mouse.wheel = AsyncMock()
    page.mouse.down = AsyncMock()
    page.mouse.up = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.keyboard.type = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.go_back = AsyncMock()
    page.reload = AsyncMock()
    return page


async def run_action(action_name: str, arguments: dict) -> tuple[bool, MagicMock]:
    page = make_page()
    handled = await execute_n1_primitive_action(page, action_name, arguments, VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
    return handled, page


class TestClickActions:
    async def test_left_click(self):
        handled, page = await run_action("left_click", {"coordinates": [500, 500]})
        assert handled is True
        abs_x, abs_y = denormalize_coordinates([500, 500], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        page.mouse.click.assert_awaited_once_with(abs_x, abs_y)

    async def test_left_click_defaults_to_origin(self):
        handled, page = await run_action("left_click", {})
        assert handled is True
        abs_x, abs_y = denormalize_coordinates([0, 0], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        page.mouse.click.assert_awaited_once_with(abs_x, abs_y)

    async def test_double_click(self):
        handled, page = await run_action("double_click", {"coordinates": [100, 200]})
        assert handled is True
        abs_x, abs_y = denormalize_coordinates([100, 200], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        page.mouse.dblclick.assert_awaited_once_with(abs_x, abs_y)

    async def test_right_click(self):
        handled, page = await run_action("right_click", {"coordinates": [100, 200]})
        assert handled is True
        abs_x, abs_y = denormalize_coordinates([100, 200], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        page.mouse.click.assert_awaited_once_with(abs_x, abs_y, button="right")

    async def test_triple_click(self):
        handled, page = await run_action("triple_click", {"coordinates": [100, 200]})
        assert handled is True
        abs_x, abs_y = denormalize_coordinates([100, 200], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        page.mouse.click.assert_awaited_once_with(abs_x, abs_y, click_count=3)


class TestTypeAction:
    async def test_type_clears_and_presses_enter_by_default(self):
        handled, page = await run_action("type", {"text": "hello"})
        assert handled is True
        page.keyboard.type.assert_awaited_once_with("hello")
        # Clear-before-typing + trailing Enter both default to True.
        assert call("Backspace") in page.keyboard.press.await_args_list
        assert call("Enter") in page.keyboard.press.await_args_list

    async def test_type_can_skip_clear_and_enter(self):
        handled, page = await run_action(
            "type", {"text": "hello", "press_enter_after": False, "clear_before_typing": False}
        )
        assert handled is True
        page.keyboard.type.assert_awaited_once_with("hello")
        page.keyboard.press.assert_not_awaited()

    async def test_type_defaults_to_empty_text(self):
        handled, page = await run_action("type", {})
        assert handled is True
        page.keyboard.type.assert_awaited_once_with("")


class TestKeyActions:
    async def test_key_press_by_key_field(self):
        handled, page = await run_action("key_press", {"key": "Enter"})
        assert handled is True
        page.keyboard.press.assert_awaited_once_with("Enter")

    async def test_key_press_by_legacy_key_comb_field(self):
        handled, page = await run_action("key", {"key_comb": "Control+c"})
        assert handled is True
        page.keyboard.press.assert_awaited_once_with("Control+c")

    async def test_key_press_translates_meta_to_control_or_meta(self):
        handled, page = await run_action("key_press", {"key": "Meta+a"})
        assert handled is True
        page.keyboard.press.assert_awaited_once_with("ControlOrMeta+a")


class TestScrollAction:
    async def test_scroll_down_default(self):
        handled, page = await run_action("scroll", {})
        assert handled is True
        abs_x, abs_y = denormalize_coordinates([500, 500], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        page.mouse.move.assert_awaited_once_with(abs_x, abs_y)
        expected_delta = 3 * (VIEWPORT_HEIGHT * 0.1)
        page.mouse.wheel.assert_awaited_once_with(0, expected_delta)

    async def test_scroll_up_negates_delta_y(self):
        handled, page = await run_action("scroll", {"direction": "up", "amount": 2})
        assert handled is True
        expected_delta = -(2 * (VIEWPORT_HEIGHT * 0.1))
        page.mouse.wheel.assert_awaited_once_with(0, expected_delta)

    async def test_scroll_right_sets_delta_x(self):
        handled, page = await run_action("scroll", {"direction": "right", "amount": 1})
        assert handled is True
        expected_delta = 1 * (VIEWPORT_HEIGHT * 0.1)
        page.mouse.wheel.assert_awaited_once_with(expected_delta, 0)

    async def test_scroll_accepts_legacy_coordinate_singular_key(self):
        handled, page = await run_action("scroll", {"coordinate": [10, 20]})
        assert handled is True
        abs_x, abs_y = denormalize_coordinates([10, 20], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        page.mouse.move.assert_awaited_once_with(abs_x, abs_y)


class TestHoverAction:
    async def test_hover(self):
        handled, page = await run_action("hover", {"coordinates": [50, 60]})
        assert handled is True
        abs_x, abs_y = denormalize_coordinates([50, 60], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        page.mouse.move.assert_awaited_once_with(abs_x, abs_y)


class TestDragAction:
    async def test_drag_moves_down_and_up(self):
        handled, page = await run_action("drag", {"start_coordinates": [10, 10], "coordinates": [200, 200]})
        assert handled is True
        start_x, start_y = denormalize_coordinates([10, 10], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        end_x, end_y = denormalize_coordinates([200, 200], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        assert page.mouse.move.await_args_list == [call(start_x, start_y), call(end_x, end_y)]
        page.mouse.down.assert_awaited_once_with()
        page.mouse.up.assert_awaited_once_with()

    async def test_drag_accepts_legacy_camelcase_keys(self):
        handled, page = await run_action("drag", {"startCoordinates": [1, 1], "endCoordinates": [2, 2]})
        assert handled is True
        start_x, start_y = denormalize_coordinates([1, 1], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        end_x, end_y = denormalize_coordinates([2, 2], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        assert page.mouse.move.await_args_list == [call(start_x, start_y), call(end_x, end_y)]


class TestNavigationActions:
    async def test_goto(self):
        handled, page = await run_action("goto", {"url": "https://example.com"})
        assert handled is True
        page.goto.assert_awaited_once_with("https://example.com")
        page.wait_for_load_state.assert_awaited_once_with("domcontentloaded")

    async def test_goto_url_alias(self):
        handled, page = await run_action("goto_url", {"url": "https://example.com"})
        assert handled is True
        page.goto.assert_awaited_once_with("https://example.com")

    async def test_back(self):
        handled, page = await run_action("back", {})
        assert handled is True
        page.go_back.assert_awaited_once_with()

    async def test_go_back_alias(self):
        handled, page = await run_action("go_back", {})
        assert handled is True
        page.go_back.assert_awaited_once_with()

    async def test_refresh(self):
        handled, page = await run_action("refresh", {})
        assert handled is True
        page.reload.assert_awaited_once_with()


class TestWaitAction:
    async def test_wait_does_not_touch_the_page(self):
        handled, page = await run_action("wait", {})
        assert handled is True
        page.mouse.assert_not_called()
        page.keyboard.assert_not_called()
        page.goto.assert_not_awaited()


class TestUnknownAction:
    async def test_unknown_action_returns_false_and_touches_nothing(self):
        handled, page = await run_action("teleport", {})
        assert handled is False
        page.mouse.assert_not_called()
        page.keyboard.assert_not_called()
        page.goto.assert_not_awaited()
