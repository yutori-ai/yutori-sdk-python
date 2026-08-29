"""Shared asyncio lifecycle helpers for macOS computer-use subprocesses and tasks.

Both the cua-driver transport and the presentation host speak newline-delimited
JSON-RPC over a long-lived child process, so they spawn it identically (all
three pipes plus the same oversized stream limit), drain its stderr identically,
and need the same escalating shutdown: close stdin, wait, then escalate to
``terminate()`` and finally ``kill()`` if the process does not exit in time.
They (and the frame-polling and no-progress helpers) also repeatedly need to
cancel a set of in-flight tasks -- e.g. the loser of an
``asyncio.wait(..., return_when=FIRST_COMPLETED)`` race -- and await their
cancellation before continuing.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Screenshot-bearing JSON-RPC frames routinely exceed asyncio's 64 KiB default,
# so both stdio peers raise the stream limit to the same ceiling.
RPC_STREAM_LIMIT_BYTES = 32 * 1024 * 1024

_STDERR_CHUNK_BYTES = 4096


async def spawn_rpc_subprocess(*argv: str) -> asyncio.subprocess.Process:
    """Spawn a newline-delimited JSON-RPC child with all three pipes attached.

    Callers own error translation and the process handle; this only fixes the
    pipe wiring and stream limit that every stdio RPC peer here shares.
    """
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=RPC_STREAM_LIMIT_BYTES,
    )


async def drain_stream(stream: "asyncio.StreamReader | None") -> None:
    """Read and discard a piped stream until EOF so the child never blocks on it.

    Returns immediately when the stream is absent, and treats cancellation as an
    ordinary stop -- this runs as a detached background task whose only job is to
    keep the pipe from filling up.
    """
    if stream is None:
        return
    try:
        while await stream.read(_STDERR_CHUNK_BYTES):
            pass
    except asyncio.CancelledError:
        pass


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
