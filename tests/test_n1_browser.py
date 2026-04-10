from __future__ import annotations

import sys

import pytest

from yutori.n1.browser import (
    EXTRACT_CONTENT_AND_LINKS_TOOL_NAME,
    AsyncPlaywrightActionExecutor,
    extract_content_and_links,
    extract_content_and_links_tool_schema,
)


class FakeLocator:
    def __init__(self, snapshot: str) -> None:
        self._snapshot = snapshot

    async def aria_snapshot(self) -> str:
        return self._snapshot


class FakeMouse:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def click(self, x, y, *, button="left", click_count=1) -> None:
        self.calls.append(("click", x, y, button, click_count))

    async def move(self, x, y) -> None:
        self.calls.append(("move", x, y))

    async def down(self) -> None:
        self.calls.append(("down",))

    async def up(self) -> None:
        self.calls.append(("up",))

    async def wheel(self, dx, dy) -> None:
        self.calls.append(("wheel", dx, dy))


class FakeKeyboard:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def press(self, key) -> None:
        self.calls.append(("press", key))

    async def down(self, key) -> None:
        self.calls.append(("down", key))

    async def up(self, key) -> None:
        self.calls.append(("up", key))

    async def type(self, text) -> None:
        self.calls.append(("type", text))


class FakeReadyChecker:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def wait_until_ready(self, page, fast_mode: bool = False) -> bool:
        self.calls.append(fast_mode)
        return True


class FakePage:
    def __init__(self, *, url: str = "https://example.com", snapshot: str = "") -> None:
        self.url = url
        self._snapshot = snapshot
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.marker_set = False
        self.on_start_page = False
        self.goto_calls: list[str] = []
        self.reload_calls = 0
        self.forward_calls = 0
        self.back_calls = 0
        self.evaluate_calls: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        assert selector == "body"
        return FakeLocator(self._snapshot)

    async def goto(self, url: str, wait_until: str = "load") -> None:
        self.goto_calls.append(f"{url}|{wait_until}")
        self.url = url

    async def reload(self) -> None:
        self.reload_calls += 1

    async def go_back(self) -> None:
        self.back_calls += 1
        self.on_start_page = self.marker_set

    async def go_forward(self) -> None:
        self.forward_calls += 1
        self.on_start_page = False

    async def evaluate(self, expression: str):
        self.evaluate_calls.append(expression)
        if "history.replaceState" in expression:
            self.marker_set = True
            return True
        if "isYutoriStartMarker" in expression:
            return self.on_start_page
        return None


@pytest.mark.asyncio
async def test_extract_content_and_links_tool_schema_defaults() -> None:
    schema = extract_content_and_links_tool_schema()

    assert schema["type"] == "function"
    assert schema["function"]["name"] == EXTRACT_CONTENT_AND_LINKS_TOOL_NAME


@pytest.mark.asyncio
async def test_extract_content_and_links_deduplicates_by_url() -> None:
    snapshot = """
- link "Short title":
  - /url: https://example.com/post
- link "Much better title":
  - /url: https://example.com/post
- link "Another page":
  - /url: https://example.com/other
""".strip()
    page = FakePage(url="https://example.com", snapshot=snapshot)

    result = await extract_content_and_links(page)

    assert "Current URL: https://example.com" in result
    assert "- [Much better title](https://example.com/post)" in result
    assert "- [Another page](https://example.com/other)" in result
    assert "Short title" not in result


@pytest.mark.asyncio
async def test_action_executor_retries_custom_tool_and_injects_page() -> None:
    page = FakePage()
    checker = FakeReadyChecker()
    attempts = {"count": 0}

    async def flaky_tool(*, page, note: str) -> str:
        assert page is not None
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("try again")
        return f"remembered {note}"

    executor = AsyncPlaywrightActionExecutor(
        page,
        viewport_width=1280,
        viewport_height=800,
        page_ready_checker=checker,
        custom_tools={"remember": flaky_tool},
        num_tool_retries=2,
        retry_delay=0,
    )

    result = await executor.execute("remember", {"note": "hello"})

    assert result == "remembered hello"
    assert attempts["count"] == 2
    assert checker.calls == [False, False]


@pytest.mark.asyncio
async def test_action_executor_go_back_guardrail_restores_start_page() -> None:
    page = FakePage()
    checker = FakeReadyChecker()
    executor = AsyncPlaywrightActionExecutor(
        page,
        viewport_width=1280,
        viewport_height=800,
        page_ready_checker=checker,
    )

    await executor.mark_current_page_as_start()
    result = await executor.execute("go_back", {})

    assert "cannot go back further" in result
    assert page.back_calls == 1
    assert page.forward_calls == 1
    assert checker.calls == [False, False]


@pytest.mark.asyncio
async def test_action_executor_type_defaults_match_n1_behavior() -> None:
    page = FakePage()
    checker = FakeReadyChecker()
    executor = AsyncPlaywrightActionExecutor(
        page,
        viewport_width=1280,
        viewport_height=800,
        page_ready_checker=checker,
    )

    result = await executor.execute("type", {"text": "hello"})

    assert result == "Typed 5 characters"
    expected_select_all = "Meta+a" if sys.platform == "darwin" else "Control+a"
    assert page.keyboard.calls == [
        ("press", expected_select_all),
        ("press", "Backspace"),
        ("type", "hello"),
        ("press", "Enter"),
    ]
    assert checker.calls == [False]


@pytest.mark.asyncio
async def test_action_executor_type_can_disable_n1_defaults_for_n1_5() -> None:
    page = FakePage()
    checker = FakeReadyChecker()
    executor = AsyncPlaywrightActionExecutor(
        page,
        viewport_width=1280,
        viewport_height=800,
        page_ready_checker=checker,
        default_clear_before_typing=False,
        default_press_enter_after_typing=False,
    )

    result = await executor.execute("type", {"text": "hello"})

    assert result == "Typed 5 characters"
    assert page.keyboard.calls == [("type", "hello")]
    assert checker.calls == [False]
