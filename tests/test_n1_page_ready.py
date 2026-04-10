from __future__ import annotations

from collections.abc import Iterable

import pytest

from yutori.n1.page_ready import NoOpPageReadyChecker, PageReadyChecker


class FakeAsyncPage:
    def __init__(self, *, url: str = "https://example.com", ready_values: Iterable[object] | None = None) -> None:
        self.url = url
        self.expressions: list[str] = []
        self._ready_values = list(ready_values) if ready_values is not None else [True]
        self._last_value: object = self._ready_values[-1] if self._ready_values else True

    async def evaluate(self, expression: str):
        self.expressions.append(expression)
        if self._ready_values:
            self._last_value = self._ready_values.pop(0)
        if isinstance(self._last_value, Exception):
            raise self._last_value
        return self._last_value


@pytest.mark.asyncio
async def test_page_ready_checker_includes_optional_js_injections() -> None:
    checker = PageReadyChecker(
        initial_wait=0,
        wait_after_ready=0,
        poll_interval=0,
        replace_native_select_dropdown=True,
        disable_new_tabs=True,
        disable_printing=True,
    )
    page = FakeAsyncPage()

    assert await checker.wait_until_ready(page) is True

    assert len(page.expressions) == 1
    expression = page.expressions[0]
    assert "document.readyState !== 'complete'" in expression
    assert "yutori-custom-dropdown-element" in expression
    assert "Object.defineProperty(window, 'open'" in expression
    assert "window.__printGuardInstalled__" in expression


@pytest.mark.asyncio
async def test_page_ready_checker_timeout_returns_false_for_blank_page_when_not_raising() -> None:
    checker = PageReadyChecker(
        timeout=0.01,
        initial_wait=0,
        wait_after_ready=0,
        poll_interval=0.001,
        raise_on_blank_page=False,
    )
    page = FakeAsyncPage(url="about:blank", ready_values=[False])

    assert await checker.wait_until_ready(page) is False


@pytest.mark.asyncio
async def test_page_ready_checker_timeout_raises_for_blank_page_when_configured() -> None:
    checker = PageReadyChecker(
        timeout=0.01,
        initial_wait=0,
        wait_after_ready=0,
        poll_interval=0.001,
        raise_on_blank_page=True,
    )
    page = FakeAsyncPage(url="about:blank", ready_values=[False])

    with pytest.raises(RuntimeError, match="Page is blank"):
        await checker.wait_until_ready(page)


@pytest.mark.asyncio
async def test_noop_page_ready_checker_bypasses_evaluate_failures() -> None:
    checker = NoOpPageReadyChecker()
    page = FakeAsyncPage(ready_values=[RuntimeError("boom")])

    assert await checker.wait_until_ready(page) is True
    assert await checker.is_ready(page) is True
    assert page.expressions == []
