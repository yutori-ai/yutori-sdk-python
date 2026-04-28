"""Key name mapping for the Navigator-n1.5 lowercase key format.

Navigator-n1.5 returns lowercase key names (e.g. ``ctrl+c``, ``enter``, ``left``)
while Playwright expects Playwright-style names (e.g. ``Control+c``,
``Enter``, ``ArrowLeft``).  Use :func:`map_key_to_playwright` to convert
a full Navigator-n1.5 key expression to a Playwright-compatible string.

Only the Playwright ``key`` name is needed here (not ``code`` / ``keyCode``).
"""

from __future__ import annotations

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


def _split_into_combos(key_expr: str) -> list[list[str]]:
    """Split *key_expr* into one token list per sequential press.

    Spaces separate sequential presses; ``+`` separates simultaneous tokens
    within a press. Empty space-separated parts are dropped, but empty
    ``+``-separated tokens are preserved so callers can decide whether to
    keep or filter them.
    """
    return [part.split("+") for part in key_expr.strip().split(" ") if part]


def map_key_to_playwright(key_expr: str) -> list[str]:
    """Convert a Navigator-n1.5 key expression to a list of Playwright key-press strings.

    Navigator-n1.5 uses ``+`` for simultaneous combos and spaces for sequential
    presses.  For example:

    * ``"ctrl+c"``         → ``["Control+c"]``
    * ``"down down enter"`` → ``["ArrowDown", "ArrowDown", "Enter"]``
    * ``"ctrl+shift+t"``   → ``["Control+Shift+t"]``

    Returns a list because sequential presses need separate
    ``keyboard.press()`` calls.  The returned strings use ``+`` as a
    combo delimiter and are suitable for ``keyboard.press()`` which
    understands combos.  For ``keyboard.down()``/``keyboard.up()``
    (which only accept single keys), use :func:`map_keys_individual`.
    """
    return ["+".join(_map_single_key(t) for t in tokens) for tokens in _split_into_combos(key_expr)]


def map_keys_individual(key_expr: str) -> list[str]:
    """Convert a Navigator-n1.5 key expression to a flat list of individual Playwright keys.

    Unlike :func:`map_key_to_playwright`, this never joins keys with ``+``.
    Each token is mapped individually, making the result safe for
    ``keyboard.down()`` / ``keyboard.up()`` which only accept single keys.

    * ``"ctrl+c"``          → ``["Control", "c"]``
    * ``"down down enter"``  → ``["ArrowDown", "ArrowDown", "Enter"]``
    * ``"ctrl+plus"``        → ``["Control", "+"]``
    """
    return [_map_single_key(token) for tokens in _split_into_combos(key_expr) for token in tokens if token]
