"""Unhide a hidden application without activating it.

``launch_app`` starts the target hidden so it cannot take focus from the user, and cua-driver
0.23 refuses raw keyboard input to a hidden or minimized window (``minimized_or_hidden_window``).
A window-scope session therefore unhides the app before driving it: ``NSRunningApplication.unhide``
shows its windows behind the active application without changing the frontmost app. The call is
made through the ObjC bridge of ``osascript``, so it needs no Apple Events grant for the target and
no accessibility permission of its own.
"""

from __future__ import annotations

import asyncio
import re

from .frontmost import _run

_VISIBILITY_SETTLE_SECONDS = 0.2
_VISIBILITY_POLLS = 5
_HIDDEN_KEY_PATTERN = re.compile(r"^\s*hidden\s*=\s*(\S+)", re.IGNORECASE | re.MULTILINE)
# Zero-argument ObjC methods are invoked by property access in the JXA bridge.
_UNHIDE_SCRIPT = (
    "ObjC.import('AppKit');"
    "var app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(%d);"
    "if (app.isNil()) { 'missing' } else { app.unhide; 'requested' }"
)


def parse_lsappinfo_hidden(output: str) -> bool | None:
    """Read the hidden state out of ``lsappinfo info -only hidden <ASN>`` output.

    Current macOS prints the state as a flag in the summary line (``[ NULL ]  [ NULL ]  (hidden)``
    when hidden, no flag when visible); the ``key = value`` layout used for other fields
    (``hidden = true``) is accepted too. Empty output means LaunchServices does not know the app.
    """
    if not output.strip():
        return None
    match = _HIDDEN_KEY_PATTERN.search(output)
    if match is not None:
        return match.group(1).strip().strip('"').lower() in {"true", "yes", "1"}
    return "(hidden)" in output


async def application_hidden(pid: int) -> bool | None:
    """Report whether LaunchServices lists ``pid`` as hidden, or None when it cannot say."""
    try:
        asn = await _run("lsappinfo", "find", f"pid={pid}")
        if not asn or not asn.strip().startswith("ASN:"):
            return None
        info = await _run("lsappinfo", "info", "-only", "hidden", asn.strip())
    except asyncio.TimeoutError:
        return None
    if info is None:
        return None
    return parse_lsappinfo_hidden(info)


async def unhide_application(pid: int) -> bool:
    """Ask AppKit to unhide ``pid`` and report whether LaunchServices then shows it as visible.

    False means the app is still hidden or the state could not be established; callers treat
    that as advisory (the driver will refuse keyboard input itself if the window stays hidden).
    """
    try:
        outcome = await _run("/usr/bin/osascript", "-l", "JavaScript", "-e", _UNHIDE_SCRIPT % pid)
    except asyncio.TimeoutError:
        return False
    if outcome is None or outcome.strip() != "requested":
        return False
    for _ in range(_VISIBILITY_POLLS):
        hidden = await application_hidden(pid)
        if hidden is False:
            return True
        await asyncio.sleep(_VISIBILITY_SETTLE_SECONDS)
    return False
