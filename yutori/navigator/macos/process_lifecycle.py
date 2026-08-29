"""Shared graceful-shutdown helper for asyncio subprocesses.

Both the cua-driver transport and the presentation host manage their own
``asyncio.subprocess.Process`` and need the same escalating shutdown: close
stdin, wait, then escalate to ``terminate()`` and finally ``kill()`` if the
process does not exit in time.
"""

from __future__ import annotations

import asyncio


async def terminate_process_gracefully(
    process: asyncio.subprocess.Process,
    *,
    exit_timeout: float,
    kill_timeout: "float | None" = None,
) -> None:
    """Close stdin, then escalate wait -> terminate -> kill until the process exits.

    ``kill_timeout`` bounds the wait after ``terminate()`` is sent; it defaults to
    ``exit_timeout`` when not given.
    """
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=exit_timeout)
        except asyncio.TimeoutError:
            process.terminate()
            resolved_kill_timeout = exit_timeout if kill_timeout is None else kill_timeout
            try:
                await asyncio.wait_for(process.wait(), timeout=resolved_kill_timeout)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
