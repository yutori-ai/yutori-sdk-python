"""Shared asyncio lifecycle helpers for macOS computer-use subprocesses and tasks.

Both the cua-driver transport and the presentation host manage their own
``asyncio.subprocess.Process`` and need the same escalating shutdown: close
stdin, wait, then escalate to ``terminate()`` and finally ``kill()`` if the
process does not exit in time. They (and the frame-polling and no-progress
helpers) also repeatedly need to cancel a set of in-flight tasks -- e.g. the
loser of an ``asyncio.wait(..., return_when=FIRST_COMPLETED)`` race -- and
await their cancellation before continuing.
"""

from __future__ import annotations

import asyncio
from typing import Any


async def cancel_and_drain(*tasks: "asyncio.Task[Any]") -> None:
    """Cancel any of the given tasks still running, then await all of them.

    Cancelling an already-done task is a no-op, so this is safe whether the
    tasks are a raced pair (one finished, one not) or an already-pending set.
    `return_exceptions=True` absorbs each task's `CancelledError`/result so
    this never raises on the caller's behalf.
    """
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


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
