"""Page readiness helpers for Playwright-style Navigator agent loops."""

from __future__ import annotations

import asyncio
import logging
from functools import cached_property
from typing import Any, Protocol

from ._assets import load_js_asset

logger = logging.getLogger(__name__)
_REPLACE_NATIVE_SELECT_DROPDOWN_JS = load_js_asset("replace_native_select_dropdown.js")
_DISABLE_NEW_TABS_JS = load_js_asset("disable_new_tabs.js")
_DISABLE_PRINTING_JS = load_js_asset("disable_printing.js")


class SupportsAsyncPageReady(Protocol):
    """A Playwright-style async page that can be evaluated for readiness."""

    url: str

    async def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript against the page."""


class PageReadyChecker:
    """Wait for a page to become stable enough for the next agent step."""

    def __init__(
        self,
        timeout: int = 30,
        initial_wait: float = 2.0,
        wait_after_ready: float = 0.0,
        replace_native_select_dropdown: bool = False,
        disable_new_tabs: bool = False,
        disable_printing: bool = False,
        raise_on_blank_page: bool = False,
        poll_interval: float = 1.0,
    ) -> None:
        self.timeout = timeout
        self.initial_wait = initial_wait
        self.wait_after_ready = wait_after_ready
        self.replace_native_select_dropdown = replace_native_select_dropdown
        self.disable_new_tabs = disable_new_tabs
        self.disable_printing = disable_printing
        self.raise_on_blank_page = raise_on_blank_page
        self.poll_interval = poll_interval

    async def wait_until_ready(self, page: SupportsAsyncPageReady, fast_mode: bool = False) -> bool:
        """Wait until the page is ready, or return ``False`` when timing out."""

        try:
            return await asyncio.wait_for(self._wait_until_ready(page, fast_mode), timeout=self.timeout)
        except asyncio.TimeoutError:
            logger.error("Page did not become ready within %ss for URL: %s", self.timeout, page.url)
            self._check_blank_page(page)
            return False

    async def _wait_until_ready(self, page: SupportsAsyncPageReady, fast_mode: bool = False) -> bool:
        speedup_factor = 0.2 if fast_mode else 1.0
        await asyncio.sleep(self.initial_wait * speedup_factor)

        while True:
            if await self.is_ready(page):
                await asyncio.sleep(self.wait_after_ready * speedup_factor)
                self._check_blank_page(page)
                return True
            await asyncio.sleep(self.poll_interval * speedup_factor)

    async def is_ready(self, page: SupportsAsyncPageReady) -> bool:
        """Check page readiness without applying a timeout wrapper."""

        try:
            ready_state = await page.evaluate(self.page_ready_check_js)
        except Exception as exc:
            logger.warning("Page evaluate function failed: %s", exc)
            return False

        if not ready_state:
            logger.info("Page is still loading: %s", page.url)
            return False

        return True

    @cached_property
    def page_ready_check_js(self) -> str:
        lines = [
            "() => {",
            "    if (document.readyState !== 'complete') return false;",
            "    if (window.performance && window.performance.getEntriesByType) {",
            "        const resources = window.performance.getEntriesByType('resource');",
            "        const pendingResources = resources.filter(r => !r.responseEnd);",
            "        if (pendingResources.length > 0) return false;",
            "    }",
        ]
        if self.replace_native_select_dropdown:
            lines.append(_REPLACE_NATIVE_SELECT_DROPDOWN_JS)
        if self.disable_new_tabs:
            lines.append(_DISABLE_NEW_TABS_JS)
        if self.disable_printing:
            lines.append(_DISABLE_PRINTING_JS)
        lines.extend(
            [
                "    return true;",
                "}",
            ]
        )
        return "\n".join(lines)

    def _check_blank_page(self, page: SupportsAsyncPageReady) -> None:
        if not self.raise_on_blank_page:
            return
        if page.url == "about:blank" or page.url.startswith("chrome-error://") or page.url.startswith("about:neterror"):
            raise RuntimeError(f"Page is blank with url: {page.url}")


class NoOpPageReadyChecker(PageReadyChecker):
    """Disable ready checking for sites that block ``page.evaluate``."""

    async def wait_until_ready(self, page: SupportsAsyncPageReady, fast_mode: bool = False) -> bool:  # noqa: ARG002
        return True

    async def is_ready(self, page: SupportsAsyncPageReady) -> bool:  # noqa: ARG002
        return True


__all__ = [
    "NoOpPageReadyChecker",
    "PageReadyChecker",
]
