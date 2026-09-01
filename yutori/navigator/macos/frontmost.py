"""Frontmost-application probe used to guard keyboard delivery.

The n2 macOS loop types through the driver's desktop scope: keystrokes go to whatever
application is frontmost, with no target pid. That is what the model expects, but it
also means a focus change between the screenshot the model reasoned over and the
keystroke it chose lands the text in the wrong application. The probe here reads the
frontmost application through LaunchServices (``lsappinfo``), which needs no TCC grant
and costs a few milliseconds, so the handler can record it per observation and compare
right before delivering keys.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from dataclasses import dataclass

_LSAPPINFO_TIMEOUT_SECONDS = 2.0
_PID_PATTERN = re.compile(r"^\s*pid\s*=\s*(\d+)", re.MULTILINE)
_NAME_PATTERN = re.compile(r'^"(.*?)"\s+ASN:', re.MULTILINE)


@dataclass(frozen=True)
class FrontmostApp:
    pid: int
    name: str | None = None

    def describe(self) -> str:
        return f"{self.name} (pid {self.pid})" if self.name else f"pid {self.pid}"


def parse_lsappinfo_info(output: str) -> FrontmostApp | None:
    """Extract pid and display name from ``lsappinfo info -only pid -only name <ASN>`` output."""
    pid_match = _PID_PATTERN.search(output)
    if pid_match is None:
        return None
    name_match = _NAME_PATTERN.search(output)
    return FrontmostApp(pid=int(pid_match.group(1)), name=name_match.group(1) if name_match else None)


async def _run(*command: str) -> str | None:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return None
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=_LSAPPINFO_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        # Reap the child on both exits: a hung LaunchServices binary must not survive the
        # probe, and every screenshot or keystroke would otherwise spawn another one.
        await _reap(process)
        raise
    if process.returncode != 0:
        return None
    return stdout.decode("utf-8", "replace")


async def _reap(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(process.wait()), timeout=_LSAPPINFO_TIMEOUT_SECONDS)


async def frontmost_app() -> FrontmostApp | None:
    """Return the frontmost regular application, or None when it cannot be determined.

    A None result disables the focus guard for that step rather than blocking input:
    the probe is a safety net, and LaunchServices being unavailable (headless session,
    login window) is not evidence that focus moved.
    """
    try:
        asn = await _run("lsappinfo", "front")
        if not asn or not asn.strip().startswith("ASN:"):
            return None
        info = await _run("lsappinfo", "info", "-only", "pid", "-only", "name", asn.strip())
    except asyncio.TimeoutError:
        return None
    if info is None:
        return None
    return parse_lsappinfo_info(info)
