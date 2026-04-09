from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from yutori.n1.playwright import (
    PlaywrightActionContext,
    PlaywrightActionError,
    PlaywrightActionExecutor,
    PlaywrightToolExecutionResult,
    render_action_trace,
)


class FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int, str]] = []
        self.click_counts: list[int] = []
        self.double_clicks: list[tuple[int, int]] = []
        self.moves: list[tuple[int, int]] = []
        self.wheels: list[tuple[float, float]] = []
        self.down_count = 0
        self.up_count = 0

    async def click(self, x: int, y: int, *, button: str = "left", click_count: int = 1) -> None:
        self.clicks.append((x, y, button))
        self.click_counts.append(click_count)

    async def dblclick(self, x: int, y: int) -> None:
        self.double_clicks.append((x, y))

    async def move(self, x: int, y: int, *, steps: int | None = None) -> None:
        del steps
        self.moves.append((x, y))

    async def wheel(self, delta_x: float, delta_y: float) -> None:
        self.wheels.append((delta_x, delta_y))

    async def down(self) -> None:
        self.down_count += 1

    async def up(self) -> None:
        self.up_count += 1


class FakeKeyboard:
    def __init__(self) -> None:
        self.typed: list[str] = []
        self.pressed: list[str] = []
        self.down_calls: list[str] = []
        self.up_calls: list[str] = []

    async def type(self, text: str) -> None:
        self.typed.append(text)

    async def press(self, key: str) -> None:
        self.pressed.append(key)

    async def down(self, key: str) -> None:
        self.down_calls.append(key)

    async def up(self, key: str) -> None:
        self.up_calls.append(key)


class FakePage:
    def __init__(self) -> None:
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.url = "http://fixture.local/start"
        self.viewport_size = {"width": 1280, "height": 800}
        self.goto_calls: list[tuple[str, dict[str, Any]]] = []
        self.reload_calls: list[dict[str, Any]] = []
        self.go_back_calls: list[dict[str, Any]] = []
        self.go_forward_calls: list[dict[str, Any]] = []
        self.wait_states: list[tuple[str, dict[str, Any]]] = []
        self.evaluate_results: list[Any] = []
        self.evaluate_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.aria_snapshot_result: str | None = None

    async def goto(self, url: str, **kwargs: Any) -> SimpleNamespace:
        self.url = url
        self.goto_calls.append((url, kwargs))
        return SimpleNamespace(url=url)

    async def reload(self, **kwargs: Any) -> SimpleNamespace:
        self.reload_calls.append(kwargs)
        return SimpleNamespace(url=self.url)

    async def go_back(self, **kwargs: Any) -> None:
        self.go_back_calls.append(kwargs)
        self.url = "http://fixture.local/previous"
        return None

    async def go_forward(self, **kwargs: Any) -> None:
        self.go_forward_calls.append(kwargs)
        self.url = "http://fixture.local/next"
        return None

    async def wait_for_load_state(self, state: str, **kwargs: Any) -> None:
        self.wait_states.append((state, kwargs))

    async def evaluate(self, script: str, *args: Any) -> Any:
        self.evaluate_calls.append((script, args))
        if "__n1PrintGuardInstalled" in script:
            return {"ready": True}
        if script.startswith("window.scrollBy("):
            return None
        if self.evaluate_results:
            return self.evaluate_results.pop(0)
        raise AssertionError("No evaluate result queued")

    def locator(self, selector: str) -> SimpleNamespace:
        assert selector == "body"
        return SimpleNamespace(aria_snapshot=AsyncMock(return_value=self.aria_snapshot_result))


def make_executor() -> PlaywrightActionExecutor:
    return PlaywrightActionExecutor(navigation_timeout_ms=1_000, settle_delay_seconds=0)


def make_context(page: FakePage) -> PlaywrightActionContext:
    return PlaywrightActionContext(page=page, viewport_width=1280, viewport_height=800)


def make_tool_call(name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def test_render_action_trace_formats_scaled_coordinates() -> None:
    result = render_action_trace("left_click", {"coordinates": [500, 250]}, width=1280, height=800)
    assert result == "left_click([640, 200])"


def test_action_context_from_page_uses_viewport_size() -> None:
    page = FakePage()
    context = PlaywrightActionContext.from_page(page)
    assert context.viewport_width == 1280
    assert context.viewport_height == 800


@pytest.mark.asyncio
async def test_execute_action_left_click_supports_ref_only_targeting() -> None:
    executor = make_executor()
    page = FakePage()
    page.evaluate_results = [{"success": True, "coordinates": [123.4, 56.7]}]

    result = await executor.execute_action(
        context=make_context(page),
        action_name="left_click",
        arguments={"coordinates": [], "ref": "ref_1"},
    )

    assert result == "Clicked 1x with left"
    assert page.mouse.moves[-1] == (123, 56)
    assert page.mouse.clicks == [(123, 56, "left")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_name", "arguments", "expected_url"),
    [
        ("goto_url", {"url": "http://fixture.local/modal"}, "http://fixture.local/modal"),
        ("go_back", {}, "http://fixture.local/previous"),
        ("refresh", {}, "http://fixture.local/start"),
    ],
)
async def test_navigation_actions_wait_for_domcontentloaded(
    action_name: str,
    arguments: dict[str, Any],
    expected_url: str,
) -> None:
    executor = make_executor()
    page = FakePage()

    await executor.execute_action(context=make_context(page), action_name=action_name, arguments=arguments)

    assert page.url == expected_url
    assert any(state == "domcontentloaded" for state, _ in page.wait_states)


@pytest.mark.asyncio
async def test_execute_action_supports_n1_5_mouse_actions() -> None:
    executor = make_executor()
    page = FakePage()
    context = make_context(page)

    await executor.execute_action(context=context, action_name="mouse_move", arguments={"coordinates": [250, 500]})
    await executor.execute_action(context=context, action_name="middle_click", arguments={"coordinates": [500, 250]})
    await executor.execute_action(context=context, action_name="mouse_down", arguments={"coordinates": [500, 250]})
    await executor.execute_action(context=context, action_name="mouse_up", arguments={"coordinates": [500, 250]})

    assert page.mouse.moves[0] == (320, 400)
    assert (640, 200, "middle") in page.mouse.clicks
    assert page.mouse.down_count == 1
    assert page.mouse.up_count == 1


@pytest.mark.asyncio
async def test_execute_action_key_press_supports_n1_5_key_sequences() -> None:
    executor = make_executor()
    page = FakePage()

    result = await executor.execute_action(
        context=make_context(page),
        action_name="key_press",
        arguments={"key": "down down enter"},
    )

    assert result == "Pressed key: down down enter"
    assert page.keyboard.pressed == ["ArrowDown", "ArrowDown", "Enter"]


@pytest.mark.asyncio
async def test_execute_action_applies_modifiers_to_clicks_and_scrolls() -> None:
    executor = make_executor()
    page = FakePage()
    context = make_context(page)

    await executor.execute_action(
        context=context,
        action_name="left_click",
        arguments={"coordinates": [500, 250], "modifier": "shift"},
    )
    await executor.execute_action(
        context=context,
        action_name="scroll",
        arguments={"coordinates": [500, 500], "direction": "down", "amount": 1, "modifier": "alt"},
    )

    assert page.keyboard.down_calls == ["Shift", "Alt"]
    assert page.keyboard.up_calls == ["Shift", "Alt"]
    assert page.mouse.clicks[0] == (640, 200, "left")
    assert page.mouse.wheels


@pytest.mark.asyncio
async def test_execute_action_hold_key_without_duration_is_a_key_press() -> None:
    executor = make_executor()
    page = FakePage()

    result = await executor.execute_action(
        context=make_context(page),
        action_name="hold_key",
        arguments={"key": "shift"},
    )

    assert result == "Pressed key: shift"
    assert page.keyboard.pressed == ["Shift"]
    assert page.keyboard.down_calls == []
    assert page.keyboard.up_calls == []


@pytest.mark.asyncio
async def test_execute_action_hold_key_with_duration_uses_individual_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(duration: float) -> None:
        sleep_calls.append(duration)

    from yutori.n1.playwright import executor as executor_module

    monkeypatch.setattr(executor_module.asyncio, "sleep", fake_sleep)
    executor = make_executor()
    page = FakePage()

    result = await executor.execute_action(
        context=make_context(page),
        action_name="hold_key",
        arguments={"key": "ctrl+plus", "duration": 0.5},
    )

    assert result == "Held key 'ctrl+plus' for 0.5s"
    assert page.keyboard.down_calls == ["Control", "+"]
    assert page.keyboard.up_calls == ["+", "Control"]
    assert sleep_calls == [0.5]


@pytest.mark.asyncio
async def test_execute_action_wait_supports_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(duration: float) -> None:
        sleep_calls.append(duration)

    from yutori.n1.playwright import executor as executor_module

    monkeypatch.setattr(executor_module.asyncio, "sleep", fake_sleep)
    executor = make_executor()
    page = FakePage()

    result = await executor.execute_action(
        context=make_context(page),
        action_name="wait",
        arguments={"duration": 0.25},
    )

    assert result == "Waited 0.25s"
    assert sleep_calls == [0.25]


@pytest.mark.asyncio
async def test_execute_action_scroll_without_coordinates_uses_window_scroll_by() -> None:
    executor = make_executor()
    page = FakePage()

    result = await executor.execute_action(
        context=make_context(page),
        action_name="scroll",
        arguments={"direction": "down", "amount": 2},
    )

    assert result == "Scrolled down"
    assert page.evaluate_calls[-1] == ("window.scrollBy(0.0, 200.0)", ())


@pytest.mark.asyncio
async def test_execute_tool_call_extract_content_returns_snapshot() -> None:
    executor = make_executor()
    page = FakePage()
    page.url = "http://fixture.local/form"
    page.aria_snapshot_result = '- heading "Checkout"\n- button "Submit"'

    result = await executor.execute_tool_call(make_context(page), make_tool_call("extract_content", {}))

    assert isinstance(result, PlaywrightToolExecutionResult)
    assert result.trace == "extract_content()"
    assert result.current_url == "http://fixture.local/form"
    assert "Accessible page snapshot:" in result.output_text
    assert '- heading "Checkout"' in result.output_text


@pytest.mark.asyncio
async def test_execute_tool_call_extract_elements_returns_ref_annotated_content() -> None:
    executor = make_executor()
    page = FakePage()
    page.url = "http://fixture.local/form"
    page.evaluate_results = [{"success": True, "pageContent": '- button "Save" [ref=ref_1]'}]

    result = await executor.execute_tool_call(make_context(page), make_tool_call("extract_elements", {}))

    assert isinstance(result, PlaywrightToolExecutionResult)
    assert result.trace == "extract_elements()"
    assert result.current_url == "http://fixture.local/form"
    assert result.output_text == '- button "Save" [ref=ref_1]'


@pytest.mark.asyncio
async def test_execute_tool_call_find_returns_matches() -> None:
    executor = make_executor()
    page = FakePage()
    page.url = "http://fixture.local/form"
    page.evaluate_results = [
        {
            "success": True,
            "message": 'Found 1 visible match for "Save".',
            "matches": ['- button "Save" [ref=ref_1]'],
        }
    ]

    result = await executor.execute_tool_call(make_context(page), make_tool_call("find", {"text": "Save"}))

    assert isinstance(result, PlaywrightToolExecutionResult)
    assert result.trace == "find(text='Save')"
    assert result.output_text == 'Found 1 visible match for "Save".\nMatches:\n- button "Save" [ref=ref_1]'


@pytest.mark.asyncio
async def test_execute_tool_call_set_element_value_updates_ref_target() -> None:
    executor = make_executor()
    page = FakePage()
    page.url = "http://fixture.local/form"
    page.evaluate_results = [{"success": True, "message": 'Set text value to "alice@example.com"'}]

    result = await executor.execute_tool_call(
        make_context(page),
        make_tool_call("set_element_value", {"ref": "ref_2", "value": "alice@example.com"}),
    )

    assert isinstance(result, PlaywrightToolExecutionResult)
    assert result.trace == "set_element_value(ref='ref_2', value='alice@example.com')"
    assert result.output_text == 'Set text value to "alice@example.com"'
    assert page.evaluate_calls[-1][1] == ({"ref": "ref_2", "value": "alice@example.com"},)


@pytest.mark.asyncio
async def test_execute_tool_call_execute_js_returns_serialized_result() -> None:
    executor = make_executor()
    page = FakePage()
    page.url = "http://fixture.local/form"
    page.evaluate_results = [{"success": True, "hasResult": True, "result": '{"count":1}'}]

    result = await executor.execute_tool_call(
        make_context(page),
        make_tool_call("execute_js", {"text": "return { count: 1 };"})
    )

    assert isinstance(result, PlaywrightToolExecutionResult)
    assert result.trace == "execute_js()"
    assert result.output_text == 'JavaScript result: {"count":1}'


@pytest.mark.asyncio
async def test_execute_tool_call_rejects_unsupported_zoom_tool() -> None:
    executor = make_executor()
    page = FakePage()

    with pytest.raises(PlaywrightActionError):
        await executor.execute_tool_call(
            make_context(page),
            make_tool_call("zoom", {"region": [0, 0, 10, 10]}),
        )
