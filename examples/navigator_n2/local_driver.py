"""Cua Driver 0.10 desktop-scope adapter for Cua's AsyncComputerHandler."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Literal, Optional, Union


class CuaDriverError(RuntimeError):
    """Raised when the Cua Driver process or an MCP tool call fails."""


class DriverMCPClient:
    """Minimal line-delimited JSON-RPC client for ``cua-driver mcp``."""

    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self.process is not None:
            return
        from cua_driver import get_binary_path

        self.process = await asyncio.create_subprocess_exec(
            str(get_binary_path()),
            "mcp",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        await self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "yutori-n2-cookbook", "version": "0.1"},
            },
        )
        await self.notify("notifications/initialized")

    async def _write(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise CuaDriverError("Cua Driver is not running")
        self.process.stdin.write(json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode() + b"\n")
        await self.process.stdin.drain()

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        await self._write(message)

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._lock:
            self._next_id += 1
            request_id = self._next_id
            message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                message["params"] = params
            await self._write(message)

            if self.process is None or self.process.stdout is None:
                raise CuaDriverError("Cua Driver is not running")
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    code = await self.process.wait()
                    raise CuaDriverError(f"Cua Driver exited unexpectedly with status {code}")
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if response.get("id") != request_id:
                    continue
                if response.get("error"):
                    raise CuaDriverError(f"Cua Driver RPC error: {response['error']}")
                result = response.get("result")
                return result if isinstance(result, dict) else {}

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError") is True:
            text = " ".join(
                str(part.get("text"))
                for part in result.get("content") or []
                if isinstance(part, dict) and part.get("type") == "text"
            )
            raise CuaDriverError(f"Cua Driver {name} failed: {text or 'unknown error'}")
        return result

    async def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            process.terminate()
            await process.wait()


class CuaDriverDesktop:
    """Full-display, native-pixel macOS handler backed only by Cua Driver."""

    def __init__(self, client: DriverMCPClient | None = None, *, session: str | None = None) -> None:
        self.client = client or DriverMCPClient()
        self.session = session or f"yutori-n2-{uuid.uuid4().hex[:8]}"
        self._native_size: tuple[int, int] | None = None

    async def __aenter__(self) -> "CuaDriverDesktop":
        await self.client.start()
        await self.client.call_tool("start_session", {"session": self.session, "capture_scope": "desktop"})
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        try:
            await self.client.call_tool("end_session", {"session": self.session})
        finally:
            await self.client.close()

    def _desktop_args(self, **arguments: Any) -> dict[str, Any]:
        return {"session": self.session, "scope": "desktop", **arguments}

    async def get_environment(self) -> Literal["mac"]:
        return "mac"

    async def get_dimensions(self) -> tuple[int, int]:
        if self._native_size is None:
            await self.screenshot()
        assert self._native_size is not None
        return self._native_size

    async def screenshot(self, text: Optional[str] = None) -> str:
        del text
        result = await self.client.call_tool("get_desktop_state", {"session": self.session})
        structured = result.get("structuredContent") or result.get("structured_content") or {}
        width = structured.get("screenshot_width")
        height = structured.get("screenshot_height")
        if not isinstance(width, int) or not isinstance(height, int):
            raise CuaDriverError("get_desktop_state did not return native screenshot dimensions")
        self._native_size = (width, height)
        for part in result.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "image" and part.get("data"):
                return str(part["data"])
        raise CuaDriverError("get_desktop_state did not return an inline image")

    async def click(self, x: int, y: int, button: str = "left") -> None:
        await self.client.call_tool("click", self._desktop_args(x=x, y=y, button=button))

    async def double_click(self, x: int, y: int) -> None:
        await self.client.call_tool("click", self._desktop_args(x=x, y=y, count=2))

    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
        width, height = await self.get_dimensions()
        horizontal = abs(scroll_x) > abs(scroll_y)
        delta = scroll_x if horizontal else scroll_y
        direction = ("right" if delta > 0 else "left") if horizontal else ("down" if delta > 0 else "up")
        dimension = width if horizontal else height
        amount = max(1, min(50, round(abs(delta) / max(1, dimension * 0.1))))
        await self.client.call_tool(
            "scroll",
            self._desktop_args(x=x, y=y, direction=direction, amount=amount),
        )

    async def type(self, text: str) -> None:
        await self.client.call_tool("type_text", self._desktop_args(text=text))

    async def wait(self, ms: int = 1000) -> None:
        await asyncio.sleep(ms / 1000)

    async def move(self, x: int, y: int) -> None:
        await self.client.call_tool("move_cursor", self._desktop_args(x=x, y=y))

    async def keypress(self, keys: Union[list[str], str]) -> None:
        sequence = [keys] if isinstance(keys, str) else keys
        if len(sequence) == 1:
            await self.client.call_tool("press_key", self._desktop_args(key=sequence[0]))
        else:
            await self.client.call_tool("hotkey", self._desktop_args(keys=sequence))

    async def drag(self, path: list[dict[str, int]]) -> None:
        if len(path) < 2:
            raise ValueError("drag path must contain at least two points")
        start, end = path[0], path[-1]
        await self.client.call_tool(
            "drag",
            self._desktop_args(
                from_x=start["x"],
                from_y=start["y"],
                to_x=end["x"],
                to_y=end["y"],
                delivery_mode="foreground",
            ),
        )

    async def get_current_url(self) -> str:
        return ""

    async def left_mouse_down(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        del x, y
        raise NotImplementedError("n2 uses the atomic drag action")

    async def left_mouse_up(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        del x, y
        raise NotImplementedError("n2 uses the atomic drag action")
