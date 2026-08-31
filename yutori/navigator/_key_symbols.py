"""Shared word-form punctuation key names, used by both key-mapping schemes.

``keys.py`` (Navigator n1.5 -> Playwright) and ``n2_actions.py`` (Navigator n2
-> the SDK's internal key vocabulary) each need to recognize spelled-out
punctuation key names like ``"comma"`` or ``"bracketleft"``. Both target
vocabularies represent punctuation as the literal character itself, so the
two modules' tables were identical by coincidence rather than by import --
this module is the single source of truth those tables merge in, so a fix or
addition can't update one and silently miss the other.
"""

from __future__ import annotations

PUNCTUATION_KEY_NAMES: dict[str, str] = {
    "minus": "-",
    "plus": "+",
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
}
