"""Key name mapping for n1.5's lowercase key format.

n1.5 returns lowercase key names (e.g. ``ctrl+c``, ``enter``, ``left``)
while Playwright expects Playwright-style names (e.g. ``Control+c``,
``Enter``, ``ArrowLeft``).  Use :func:`map_key_to_playwright` to convert
a full n1.5 key expression to a Playwright-compatible string.

The mapping mirrors the internal ``browser_key_map.KEY_MAP`` — only the
Playwright ``key`` field is needed here (not ``code`` / ``keyCode``).
"""

from __future__ import annotations

# Single key name mapping: n1.5 lowercase → Playwright key name.
# Derived from the internal browser_key_map.KEY_MAP — every entry here
# corresponds to one in that authoritative map.
_KEY_MAP: dict[str, str] = {
    # Modifier keys
    "ctrl": "Control",
    "control": "Control",
    "cmd": "Meta",
    "command": "Meta",
    "meta": "Meta",
    "alt": "Alt",
    "option": "Alt",
    "shift": "Shift",
    "super": "Meta",
    # Enter keys
    "enter": "Enter",
    "return": "Enter",
    "kp_enter": "Enter",
    # Navigation keys
    "tab": "Tab",
    "delete": "Delete",
    "backspace": "Backspace",
    "escape": "Escape",
    "esc": "Escape",
    "space": " ",
    " ": " ",
    # Arrow keys
    "arrowup": "ArrowUp",
    "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    # Page navigation
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    # Function keys
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
    # Special characters
    ";": ";",
    "=": "=",
    ",": ",",
    "-": "-",
    ".": ".",
    "/": "/",
    "`": "`",
    "[": "[",
    "\\": "\\",
    "]": "]",
    "'": "'",
    "!": "!",
    "@": "@",
    "#": "#",
    "$": "$",
    "%": "%",
    "^": "^",
    "&": "&",
    "*": "*",
    "(": "(",
    ")": ")",
    "_": "_",
    "+": "+",
    "{": "{",
    "}": "}",
    "|": "|",
    ":": ":",
    '"': '"',
    "<": "<",
    ">": ">",
    "?": "?",
    "~": "~",
    # Word-form punctuation
    "plus": "+",
    "minus": "-",
    "equal": "=",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "backslash": "\\",
    "semicolon": ";",
    "quote": "'",
    "backquote": "`",
    "bracketleft": "[",
    "bracketright": "]",
    # Lock keys
    "capslock": "CapsLock",
    "numlock": "NumLock",
    "scrolllock": "ScrollLock",
    # Media / misc keys
    "pause": "Pause",
    "insert": "Insert",
    "printscreen": "PrintScreen",
    # Numpad
    "numpad0": "0",
    "numpad1": "1",
    "numpad2": "2",
    "numpad3": "3",
    "numpad4": "4",
    "numpad5": "5",
    "numpad6": "6",
    "numpad7": "7",
    "numpad8": "8",
    "numpad9": "9",
    "numpadmultiply": "*",
    "numpadadd": "+",
    "numpadsubtract": "-",
    "numpaddecimal": ".",
    "numpaddivide": "/",
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
