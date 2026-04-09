"""Key name mapping for n1.5's lowercase key format.

n1.5 returns lowercase key names (e.g. ``ctrl+c``, ``enter``, ``left``)
while Playwright expects Playwright-style names (e.g. ``Control+c``,
``Enter``, ``ArrowLeft``).  Use :func:`map_key_to_playwright` to convert
a full n1.5 key expression to a Playwright-compatible string.
"""

from __future__ import annotations

# Single key name mapping: n1.5 lowercase → Playwright
_KEY_MAP: dict[str, str] = {
    # Modifiers
    "ctrl": "Control",
    "alt": "Alt",
    "shift": "Shift",
    "meta": "Meta",
    "command": "Meta",
    "super": "Meta",
    # Common
    "enter": "Enter",
    "backspace": "Backspace",
    "delete": "Delete",
    "tab": "Tab",
    "esc": "Escape",
    "space": " ",
    # Arrow keys
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "up": "ArrowUp",
    "down": "ArrowDown",
    # Page navigation
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "home": "Home",
    "end": "End",
    # Function keys
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}


def _map_single_key(key: str) -> str:
    """Map one key token (no ``+`` or spaces) to its Playwright name."""
    return _KEY_MAP.get(key.lower().strip(), key)


def map_key_to_playwright(key_expr: str) -> list[str]:
    """Convert an n1.5 key expression to a list of Playwright key-press strings.

    n1.5 uses ``+`` for simultaneous combos and spaces for sequential
    presses.  For example:

    * ``"ctrl+c"``         → ``["Control+c"]``
    * ``"down down enter"`` → ``["ArrowDown", "ArrowDown", "Enter"]``
    * ``"ctrl+shift+t"``   → ``["Control+Shift+t"]``

    Returns a list because sequential presses need separate
    ``keyboard.press()`` calls.
    """
    parts = key_expr.strip().split(" ")
    result: list[str] = []
    for part in parts:
        if not part:
            continue
        tokens = part.split("+")
        mapped = "+".join(_map_single_key(t) for t in tokens)
        result.append(mapped)
    return result
