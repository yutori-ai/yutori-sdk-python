"""Python controller for the bundled protocol-v2 macOS presentation host."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .overlay_build import OVERLAY_PROTOCOL_VERSION, PreparedMacOSOverlay, load_prepared_macos_overlay
from .process_lifecycle import cancel_and_drain, drain_stream, spawn_rpc_subprocess, terminate_process_gracefully
from .types import (
    CancellationLatch,
    MacOSPresentationCapabilities,
    MacOSPresentationStatus,
    ShellPresentationEvent,
)

_READY_TIMEOUT_SECONDS = 15
_OPERATION_TIMEOUT_SECONDS = 5
_ENCODE_TIMEOUT_SECONDS = 30
_PROCESS_EXIT_TIMEOUT_SECONDS = 1
_OVERLAY_LEAD_SECONDS = 0.15
_SHELL_MINIMUM_DWELL_SECONDS = 0.9
_SHELL_TERMINAL_HOLD_SECONDS = 0.9
_BACKGROUND_TERMINAL_HOLD_SECONDS = 1.5
_NORMALIZED_SCALE = 1000

_ACTION_STATUS = {
    "left_click": "Click",
    "click": "Click",
    "double_click": "Double-click",
    "right_click": "Right-click",
    "middle_click": "Middle-click",
    "triple_click": "Triple-click",
    "mouse_move": "Move",
    "move": "Move",
    "drag": "Drag",
    "scroll": "Scroll",
    "type": "Type",
    "key_press": "Key press",
    "key": "Key press",
    "wait": "Wait",
}
_CLICK_COUNTS = {
    "left_click": 1,
    "click": 1,
    "double_click": 2,
    "triple_click": 3,
    "right_click": 1,
    "middle_click": 1,
}
_SCROLL_ROTATION = {"down": 0, "up": 180, "right": -90, "left": 90}
_KEY_LABELS = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "cmd": "Cmd",
    "command": "Cmd",
    "meta": "Cmd",
    "super": "Super",
    "alt": "Alt",
    "option": "Option",
    "shift": "Shift",
    "enter": "Enter",
    "return": "Enter",
    "esc": "Esc",
    "escape": "Esc",
    "tab": "Tab",
    "space": "Space",
    "backspace": "Backspace",
    "delete": "Delete",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
}


class MacOSPresentationError(RuntimeError):
    pass


def _positive_finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _valid_stop_region(value: Any) -> "tuple[float, float, float, float] | None":
    if not isinstance(value, dict):
        return None
    fields = tuple(value.get(key) for key in ("x", "y", "width", "height"))
    if not all(isinstance(field, (int, float)) and math.isfinite(field) for field in fields):
        return None
    x, y, width, height = (float(field) for field in fields)
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1000 or y + height > 1000:
        return None
    return x, y, width, height


def _point(arguments: dict[str, Any], *keys: str) -> "tuple[float, float] | None":
    for key in keys:
        value = arguments.get(key)
        if (
            isinstance(value, (list, tuple))
            and len(value) >= 2
            and all(isinstance(component, (int, float)) and not isinstance(component, bool) for component in value[:2])
        ):
            return float(value[0]), float(value[1])
    return None


def _key_caps(expression: str) -> list[str]:
    parts = [part for part in expression.replace("+", " ").split() if part]
    labels = []
    for part in parts:
        normalized = part.lower()
        if normalized in _KEY_LABELS:
            labels.append(_KEY_LABELS[normalized])
        elif len(part) == 1 or (normalized.startswith("f") and normalized[1:].isdigit()):
            labels.append(part.upper())
        else:
            labels.append(part[:1].upper() + part[1:])
    return labels


def _action_visual(name: str, arguments: dict[str, Any]) -> "dict[str, Any] | None":
    action = name.lower()
    point = _point(arguments, "coordinates", "coordinate", "center_coordinates")
    if action in _CLICK_COUNTS:
        if point is None:
            return None
        return {"kind": action, "point": point, "badge": {"type": "loop"}, "clicks": _CLICK_COUNTS[action]}
    if action in {"mouse_move", "move"}:
        return {"kind": action, "point": point, "badge": {"type": "loop"}} if point else None
    if action == "drag":
        start = _point(arguments, "start_coordinates", "start_coordinate")
        end = point or _point(arguments, "end_coordinates")
        if start is None or end is None:
            return None
        return {"kind": action, "point": start, "to": end, "badge": {"type": "loop"}}
    if action == "scroll":
        if point is None:
            return None
        direction = str(arguments.get("direction") or "down").lower()
        return {
            "kind": action,
            "point": point,
            "badge": {"type": "glyph", "glyph": "scroll", "rotationDegrees": _SCROLL_ROTATION.get(direction, 0)},
            "direction": direction,
        }
    if action == "type" and isinstance(arguments.get("text"), str):
        return {"kind": action, "point": point, "badge": {"type": "glyph", "glyph": "type"}}
    if action in {"key_press", "key"}:
        label = str(arguments.get("key") or arguments.get("key_comb") or "")
        keys = _key_caps(label)
        if not keys:
            return None
        lowered = {part.lower() for part in label.replace("+", " ").split()}
        command_modifiers = {"cmd", "command", "meta", "super"}
        shortcut_modifiers = command_modifiers | {"ctrl", "control"}
        clipboard = "copy" if "c" in lowered and lowered & shortcut_modifiers else None
        if "v" in lowered and lowered & shortcut_modifiers:
            clipboard = "paste"
        if clipboard:
            return {
                "kind": action,
                "point": point,
                "badge": {"type": "glyph", "glyph": clipboard},
                "keys": keys,
            }
        if lowered & command_modifiers:
            command_keys = [key for key in keys if key not in {"Cmd", "Super"}]
            return {
                "kind": action,
                "point": point,
                "badge": {"type": "command", "keys": command_keys},
                "suppress_capsule": True,
            }
        return {"kind": action, "point": point, "badge": {"type": "key", "keys": keys}, "keys": keys}
    if action == "wait":
        return {"kind": action, "point": point, "badge": {"type": "loop"}}
    return None


def _queue_item(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    action = name.lower()
    if action == "scroll":
        direction = str(arguments.get("direction") or "down").lower()
        return {"glyph": "scroll", "rotationDegrees": _SCROLL_ROTATION.get(direction, 0)}
    if action == "type":
        return {"glyph": "type"}
    if action == "wait":
        return {"mark": "loop"}
    if action in {"key", "key_press"}:
        return {"glyph": "key"}
    if action in {"mouse_move", "move"}:
        return {"glyph": "pointer"}
    if action == "drag":
        return {"glyph": "drag"}
    return {"glyph": "click"}


class MacOSPresentationController:
    """Launch and control the optional AppKit/WebKit presentation surface."""

    def __init__(
        self,
        *,
        native_width: int,
        native_height: int,
        cancellation: "CancellationLatch | None" = None,
        prepared: "PreparedMacOSOverlay | None" = None,
        cache_directory: "str | Path | None" = None,
        requested: bool = True,
        show_stop_button: bool = True,
        restore_native_cursor: "Callable[[], Awaitable[str]] | None" = None,
    ) -> None:
        self.native_width = native_width
        self.native_height = native_height
        self.cancellation = cancellation or CancellationLatch()
        self._prepared = prepared
        self._cache_directory = cache_directory
        self._show_stop_button = show_stop_button
        self._restore_native_cursor = restore_native_cursor
        self._status = MacOSPresentationStatus(requested, False, "unavailable", "current")
        self._process: "asyncio.subprocess.Process | None" = None
        self._reader_task: "asyncio.Task[None] | None" = None
        self._stderr_task: "asyncio.Task[None] | None" = None
        self._ready: "asyncio.Future[dict[str, Any]] | None" = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._send_lock = asyncio.Lock()
        self._stopping = False
        self._fatal_error: "MacOSPresentationError | None" = None
        self._viewport: "tuple[int, int] | None" = None
        self._last_render: dict[str, str] = {}
        self._reasoning = ""
        self._action_status = ""
        self._terminal_command = ""
        self._active_keys: "list[str] | None" = None
        self._queue_active = False
        self._batch_is_last = False
        self._capture_id = 0
        self._shell_started_at: dict[str, float] = {}
        self._background: dict[str, ShellPresentationEvent] = {}
        self._background_removals: set[asyncio.Task[None]] = set()
        self._telemetry: list[dict[str, Any]] = []

    @property
    def status(self) -> MacOSPresentationStatus:
        return self._status

    @property
    def telemetry(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(event) for event in self._telemetry)

    @property
    def background_counts(self) -> dict[str, int]:
        counts = {state: 0 for state in ("started", "completed", "failed", "cancelled")}
        for event in self._telemetry:
            if event.get("type") != "background_command":
                continue
            state = event.get("state")
            if state == "running":
                counts["started"] += 1
            elif state == "timed_out":
                counts["failed"] += 1
            elif state in counts:
                counts[state] += 1
        return counts

    async def start(self) -> None:
        if not self._status.requested:
            return
        self._status = replace(self._status, state="starting")
        prepared = self._prepared or load_prepared_macos_overlay(self._cache_directory)
        config = json.dumps({"showStopButton": self._show_stop_button, "enableHotkey": True}, separators=(",", ":"))
        try:
            self._process = await spawn_rpc_subprocess(str(prepared.binary), str(prepared.html), config)
            self._ready = asyncio.get_running_loop().create_future()
            self._reader_task = asyncio.create_task(self._read_host())
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            reply = await asyncio.wait_for(self._ready, timeout=_READY_TIMEOUT_SECONDS)
            capabilities = self._validate_ready(reply)
            self._viewport = (capabilities.viewport_width, capabilities.viewport_height)
            self._status = MacOSPresentationStatus(
                requested=True,
                available=True,
                state="arming",
                cursor="current",
                capabilities=capabilities,
                degradation_reason=None if capabilities.hotkey else "hotkey_unavailable",
            )
            await self._send_operation(
                {
                    "op": "mount",
                    "snapshot": {
                        "cursor": {"x": capabilities.viewport_width / 2, "y": capabilities.viewport_height / 2},
                        "thought": None,
                        "badge": {"type": "loop"},
                        "hidden": False,
                    },
                }
            )
            armed = await self._send_command({"op": "arm"})
            if armed.get("state") != "armed":
                raise MacOSPresentationError("Overlay did not arm.")
            self._status = replace(self._status, state="armed")
        except BaseException:
            await self._terminate_process()
            raise

    async def reveal(self) -> None:
        reply = await self._send_command({"op": "reveal"})
        if reply.get("state") != "visible":
            raise MacOSPresentationError("Overlay did not reveal.")
        self._status = replace(self._status, available=True, state="active", cursor="yutori")

    def blocks_point(self, point: tuple[float, float]) -> bool:
        if not self._status.available:
            return False
        capabilities = self._status.capabilities
        region = capabilities.stop_region if capabilities else None
        if region is None:
            return False
        x, y = point
        left, top, width, height = region
        return left <= x <= left + width and top <= y <= top + height

    def _clear_action_labels(self) -> None:
        """Reset the capsule's action-status, terminal-command, and active-key labels.

        Shared by the ``reasoning``, ``action_done``, and ``final`` branches of
        :meth:`present`, each of which clears these three fields immediately
        before re-rendering the capsule.
        """
        self._action_status = ""
        self._terminal_command = ""
        self._active_keys = None

    async def present(self, event: dict[str, Any]) -> None:
        if not self._status.available or self._stopping:
            return
        try:
            event_type = event.get("type")
            if event_type == "reasoning":
                text = event.get("text")
                if isinstance(text, str) and text.strip():
                    self._reasoning = text.strip()
                    self._clear_action_labels()
                    await self._render_capsule()
            elif event_type in {"action", "batch_member"}:
                await self._present_action(event)
            elif event_type == "action_done":
                if self._batch_is_last:
                    self._queue_active = False
                    self._batch_is_last = False
                self._clear_action_labels()
                await self._render_capsule()
            elif event_type == "final":
                self._reasoning = ""
                self._clear_action_labels()
                await self._render_capsule()
            elif event_type == "shell":
                shell_event = event.get("event")
                if isinstance(shell_event, ShellPresentationEvent):
                    await self._present_shell(shell_event)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - presentation is fail-soft
            await self._degrade(f"presentation_failed:{type(error).__name__}", error)

    async def before_capture(self, capture_id: int) -> bool:
        if not self._status.available:
            return False
        try:
            if capture_id <= self._capture_id:
                raise MacOSPresentationError("Capture IDs must increase monotonically.")
            self._capture_id = capture_id
            reply = await self._send_command({"op": "captureHide", "capture_id": capture_id})
            if reply.get("capture_id") != capture_id or reply.get("state") != "hidden":
                raise MacOSPresentationError("Overlay did not hide for capture.")
            return True
        except Exception as error:  # noqa: BLE001 - presentation is fail-soft
            await self._degrade("capture_hide_failed", error)
            return False

    async def after_capture(self, capture_id: int, width: int, height: int) -> bool:
        if not self._status.available or capture_id != self._capture_id:
            return False
        try:
            self._validate_capture_geometry(width, height)
            reply = await self._send_command({"op": "captureReveal", "capture_id": capture_id})
            if reply.get("capture_id") != capture_id or reply.get("state") != "visible":
                raise MacOSPresentationError("Overlay did not reveal after capture.")
            return True
        except Exception as error:  # noqa: BLE001 - presentation is fail-soft
            await self._degrade("capture_reveal_failed", error)
            return False

    async def encode_observation(self, png_bytes: bytes) -> "tuple[bytes, str] | None":
        if not self._status.available:
            return None
        try:
            reply = await self._send_command(
                {
                    "op": "encode",
                    "data": base64.b64encode(png_bytes).decode("ascii"),
                    "max_long_side": 1920,
                    "quality": 0.8,
                },
                timeout=_ENCODE_TIMEOUT_SECONDS,
            )
            encoded = reply.get("encoded")
            if not isinstance(encoded, dict) or encoded.get("format") not in {"webp", "jpeg"}:
                raise MacOSPresentationError("Overlay observation encoder returned invalid data.")
            data = base64.b64decode(encoded.get("data") or "", validate=True)
            if not data:
                raise MacOSPresentationError("Overlay observation encoder returned empty data.")
            codec = str(encoded["format"])
            self._status = replace(self._status, codec=codec)
            return data, codec
        except Exception as error:  # noqa: BLE001 - Pillow JPEG remains available
            await self._degrade("encoder_failed", error)
            return None

    async def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        await cancel_and_drain(*self._background_removals)
        if self._process is not None and self._process.returncode is None and self._fatal_error is None:
            try:
                await self._send_operation({"op": "destroy"}, allow_stopping=True)
                await self._send_command({"op": "retire"}, allow_stopping=True)
            except Exception:
                pass
        await self._terminate_process()
        self._status = replace(self._status, available=False, state="retired")

    async def _read_host(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                try:
                    reply = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    await self._degrade("malformed_host_reply", error)
                    return
                if not isinstance(reply, dict):
                    await self._degrade("malformed_host_reply")
                    return
                if reply.get("event") == "stop":
                    self.cancellation.request("operator_stop")
                    continue
                if reply.get("ready") is True and self._ready is not None and not self._ready.done():
                    self._ready.set_result(reply)
                    continue
                reply_id = reply.get("id")
                if isinstance(reply_id, int) and reply_id in self._pending:
                    future = self._pending.pop(reply_id)
                    if future.done():
                        continue
                    if reply.get("ok") is True:
                        future.set_result(reply)
                    else:
                        future.set_exception(
                            MacOSPresentationError(str(reply.get("error") or "Overlay operation failed."))
                        )
                    continue
                if reply.get("error"):
                    await self._degrade("webkit_failure", MacOSPresentationError(str(reply["error"])))
                    return
        except asyncio.CancelledError:
            return
        except Exception as error:  # noqa: BLE001 - classified as host failure
            await self._degrade("host_read_failed", error)
            return
        if not self._stopping:
            await self._degrade("host_exited")

    async def _drain_stderr(self) -> None:
        assert self._process is not None
        await drain_stream(self._process.stderr)

    async def _send_envelope(
        self,
        envelope: dict[str, Any],
        *,
        timeout: float = _OPERATION_TIMEOUT_SECONDS,
        allow_stopping: bool = False,
    ) -> dict[str, Any]:
        if self._fatal_error is not None:
            raise self._fatal_error
        if self._stopping and not allow_stopping:
            raise MacOSPresentationError("Overlay is stopping.")
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise MacOSPresentationError("Overlay host is not running.")
        async with self._send_lock:
            message_id = self._next_id
            self._next_id += 1
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self._pending[message_id] = future
            try:
                process.stdin.write(json.dumps({"id": message_id, **envelope}, separators=(",", ":")).encode() + b"\n")
                await process.stdin.drain()
            except Exception:
                self._pending.pop(message_id, None)
                raise
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError as error:
            raise MacOSPresentationError("Overlay operation timed out.") from error
        finally:
            self._pending.pop(message_id, None)
            if not future.done():
                future.cancel()

    async def _send_operation(self, operation: dict[str, Any], *, allow_stopping: bool = False) -> dict[str, Any]:
        signature = json.dumps(operation, separators=(",", ":"), sort_keys=True)
        dedupe_key = str(operation.get("op"))
        if dedupe_key in {"showThought", "clearThought", "previewAction", "moveCursor"}:
            if self._last_render.get(dedupe_key) == signature:
                return {"ok": True}
            self._last_render[dedupe_key] = signature
        return await self._send_envelope({"operation": operation}, allow_stopping=allow_stopping)

    async def _send_command(
        self,
        command: dict[str, Any],
        *,
        timeout: float = _OPERATION_TIMEOUT_SECONDS,
        allow_stopping: bool = False,
    ) -> dict[str, Any]:
        return await self._send_envelope({"command": command}, timeout=timeout, allow_stopping=allow_stopping)

    async def _render_capsule(self) -> None:
        if self._queue_active:
            return
        text = " · ".join(
            part
            for part in (
                self._action_status,
                f"$ {self._terminal_command}" if self._terminal_command else "",
                self._reasoning,
            )
            if part
        )
        if not text and not self._active_keys:
            await self._send_operation({"op": "clearThought"})
            return
        operation: dict[str, Any] = {"op": "showThought", "markdown": text}
        if self._active_keys:
            operation["keys"] = self._active_keys
        await self._send_operation(operation)

    async def _present_action(self, event: dict[str, Any]) -> None:
        name = str(event.get("name") or "")
        arguments = event.get("arguments")
        if not isinstance(arguments, dict):
            return
        visual = _action_visual(name, arguments)
        if visual is None:
            return
        batch = event.get("batch") if isinstance(event.get("batch"), dict) else None
        queue = None
        if batch and isinstance(batch.get("members"), list) and len(batch["members"]) > 1:
            items = []
            for member in batch["members"]:
                if not isinstance(member, dict):
                    continue
                member_arguments = member.get("arguments") if isinstance(member.get("arguments"), dict) else {}
                items.append(_queue_item(str(member.get("name") or ""), member_arguments))
            queue = {
                "key": f"batch-{batch.get('id')}",
                "items": items,
                "completedCount": int(batch.get("index") or 0),
            }
        self._queue_active = queue is not None
        self._batch_is_last = bool(
            batch
            and isinstance(batch.get("members"), list)
            and int(batch.get("index") or 0) == len(batch["members"]) - 1
        )
        status = _ACTION_STATUS.get(name.lower(), "Working")
        if batch:
            status = f"{int(batch.get('index') or 0) + 1} of {len(batch.get('members') or [])} · {status}"
        self._action_status = status
        self._terminal_command = ""
        self._active_keys = visual.get("keys")

        viewport = self._viewport
        if viewport is None:
            return

        def viewport_point(point: tuple[float, float]) -> dict[str, float]:
            return {"x": point[0] / _NORMALIZED_SCALE * viewport[0], "y": point[1] / _NORMALIZED_SCALE * viewport[1]}

        point = visual.get("point")
        if point:
            await self._send_operation({"op": "moveCursor", "point": viewport_point(point)})
        if visual.get("suppress_capsule"):
            await self._send_operation({"op": "clearThought"})
        elif not queue:
            await self._render_capsule()
        presentation = {"badge": visual["badge"], "queue": queue, "transientEffects": []}
        await self._send_operation({"op": "previewAction", "presentation": presentation})

        if point:
            if not await self._lead():
                return
            if visual.get("clicks"):
                await self._send_command({"op": "pulse", "point": viewport_point(point)})
            if visual.get("to"):
                destination = viewport_point(visual["to"])
                presentation["transientEffects"] = [
                    {
                        "id": f"python-drag-{self._next_id}",
                        "type": "drag-trail",
                        "from": viewport_point(point),
                        "to": destination,
                        "startedAtMs": round(time.time() * 1000),
                        "durationMs": 200,
                    }
                ]
                await self._send_operation({"op": "previewAction", "presentation": presentation})
                await self._send_operation({"op": "moveCursor", "point": destination})

    async def _present_shell(self, event: ShellPresentationEvent) -> None:
        self._telemetry.append(
            {
                "type": "background_command" if event.run_in_background else "shell_command",
                **asdict(event),
            }
        )
        if event.run_in_background:
            self._background[event.task_id] = event
            await self._render_background_rail()
            if event.state in {"completed", "failed", "timed_out", "cancelled"}:
                task = asyncio.create_task(self._remove_background_after_hold(event.task_id, event))
                self._background_removals.add(task)
                task.add_done_callback(self._background_removals.discard)
            return

        if event.state in {"starting", "running"}:
            self._shell_started_at.setdefault(event.task_id, time.monotonic())
            self._action_status = "Run command"
            self._terminal_command = event.command
            self._active_keys = None
            await self._render_capsule()
            await self._send_operation(
                {
                    "op": "previewAction",
                    "presentation": {"badge": {"type": "loop"}, "queue": None, "transientEffects": []},
                }
            )
            return

        started_at = self._shell_started_at.pop(event.task_id, time.monotonic())
        remaining = _SHELL_MINIMUM_DWELL_SECONDS - (time.monotonic() - started_at)
        if remaining > 0 and not await self._sleep(remaining):
            return
        labels = {
            "completed": "Command completed",
            "failed": "Command failed",
            "timed_out": "Command timed out",
            "cancelled": "Command cancelled",
        }
        self._action_status = labels.get(event.state, "Command finished")
        if event.exit_code is not None:
            self._action_status = f"{self._action_status} · exit {event.exit_code}"
        self._terminal_command = ""
        await self._render_capsule()
        await self._sleep(_SHELL_TERMINAL_HOLD_SECONDS)
        self._action_status = ""
        self._terminal_command = ""
        await self._render_capsule()

    async def _render_background_rail(self) -> None:
        events = list(self._background.values())
        tasks = [asdict(event) for event in events[:3]]
        signature = json.dumps([tasks, max(0, len(events) - 3)], separators=(",", ":"), sort_keys=True)
        if self._last_render.get("backgroundTasks") == signature:
            return
        self._last_render["backgroundTasks"] = signature
        await self._send_command({"op": "backgroundTasks", "tasks": tasks, "overflow": max(0, len(events) - 3)})

    async def _remove_background_after_hold(self, task_id: str, terminal: ShellPresentationEvent) -> None:
        if not await self._sleep(_BACKGROUND_TERMINAL_HOLD_SECONDS):
            return
        if self._background.get(task_id) == terminal:
            self._background.pop(task_id, None)
            await self._render_background_rail()

    async def _lead(self) -> bool:
        return await self._sleep(_OVERLAY_LEAD_SECONDS)

    async def _sleep(self, seconds: float) -> bool:
        if self.cancellation.cancelled:
            return False
        sleeper = asyncio.create_task(asyncio.sleep(seconds))
        cancelled = asyncio.create_task(self.cancellation.wait())
        done, pending = await asyncio.wait({sleeper, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        await cancel_and_drain(*pending)
        return sleeper in done

    def _validate_ready(self, reply: dict[str, Any]) -> MacOSPresentationCapabilities:
        if reply.get("protocol_version") != OVERLAY_PROTOCOL_VERSION:
            raise MacOSPresentationError("Overlay returned an incompatible protocol version.")
        width, height, scale = reply.get("width"), reply.get("height"), reply.get("backing_scale")
        stop_region = _valid_stop_region(reply.get("stop_region"))
        if not _positive_finite(width) or not _positive_finite(height) or not _positive_finite(scale):
            raise MacOSPresentationError("Overlay returned invalid viewport capabilities.")
        if self._show_stop_button and stop_region is None:
            raise MacOSPresentationError("Overlay returned an invalid Stop region.")
        capabilities = MacOSPresentationCapabilities(
            protocol_version=OVERLAY_PROTOCOL_VERSION,
            viewport_width=round(width),
            viewport_height=round(height),
            backing_scale=float(scale),
            hotkey=reply.get("hotkey") is True,
            stop_region=stop_region,
        )
        self._validate_geometry(capabilities, self.native_width, self.native_height)
        return capabilities

    def _validate_capture_geometry(self, width: int, height: int) -> None:
        capabilities = self._status.capabilities
        if capabilities is None:
            raise MacOSPresentationError("Overlay capabilities are unavailable.")
        self._validate_geometry(capabilities, width, height)

    @staticmethod
    def _validate_geometry(capabilities: MacOSPresentationCapabilities, width: int, height: int) -> None:
        point_aspect = capabilities.viewport_width / capabilities.viewport_height
        pixel_aspect = width / height
        x_scale = width / capabilities.viewport_width
        y_scale = height / capabilities.viewport_height
        if (
            abs(point_aspect - pixel_aspect) / point_aspect > 0.01
            or abs(x_scale - y_scale) > 0.05
            or x_scale < 1
            or x_scale > 4
        ):
            raise MacOSPresentationError("Overlay display geometry does not match the captured desktop.")
        if abs(x_scale - capabilities.backing_scale) > 0.15:
            raise MacOSPresentationError("Overlay Retina scale does not match the captured desktop.")

    async def _degrade(self, reason: str, error: "BaseException | None" = None) -> None:
        if self._fatal_error is not None or self._stopping:
            return
        self._fatal_error = MacOSPresentationError(str(error) if error else reason)
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(self._fatal_error)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(self._fatal_error)
        self._pending.clear()
        fallback = self._status.fallback
        cursor = self._status.cursor
        if self._restore_native_cursor is not None:
            try:
                fallback = await self._restore_native_cursor()
                cursor = fallback
            except Exception:
                fallback = "cursorless"
                cursor = "cursorless"
        self._status = replace(
            self._status,
            available=False,
            state="degraded",
            cursor=cursor,
            degradation_reason=reason,
            fallback=fallback,
        )
        self._telemetry.append(
            {"type": "presentation_degraded", "reason": reason, "error_type": type(error).__name__ if error else None}
        )
        await self._terminate_process()

    async def _terminate_process(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            await terminate_process_gracefully(process, exit_timeout=_PROCESS_EXIT_TIMEOUT_SECONDS, kill_timeout=0.5)
        current = asyncio.current_task()
        tasks = [task for task in (self._reader_task, self._stderr_task) if task is not None and task is not current]
        await cancel_and_drain(*tasks)
        self._reader_task = None
        self._stderr_task = None
