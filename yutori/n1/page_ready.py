"""Page readiness helpers for Playwright-style n1 agent loops."""

from __future__ import annotations

import asyncio
import logging
from functools import cached_property
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_REPLACE_NATIVE_SELECT_DROPDOWN_JS = """
(() => {
    const handledSelectElementsConvergence = new WeakSet();

    const overwriteDefaultSelectConvergence = (input = null) => {
        let activeSelectElement = null;
        const rootElement = input || document.documentElement;

        function createCustomSelectElement() {
            const customSelect = document.createElement('div');
            customSelect.id = 'yutori-custom-dropdown-element';
            customSelect.style.position = 'absolute';
            customSelect.style.zIndex = 2147483646;
            customSelect.style.display = 'none';
            document.body.appendChild(customSelect);

            const optionsList = document.createElement('div');
            optionsList.style.border = '1px solid #ccc';
            optionsList.style.backgroundColor = '#fff';
            optionsList.style.color = 'black';
            customSelect.appendChild(optionsList);

            return customSelect;
        }

        function hideCustomSelect(customSelect) {
            customSelect.style.display = 'none';
            activeSelectElement = null;
        }

        function showCustomSelect(select) {
            activeSelectElement = select;
            const customSelect = rootElement.querySelector('#yutori-custom-dropdown-element');
            const optionsList = customSelect.firstChild;
            optionsList.innerHTML = '';
            optionsList.style.overflowY = 'auto';
            optionsList.style.maxHeight = 'none';

            Array.from(select.options).forEach(option => {
                const customOption = document.createElement('div');
                customOption.className = 'custom-option';
                customOption.style.padding = '8px';
                customOption.style.cursor = 'pointer';
                customOption.textContent = option.text;
                customOption.dataset.value = option.value;
                optionsList.appendChild(customOption);

                customOption.addEventListener('mouseenter', () => {
                    customOption.style.backgroundColor = '#f0f0f0';
                });

                customOption.addEventListener('mouseleave', () => {
                    customOption.style.backgroundColor = '';
                });

                customOption.addEventListener('mousedown', (e) => {
                    e.stopPropagation();
                    select.value = customOption.dataset.value;
                    hideCustomSelect(customSelect);
                    if (!window.location.href.includes('resy.com')) {
                        select.dispatchEvent(new InputEvent('focus', { bubbles: true, cancelable: true }));
                    }
                    select.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true }));
                    select.dispatchEvent(new InputEvent('change', { bubbles: true, cancelable: true }));
                    select.dispatchEvent(new InputEvent('blur', { bubbles: true, cancelable: true }));
                });
            });

            const selectRect = select.getBoundingClientRect();
            customSelect.style.visibility = 'hidden';
            customSelect.style.display = 'block';

            const margin = 8;
            const viewportWidth = window.innerWidth;
            const minWidth = Math.max(selectRect.width, 120);
            const contentWidth = optionsList.scrollWidth + 8;
            const maxWidth = Math.max(0, viewportWidth - margin * 2);
            const targetWidth = Math.min(Math.max(minWidth, contentWidth), maxWidth);

            const preferredLeft = selectRect.left + window.scrollX;
            const viewportLeft = window.scrollX + margin;
            const viewportRight = window.scrollX + viewportWidth - margin;
            const clampedLeft = Math.min(Math.max(preferredLeft, viewportLeft), viewportRight - targetWidth);

            customSelect.style.width = `${targetWidth}px`;
            customSelect.style.left = `${clampedLeft}px`;

            const optionsHeight = optionsList.scrollHeight;
            const viewportTop = window.scrollY + margin;
            const viewportBottom = window.scrollY + window.innerHeight - margin;
            const maxHeight = Math.max(0, viewportBottom - viewportTop);
            const desiredHeight = Math.min(optionsHeight, maxHeight);

            const spaceBelow = window.innerHeight - selectRect.bottom - margin;
            const spaceAbove = selectRect.top - margin;

            optionsList.style.maxHeight = `${desiredHeight}px`;

            let dropdownTop;
            if (spaceBelow >= desiredHeight) {
                dropdownTop = selectRect.bottom + window.scrollY;
            } else if (spaceAbove >= desiredHeight) {
                dropdownTop = selectRect.top + window.scrollY - desiredHeight;
            } else {
                const centeredTop = selectRect.top + window.scrollY + (selectRect.height / 2) - (desiredHeight / 2);
                dropdownTop = Math.min(Math.max(centeredTop, viewportTop), viewportBottom - desiredHeight);
            }

            customSelect.style.top = `${dropdownTop}px`;
            customSelect.style.visibility = 'visible';
            select.focus();

            if (!optionsList.dataset.wheelHandlerAttached) {
                optionsList.addEventListener('wheel', (event) => {
                    event.preventDefault();
                    optionsList.scrollTop += event.deltaY;
                }, { passive: false });
                optionsList.dataset.wheelHandlerAttached = 'true';
            }

            select.addEventListener('blur', () => {
                hideCustomSelect(customSelect);
            });

            select.addEventListener('change', () => {
                hideCustomSelect(customSelect);
            });
        }

        let customSelect = rootElement.querySelector('#yutori-custom-dropdown-element');
        if (!customSelect) {
            customSelect = createCustomSelectElement();
        }

        function findSelectInShadowRoot(element) {
            return element.shadowRoot ? element.shadowRoot.querySelectorAll('select') : [];
        }

        let shadowSelects = [];
        rootElement.querySelectorAll('*').forEach(el => {
            shadowSelects.push(...findSelectInShadowRoot(el));
        });

        const lightSelects = Array.from(rootElement.querySelectorAll('select'));
        const allSelects = [...lightSelects, ...shadowSelects];

        allSelects.forEach(select => {
            if (select.hasAttribute('multiple')) return;
            if (!handledSelectElementsConvergence.has(select)) {
                select.addEventListener('mousedown', (e) => {
                    if (!e.defaultPrevented) {
                        if (customSelect.style.display === 'block' && activeSelectElement === select) {
                            hideCustomSelect(customSelect);
                        } else {
                            showCustomSelect(select);
                        }
                        e.preventDefault();
                    }
                });
                handledSelectElementsConvergence.add(select);
            }
        });
    };

    overwriteDefaultSelectConvergence();
})();
""".strip()

_DISABLE_NEW_TABS_JS = """
(() => {
    const removeTargets = () => {
        document.querySelectorAll('[target], [formtarget]').forEach(el => {
            const target = el.getAttribute('target') || el.getAttribute('formtarget');
            if (target && target !== '_self' && target !== '_parent' && target !== '_top') {
                el.removeAttribute('target');
                el.removeAttribute('formtarget');
            }
        });
    };
    removeTargets();

    const openDescriptor = Object.getOwnPropertyDescriptor(window, 'open');
    if (!openDescriptor || openDescriptor.configurable !== false) {
        Object.defineProperty(window, 'open', {
            value: function (url) {
                if (typeof url === 'string' && url && !url.startsWith('about:')) {
                    window.location.href = url;
                }
                return { closed: false, focus: () => {}, blur: () => {}, close: () => {}, postMessage: () => {} };
            },
            writable: false,
            configurable: false
        });
    }

    if (!Element.prototype._setAttributePatched) {
        const originalSetAttribute = Element.prototype.setAttribute;
        Element.prototype.setAttribute = function (name, value) {
            if ((name.toLowerCase() === 'target' || name.toLowerCase() === 'formtarget') &&
                value && value !== '_self' && value !== '_parent' && value !== '_top') {
                return;
            }
            return originalSetAttribute.call(this, name, value);
        };
        Element.prototype._setAttributePatched = true;
    }

    if (!HTMLFormElement.prototype._targetPatched) {
        Object.defineProperty(HTMLFormElement.prototype, 'target', {
            set: function (val) {
                if (!val || val === '_self' || val === '_parent' || val === '_top') {
                    this.setAttribute('target', val || '');
                }
            },
            get: function () { return this.getAttribute('target') || ''; },
            configurable: true
        });
        HTMLFormElement.prototype._targetPatched = true;
    }

    if (!HTMLAnchorElement.prototype._targetPatched) {
        Object.defineProperty(HTMLAnchorElement.prototype, 'target', {
            set: function (val) {
                if (!val || val === '_self' || val === '_parent' || val === '_top') {
                    this.setAttribute('target', val || '');
                }
            },
            get: function () { return this.getAttribute('target') || ''; },
            configurable: true
        });
        HTMLAnchorElement.prototype._targetPatched = true;
    }

    if (!window._submitListenerPatched) {
        document.addEventListener('submit', (e) => {
            const target = e.target.getAttribute('target');
            if (target && target !== '_self' && target !== '_parent' && target !== '_top') {
                e.target.removeAttribute('target');
            }
        }, true);
        window._submitListenerPatched = true;
    }

    if (!window._mutationObserverPatched) {
        new MutationObserver(removeTargets).observe(document.documentElement, {
            childList: true, subtree: true, attributes: true, attributeFilter: ['target', 'formtarget']
        });
        window._mutationObserverPatched = true;
    }
})();
""".strip()

_DISABLE_PRINTING_JS = """
(() => {
    'use strict';
    if (window.__printGuardInstalled__) return;
    window.__printGuardInstalled__ = true;

    const noop = () => log('window.print() intercepted');
    const log = (...args) => { try { console.debug('[print-guard]', ...args); } catch {} };

    try {
        Object.defineProperty(window, 'print', { configurable: true, writable: true, value: noop });
    } catch {
        try { window.print = noop; } catch {}
    }

    log('print-guard installed');
})();
""".strip()


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
