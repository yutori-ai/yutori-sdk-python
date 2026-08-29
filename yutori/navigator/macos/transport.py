"""Persistent JSON-RPC transport for ``cua-driver mcp``."""

from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import suppress
from pathlib import Path
from typing import Any

_RPC_TIMEOUT_SECONDS = 30.0
_PROCESS_EXIT_TIMEOUT_SECONDS = 3.0
_RPC_STREAM_LIMIT_BYTES = 32 * 1024 * 1024


class CuaDriverError(RuntimeError):
    pass


class CuaDriverConnectionError(CuaDriverError):
    pass


class CuaDriverToolError(CuaDriverError):
    pass


class CuaDriverUncertainActionError(CuaDriverError):
    """A mutating request lost its acknowledgement and must not be retried."""


def find_cua_driver_binary() -> Path:
    discovered = shutil.which("cua-driver")
    if discovered:
        return Path(discovered)
    try:
        from cua_driver import get_binary_path

        candidate = Path(get_binary_path())
    except (ImportError, OSError) as error:
        raise CuaDriverError(
            "cua-driver is not installed; install `yutori[macos]` or run `yutori-mcp computer-use setup`."
        ) from error
    if not candidate.is_file():
        raise CuaDriverError(f"cua-driver binary is missing: {candidate}")
    return candidate


class CuaDriverTransport:
    """One serialized, restartable MCP session over a persistent subprocess."""

    def __init__(
        self,
        binary: "str | Path | None" = None,
        *,
        request_timeout_seconds: float = _RPC_TIMEOUT_SECONDS,
    ) -> None:
        self.binary = Path(binary) if binary is not None else None
        self.request_timeout_seconds = request_timeout_seconds
        self._process: "asyncio.subprocess.Process | None" = None
        self._stderr_task: "asyncio.Task[None] | None" = None
        self._next_id = 0
        self._lock = asyncio.Lock()
        self._closing = False

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        if self.running:
            return
        self._closing = False
        binary = self.binary or find_cua_driver_binary()
        try:
            self._process = await asyncio.create_subprocess_exec(
                str(binary),
                "mcp",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=_RPC_STREAM_LIMIT_BYTES,
            )
        except OSError as error:
            raise CuaDriverConnectionError(f"Failed to start cua-driver: {error}") from error
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            await self._request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "yutori-python-sdk", "version": "0.9.3"},
                },
            )
            await self._notify("notifications/initialized")
        except BaseException:
            await self.close()
            raise

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        read_only: bool = False,
        timeout_seconds: "float | None" = None,
    ) -> dict[str, Any]:
        """Call once; reconnect/retry only operations explicitly marked read-only."""
        if not self.running:
            await self.start()
        try:
            return await self._call_tool_once(name, arguments, timeout_seconds=timeout_seconds)
        except CuaDriverConnectionError as error:
            if not read_only:
                # Discard the stream carrying the lost acknowledgement before
                # any caller attempts a fresh read-only observation.
                with suppress(CuaDriverError):
                    await self._restart()
                raise CuaDriverUncertainActionError(
                    f"Cua Driver acknowledgement was lost for {name}; the action was not retried."
                ) from error
            await self._restart()
            return await self._call_tool_once(name, arguments, timeout_seconds=timeout_seconds)

    async def close(self) -> None:
        self._closing = True
        process, self._process = self._process, None
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=_PROCESS_EXIT_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=_PROCESS_EXIT_TIMEOUT_SECONDS)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)
            self._stderr_task = None

    async def _restart(self) -> None:
        await self.close()
        await self.start()

    async def _call_tool_once(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: "float | None",
    ) -> dict[str, Any]:
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout_seconds=timeout_seconds,
        )
        if result.get("isError") is True:
            details = " ".join(
                str(part.get("text"))
                for part in result.get("content") or []
                if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
            )
            raise CuaDriverToolError(f"Cua Driver {name} failed: {details or 'unknown error'}")
        return result

    async def _notify(self, method: str, params: "dict[str, Any] | None" = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        await self._write(message)

    async def _request(
        self,
        method: str,
        params: "dict[str, Any] | None" = None,
        *,
        timeout_seconds: "float | None" = None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._next_id += 1
            request_id = self._next_id
            message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                message["params"] = params
            await self._write(message)
            process = self._process
            if process is None or process.stdout is None:
                raise CuaDriverConnectionError("Cua Driver is not running.")
            timeout = self.request_timeout_seconds if timeout_seconds is None else timeout_seconds
            while True:
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
                except (asyncio.TimeoutError, asyncio.LimitOverrunError) as error:
                    raise CuaDriverConnectionError(
                        f"Cua Driver RPC {method} timed out after {timeout:g} seconds."
                    ) from error
                except (OSError, ValueError) as error:
                    raise CuaDriverConnectionError(f"Cua Driver RPC {method} failed: {error}") from error
                if not line:
                    code = process.returncode
                    if code is None:
                        code = await process.wait()
                    raise CuaDriverConnectionError(f"Cua Driver exited unexpectedly with status {code}.")
                try:
                    response = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(response, dict) or response.get("id") != request_id:
                    continue
                if response.get("error"):
                    raise CuaDriverToolError(f"Cua Driver RPC error: {response['error']}")
                result = response.get("result")
                if not isinstance(result, dict):
                    raise CuaDriverConnectionError(f"Cua Driver RPC {method} returned a non-object result.")
                return result

    async def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise CuaDriverConnectionError("Cua Driver is not running.")
        try:
            process.stdin.write(json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode() + b"\n")
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as error:
            raise CuaDriverConnectionError(f"Cua Driver write failed: {error}") from error

    async def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while await process.stderr.read(4096):
                pass
        except asyncio.CancelledError:
            pass
