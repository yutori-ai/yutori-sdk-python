"""Python controller for the bundled protocol-v2 macOS presentation host."""

from __future__ import annotations

import asyncio
import base64
import functools
import io
import json
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, TypeVar

from PIL import Image

from .overlay_build import OVERLAY_PROTOCOL_VERSION, PreparedMacOSOverlay, load_prepared_macos_overlay
from .process_lifecycle import (
    cancel_and_drain,
    drain_stream,
    race_sleep_against_cancellation,
    spawn_rpc_subprocess,
    terminate_process_gracefully,
)
from .types import (
    CancellationLatch,
    MacOSPresentationCapabilities,
    MacOSPresentationStatus,
    ShellPresentationEvent,
)

_READY_TIMEOUT_SECONDS = 15
_OPERATION_TIMEOUT_SECONDS = 5
_ENCODE_TIMEOUT_SECONDS = 30
# The host's own desktop capture: ScreenCaptureKit plus a PNG encode of a full Retina frame.
_CAPTURE_TIMEOUT_SECONDS = 15
_PROCESS_EXIT_TIMEOUT_SECONDS = 1
_OVERLAY_LEAD_SECONDS = 0.15
_SHELL_MINIMUM_DWELL_SECONDS = 0.9
_SHELL_TERMINAL_HOLD_SECONDS = 0.9
# A finished command stays on screen long enough to read on whichever surface carried
# it -- the run-command card by the cursor in a foreground run, the rail under the menu
# bar otherwise. The capsule's 0.9s hold is tuned for a glance at a status word, not for
# an operator checking what just ran on their Mac.
_SHELL_COMMAND_TERMINAL_HOLD_SECONDS = 4.0
_SHELL_RAIL_ROWS = 3
_SHELL_TERMINAL_STATES = frozenset({"completed", "failed", "timed_out", "cancelled"})
_NORMALIZED_SCALE = 1000
# Capture exclusion: the host's probe is a 4x4 checkerboard; this many of its cells in the frame
# means the probe was captured, so this macOS ignores `sharingType = .none`. See _probe_verdict.
_PROBE_MATCH_THRESHOLD = 4

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


_T = TypeVar("_T")


def _fail_soft(reason: str, default: _T) -> Callable[[Callable[..., Awaitable[_T]]], Callable[..., Awaitable[_T]]]:
    """Decorate a controller method: on any exception, degrade with ``reason`` and return ``default``.

    Consolidates the identical ``try: ...; return X`` / ``except Exception: await
    self._degrade(reason, error); return default`` shape shared by
    ``show_thumbnail``, ``before_capture``, ``after_capture``, and
    ``encode_observation``. Each of those is a single self-contained
    presentation operation whose only failure handling is "degrade and report
    failure to the caller" -- unlike ``present``/``_present_status``, which
    interleave several branches and must re-raise ``asyncio.CancelledError``
    before degrading, so those use :func:`_fail_soft_cancellable` instead.
    """

    def decorator(func: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
        @functools.wraps(func)
        async def wrapper(self: "MacOSPresentationController", *args: Any, **kwargs: Any) -> _T:
            try:
                return await func(self, *args, **kwargs)
            except Exception as error:  # noqa: BLE001 - presentation is fail-soft
                await self._degrade(reason, error)
                return default

        return wrapper

    return decorator


def _fail_soft_cancellable(func: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
    """Decorate a controller method: reraise ``asyncio.CancelledError``, else degrade and swallow.

    Consolidates the identical ``except asyncio.CancelledError: raise`` / ``except
    Exception as error: await self._degrade(f"presentation_failed:{type(error).__name__}",
    error)`` shape shared by ``present`` and ``_present_status``. Unlike :func:`_fail_soft`,
    these interleave several branches across a whole event dispatch and must let
    cancellation propagate rather than being reported as a presentation failure.
    """

    @functools.wraps(func)
    async def wrapper(self: "MacOSPresentationController", *args: Any, **kwargs: Any) -> None:
        try:
            await func(self, *args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - presentation is fail-soft
            await self._degrade(f"presentation_failed:{type(error).__name__}", error)

    return wrapper


class MacOSPresentationError(RuntimeError):
    pass


def _positive_finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _valid_region(value: Any) -> "tuple[float, float, float, float] | None":
    """A host region in the overlay's normalized 0-1000 space: the Stop item, the activity grip."""
    if not isinstance(value, dict):
        return None
    fields = tuple(value.get(key) for key in ("x", "y", "width", "height"))
    if not all(isinstance(field, (int, float)) and math.isfinite(field) for field in fields):
        return None
    x, y, width, height = (float(field) for field in fields)
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1000 or y + height > 1000:
        return None
    return x, y, width, height


def _valid_probe(value: Any) -> "dict[str, float] | None":
    """The host's probe frame: top-left origin in overlay page points, plus its cells per side."""
    if not isinstance(value, dict):
        return None
    x, y, size, cells = value.get("x"), value.get("y"), value.get("size"), value.get("cells")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y, size)):
        return None
    if x < 0 or y < 0 or size <= 0 or not isinstance(cells, int) or isinstance(cells, bool) or cells < 2:
        return None
    return {"x": float(x), "y": float(y), "size": float(size), "cells": float(cells)}


def _probe_verdict(png_bytes: bytes, probe: dict[str, float], scale: "tuple[float, float]") -> "tuple[str, int]":
    """Say whether the capture-exclusion probe is in the frame: ("leaked" | "excluded", cells matched).

    The probe is a checkerboard of saturated magenta (top-left cell) and green; the centre of every
    cell is sampled and compared with the pattern, with room for the display's colour profile.
    Matching :data:`_PROBE_MATCH_THRESHOLD` cells or more counts as the probe being captured: chance
    content in exactly that arrangement is far less likely than a partly covered probe, and a wrong
    "excluded" is the costlier mistake, since it puts Yutori's drawing in every frame the model sees.
    """
    cells = int(probe["cells"])
    cell = probe["size"] / cells
    x_scale, y_scale = scale
    matches = 0
    with Image.open(io.BytesIO(png_bytes)) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        for row in range(cells):
            for column in range(cells):
                px = round((probe["x"] + (column + 0.5) * cell) * x_scale)
                py = round((probe["y"] + (row + 0.5) * cell) * y_scale)
                if not (0 <= px < width and 0 <= py < height):
                    continue
                r, g, b = rgb.getpixel((px, py))[:3]
                if (row + column) % 2 == 0:
                    matches += r > 170 and b > 170 and g < 120
                else:
                    matches += g > 170 and r < 130 and b < 130
    return ("leaked" if matches >= _PROBE_MATCH_THRESHOLD else "excluded"), matches


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


_STATUS_LINE_MAX_CHARACTERS = 90
# The activity window scrolls, so an entry is not truncated to a glance the way the
# menu caption is; the cap only keeps a runaway reasoning block out of the host.
_TRANSCRIPT_TEXT_MAX_CHARACTERS = 4000


_ACTION_ICONS = {
    "left_click": "click",
    "click": "click",
    "double_click": "click",
    "right_click": "click",
    "middle_click": "click",
    "triple_click": "click",
    "mouse_move": "move",
    "move": "move",
    "drag": "drag",
    "scroll": "scroll",
    "type": "type",
    "key_press": "key",
    "key": "key",
    "wait": "wait",
}


def _action_text(event: dict[str, Any]) -> str:
    """One line naming an action and where it lands: "left click at (100, 20)"."""
    name = str(event.get("name") or "action").replace("_", " ")
    arguments = event.get("arguments") if isinstance(event.get("arguments"), dict) else {}
    point = _point(arguments, "coordinates", "coordinate", "start_coordinate")
    if point is not None:
        return f"{name} at ({round(point[0])}, {round(point[1])})"
    text = arguments.get("text") or arguments.get("key") or arguments.get("key_comb")
    return f"{name} {text}" if isinstance(text, str) and text.strip() else name


def _status_line(event: dict[str, Any]) -> "str | None":
    """The one-line caption a status-mode menu shows for a presentation event."""
    event_type = event.get("type")
    text: "str | None" = None
    if event_type == "task":
        value = event.get("text")
        text = f"Task: {value.strip()}" if isinstance(value, str) and value.strip() else None
    elif event_type == "status":
        value = event.get("text")
        text = value.strip() if isinstance(value, str) and value.strip() else None
    elif event_type == "reasoning":
        value = event.get("text")
        text = f"Thinking: {value.strip()}" if isinstance(value, str) and value.strip() else None
    elif event_type in {"action", "batch_member"}:
        text = _action_text(event)
    elif event_type == "final":
        text = "Finished"
    elif event_type == "shell":
        shell_event = event.get("event")
        if isinstance(shell_event, ShellPresentationEvent):
            text = f"Shell ({shell_event.state}): {shell_event.command}"
    if text is None:
        return None
    if len(text) > _STATUS_LINE_MAX_CHARACTERS:
        text = text[: _STATUS_LINE_MAX_CHARACTERS - 1] + "\u2026"
    return text


def _clamp(value: Any) -> "str | None":
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if len(text) <= _TRANSCRIPT_TEXT_MAX_CHARACTERS:
        return text
    return text[: _TRANSCRIPT_TEXT_MAX_CHARACTERS - 1] + "\u2026"


def _transcript_entry(event: dict[str, Any], sequence: int) -> "dict[str, Any] | None":
    """One activity-window row for a presentation event, or None when it has nothing to show.

    Shell entries are keyed by task id rather than by sequence, so a command is revised in
    place as it moves from running to its exit code instead of stacking a card per lifecycle
    event -- the same identity the desktop shell rail keys on.
    """
    event_type = event.get("type")
    if event_type == "shell":
        shell_event = event.get("event")
        if not isinstance(shell_event, ShellPresentationEvent):
            return None
        return {
            "id": f"shell-{shell_event.task_id}",
            "kind": "shell",
            "command": shell_event.command,
            "state": shell_event.state,
            "exitCode": shell_event.exit_code,
            "background": shell_event.run_in_background,
        }
    identity = f"entry-{sequence}"
    if event_type == "task":
        text = _clamp(event.get("text"))
        return {"id": identity, "kind": "task", "text": text} if text else None
    if event_type == "reasoning":
        text = _clamp(event.get("text"))
        return {"id": identity, "kind": "thinking", "text": text} if text else None
    if event_type in {"action", "batch_member"}:
        name = str(event.get("name") or "").lower()
        return {
            "id": identity,
            "kind": "action",
            "text": _action_text(event),
            "icon": _ACTION_ICONS.get(name, "click"),
        }
    if event_type == "action_done":
        # A step that failed while the run carried on: a refused click, a tool error.
        error = _clamp(event.get("error"))
        if error is not None:
            return {"id": identity, "kind": "error", "text": error}
        if event.get("refused") is True:
            return {"id": identity, "kind": "error", "text": "The action was refused."}
        return None
    if event_type == "final":
        text = _clamp(event.get("text"))
        return {"id": identity, "kind": "final", "text": text} if text else None
    if event_type == "status":
        text = _clamp(event.get("text"))
        return {"id": identity, "kind": "system", "text": text} if text else None
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
        mode: str = "overlay",
        title: "str | None" = None,
        exclude_from_capture: bool = True,
    ) -> None:
        if mode not in {"overlay", "status"}:
            raise ValueError("mode must be 'overlay' or 'status'")
        self.native_width = native_width
        self.native_height = native_height
        self.cancellation = cancellation or CancellationLatch()
        self._prepared = prepared
        self._cache_directory = cache_directory
        self._show_stop_button = show_stop_button
        # "status": a menu bar item with the latest frame and Stop, the shell rail, and the
        # activity window's transcript -- no full-screen page. Used for window-scope runs, where
        # the model's frame is one window and the user keeps working next to it.
        self._mode = mode
        self._title = title
        # Two ways to keep Yutori's drawing out of the model's frame without hiding it. True: the
        # panels opt out of screen capture (`sharingType = .none`) and the frames come from the
        # driver. False: the panels stay capturable, so screen recordings and screen shares of the
        # run show them, and the desktop frames come from the host instead, which leaves its own
        # windows out on the capturer's side (`capture_source == "overlay"`). Either way
        # `verify_capture_exclusion` checks the mechanism on this Mac with a probe; until it says
        # "excluded", every capture hides the overlay first (the old path).
        self._exclude_from_capture = exclude_from_capture
        self._capture_exclusion = "unverified"
        self._capture_source = "driver"
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
        self._reset_terminal()
        self._active_keys: "list[str] | None" = None
        self._queue_active = False
        self._batch_is_last = False
        self._capture_id = 0
        self._shell_started_at: dict[str, float] = {}
        self._transcript_sequence = 0
        self._shell_rail: dict[str, ShellPresentationEvent] = {}
        self._shell_rail_removals: set[asyncio.Task[None]] = set()
        self._telemetry: list[dict[str, Any]] = []
        # Status mode: the host reports whether anyone is looking (menu open or activity window shown);
        # the owner streams preview frames only while that is true.
        self._preview_demand = False
        self.on_preview_demand: "Callable[[bool], None] | None" = None
        # The activity window's grip -- the one part of it that takes the mouse -- while the
        # window is shown, in the same 0-1000 space as the Stop region. The host reports it on
        # show, hide, and every move, so a model click on it is refused rather than swallowed.
        self._activity_grip_region: "tuple[float, float, float, float] | None" = None

    @property
    def status(self) -> MacOSPresentationStatus:
        return self._status

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def capture_exclusion(self) -> str:
        """How Yutori's drawing stays out of the frames the model sees.

        ``"excluded"``: the mechanism (the panels' capture opt-out, or the host's filtered capture
        when the overlay is recordable) was verified on this Mac, so nothing is hidden for a
        capture. ``"leaked"``: the probe showed up, so every capture hides the overlay first.
        ``"unverified"`` (probe not run yet) and ``"unverifiable"`` (the probe or the host's
        capture failed) also hide the overlay for every capture.
        """
        return self._capture_exclusion

    @property
    def capture_source(self) -> str:
        """Where the model's desktop frames come from.

        ``"driver"``: the driver's desktop capture. ``"overlay"``: the host's own capture with
        its windows left out by the capturer, so the overlay stays in screen recordings and
        screen shares of the run; set once ``exclude_from_capture=False`` passes the probe, and
        handed back to the driver (with hiding) if a capture ever fails.
        """
        return self._capture_source

    @property
    def preview_demand(self) -> bool:
        return self._preview_demand

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
        settings: dict[str, Any] = {
            "showStopButton": self._show_stop_button,
            "enableHotkey": True,
            "mode": self._mode,
            "excludeFromCapture": self._exclude_from_capture,
        }
        if self._title is not None:
            settings["title"] = self._title
        # The activity window's page. Both modes show the conversation with the model; only a
        # window-scope run also has frames of its own to put above it.
        settings["activityHtml"] = str(prepared.activity_html)
        config = json.dumps(settings, separators=(",", ":"))
        try:
            self._process = await spawn_rpc_subprocess(str(prepared.binary), str(prepared.html), config)
            self._ready = asyncio.get_running_loop().create_future()
            self._reader_task = asyncio.create_task(self._read_host())
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            reply = await asyncio.wait_for(self._ready, timeout=_READY_TIMEOUT_SECONDS)
            capabilities = self._validate_status_ready(reply) if self._mode == "status" else self._validate_ready(reply)
            self._viewport = (capabilities.viewport_width, capabilities.viewport_height)
            self._status = MacOSPresentationStatus(
                requested=True,
                available=True,
                state="arming",
                cursor="current",
                capabilities=capabilities,
                degradation_reason=None if capabilities.hotkey else "hotkey_unavailable",
            )
            if self._mode == "overlay":
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
            await self._send_command_expecting({"op": "arm"}, "armed", "Overlay did not arm.")
            self._status = replace(self._status, state="armed")
        except BaseException:
            await self._terminate_process()
            raise

    async def reveal(self) -> None:
        await self._send_command_expecting({"op": "reveal"}, "visible", "Overlay did not reveal.")
        cursor = "hidden" if self._mode == "status" else "yutori"
        self._status = replace(self._status, available=True, state="active", cursor=cursor)

    @_fail_soft("thumbnail_failed", False)
    async def show_thumbnail(self, image_bytes: bytes, *, caption: "str | None" = None) -> bool:
        """Status mode: put the latest frame of the driven window in the menu bar item's menu."""
        if self._mode != "status" or not self._status.available or self._stopping:
            return False
        command: dict[str, Any] = {"op": "thumbnail", "data": base64.b64encode(image_bytes).decode("ascii")}
        if caption is not None:
            command["caption"] = caption
        await self._send_command_expecting(command, "shown", "Status item did not show the thumbnail.")
        if caption is not None:
            self._last_render["status"] = caption
        return True

    def blocking_surface(self, point: tuple[float, float]) -> "str | None":
        """Which Yutori control a model input at this point (0-1000 space) would land on.

        ``"stop"`` for the menu bar Stop item, ``"activity"`` for the activity window's grip,
        None when the point reaches the desktop. The activity window's body ignores the mouse,
        so only its grip can swallow a click.
        """
        if not self._status.available:
            return None
        capabilities = self._status.capabilities
        stop_region = capabilities.stop_region if capabilities else None
        x, y = point
        for name, region in (("stop", stop_region), ("activity", self._activity_grip_region)):
            if region is None:
                continue
            left, top, width, height = region
            if left <= x <= left + width and top <= y <= top + height:
                return name
        return None

    def blocks_point(self, point: tuple[float, float]) -> bool:
        return self.blocking_surface(point) is not None

    def _clear_action_labels(self) -> None:
        """Reset the capsule's action-status and active-key labels and the run-command card.

        Shared by the ``reasoning``, ``action_done``, ``request``, and ``final`` branches of
        :meth:`present`, each of which clears all three immediately before re-rendering the
        capsule -- and that re-render is what takes a finished card off the screen.
        """
        self._action_status = ""
        self._active_keys = None
        self._reset_terminal()

    def _reset_terminal(self) -> None:
        """Unstage the run-command card so the next render falls back to the capsule."""
        self._terminal_command = ""
        self._terminal_running = False
        self._terminal_failed = False
        self._terminal_output = ""
        self._terminal_task_id = ""

    async def _clear_reasoning_and_render(self) -> None:
        """Drop any stale reasoning/action labels and re-render the capsule.

        Shared by the ``request`` branch of :meth:`present` (clearing a completed step's
        reasoning before the next model call) and the ``final`` branch (clearing it once the
        run ends) -- both reset the capsule to the same idle state.
        """
        self._reasoning = ""
        self._clear_action_labels()
        await self._render_capsule()

    @_fail_soft_cancellable
    async def present(self, event: dict[str, Any]) -> None:
        if not self._status.available or self._stopping:
            return
        if self._mode == "status":
            await self._present_status(event)
            return
        # The transcript is the same conversation in either mode; only the desktop
        # surfaces below differ.
        await self._present_transcript(event)
        event_type = event.get("type")
        if event_type == "reasoning":
            text = event.get("text")
            if isinstance(text, str) and text.strip():
                self._reasoning = text.strip()
                self._clear_action_labels()
                await self._render_capsule()
        elif event_type == "request":
            await self._clear_reasoning_and_render()
        elif event_type in {"action", "batch_member"}:
            await self._present_action(event)
        elif event_type == "action_done":
            if self._batch_is_last:
                self._queue_active = False
                self._batch_is_last = False
            self._clear_action_labels()
            await self._render_capsule()
        elif event_type == "final":
            await self._clear_reasoning_and_render()
        elif event_type == "shell":
            shell_event = event.get("event")
            if isinstance(shell_event, ShellPresentationEvent):
                await self._present_shell(shell_event)

    async def show_preview_frame(self, image_bytes: bytes) -> bool:
        """Status mode: refresh the live frame (menu thumbnail and activity window) with a streamed frame.

        Unlike ``show_thumbnail`` this never degrades the status item: a dropped preview frame
        costs nothing, and the host reader degrades on its own when the host is gone.
        """
        if self._mode != "status" or not self._status.available or self._stopping:
            return False
        try:
            reply = await self._send_command(
                {"op": "previewFrame", "data": base64.b64encode(image_bytes).decode("ascii")}
            )
        except (MacOSPresentationError, asyncio.TimeoutError):
            return False
        return reply.get("state") == "shown"

    @_fail_soft_cancellable
    async def _present_status(self, event: dict[str, Any]) -> None:
        """Status mode: the menu's caption, the activity window's transcript, and the shell rail.

        A background run draws no cursor capsule and owns no desktop, so this is everything
        the operator can see: a glanceable line in the menu, the same step as a row in the
        activity window's conversation, and -- for a shell command -- the same click-through
        rail under the menu bar a foreground run shows, because a command running on their
        Mac should not require opening a window to notice.
        """
        if event.get("type") == "shell":
            shell_event = event.get("event")
            if isinstance(shell_event, ShellPresentationEvent):
                self._record_shell(shell_event)
                await self._track_shell_rail(shell_event)
        await self._present_transcript(event)
        text = _status_line(event)
        if text is not None and self._last_render.get("status") != text:
            self._last_render["status"] = text
            await self._send_command_expecting(
                {"op": "status", "text": text}, "shown", "Status item did not accept the caption."
            )

    async def _present_transcript(self, event: dict[str, Any]) -> None:
        """Append this event to the activity window's conversation, if it has a row to show."""
        entry = _transcript_entry(event, self._transcript_sequence)
        if entry is None:
            return
        self._transcript_sequence += 1
        await self._send_command({"op": "transcript", "entry": entry})

    async def verify_capture_exclusion(self, capture: "Callable[[], Awaitable[tuple[bytes, int, int]]]") -> str:
        """Check on this Mac that the overlay's panels stay out of a desktop capture.

        Shows the host's probe (a small checkerboard in a panel treated like the overlay), takes one
        frame, and looks for the pattern. With ``exclude_from_capture=True`` the frame comes through
        ``capture`` (the driver) and the probe opts out of capture like the panels; with ``False``
        it comes from the host's own filtered capture, which then serves every later frame. Absent,
        the overlay is left on screen for every later capture; present, or if anything goes wrong,
        each capture keeps hiding the overlay first. Advisory: never degrades the presentation.
        """
        if not self._status.available or self._mode == "status" or self._stopping:
            return self._capture_exclusion
        mechanism = "sharing" if self._exclude_from_capture else "filter"
        if not self._exclude_from_capture:
            capture = self._capture_desktop_frame
        state, matches, error_type = "unverifiable", None, None
        try:
            reply = await self._send_command({"op": "captureProbe", "phase": "show"})
            try:
                probe = _valid_probe(reply.get("probe"))
                if reply.get("state") != "shown" or probe is None:
                    raise MacOSPresentationError("Overlay did not show the capture probe.")
                png_bytes, width, height = await capture()
            finally:
                # Whatever the host answered, the probe panel must not outlive the check.
                await self._send_command({"op": "captureProbe", "phase": "hide"})
            scale = self._validate_capture_geometry(width, height)
            state, matches = _probe_verdict(png_bytes, probe, scale)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - the probe is advisory; hiding stays the default
            error_type = type(error).__name__
        self._capture_exclusion = state
        if mechanism == "filter" and state == "excluded":
            self._capture_source = "overlay"
        self._telemetry.append(
            {
                "type": "capture_exclusion",
                "mechanism": mechanism,
                "state": state,
                "matches": matches,
                "error_type": error_type,
            }
        )
        return state

    async def capture_desktop(self) -> "tuple[bytes, int, int] | None":
        """The model's desktop frame from the host, or ``None`` when the driver has to take it.

        Only while :attr:`capture_source` is ``"overlay"``. A failure hands the frames back to the
        driver for the rest of the run, with the overlay hidden around each, rather than degrading
        the presentation: the overlay keeps painting, and the run keeps going.
        """
        if self._capture_source != "overlay" or not self._status.available or self._stopping:
            return None
        try:
            return await self._capture_desktop_frame()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - the driver's capture (with hiding) is the fallback
            self._capture_source = "driver"
            self._capture_exclusion = "unverifiable"
            self._telemetry.append({"type": "capture_source", "source": "driver", "error_type": type(error).__name__})
            return None

    async def _capture_desktop_frame(self) -> tuple[bytes, int, int]:
        """One desktop frame from the host, checked to be the same shape as the driver's."""
        reply = await self._send_command({"op": "captureDesktop"}, timeout=_CAPTURE_TIMEOUT_SECONDS)
        frame = reply.get("frame")
        if not isinstance(frame, dict):
            raise MacOSPresentationError("Overlay desktop capture returned no frame.")
        png_bytes = base64.b64decode(frame.get("data") or "", validate=True)
        if not png_bytes:
            raise MacOSPresentationError("Overlay desktop capture returned empty data.")
        with Image.open(io.BytesIO(png_bytes)) as image:
            width, height = image.size
        if (width, height) != (frame.get("width"), frame.get("height")):
            raise MacOSPresentationError("Overlay desktop capture reported a different size than its frame.")
        if (width, height) != (self.native_width, self.native_height):
            raise MacOSPresentationError(
                f"Overlay desktop capture is {width}x{height}; the driver's frame is "
                f"{self.native_width}x{self.native_height}."
            )
        return png_bytes, width, height

    @_fail_soft("capture_hide_failed", False)
    async def before_capture(self, capture_id: int) -> bool:
        if not self._status.available or self._mode == "status" or self._capture_exclusion == "excluded":
            return False
        if capture_id <= self._capture_id:
            raise MacOSPresentationError("Capture IDs must increase monotonically.")
        self._capture_id = capture_id
        await self._send_command_expecting(
            {"op": "captureHide", "capture_id": capture_id},
            "hidden",
            "Overlay did not hide for capture.",
            echo_key="capture_id",
            echo_value=capture_id,
        )
        return True

    @_fail_soft("capture_reveal_failed", False)
    async def after_capture(self, capture_id: int, width: int, height: int) -> bool:
        if not self._status.available or capture_id != self._capture_id:
            return False
        self._validate_capture_geometry(width, height)
        await self._send_command_expecting(
            {"op": "captureReveal", "capture_id": capture_id},
            "visible",
            "Overlay did not reveal after capture.",
            echo_key="capture_id",
            echo_value=capture_id,
        )
        return True

    @_fail_soft("encoder_failed", None)  # Pillow JPEG remains available
    async def encode_observation(self, png_bytes: bytes) -> "tuple[bytes, str] | None":
        if not self._status.available or self._mode == "status":
            return None
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

    async def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        await cancel_and_drain(*self._shell_rail_removals)
        if self._process is not None and self._process.returncode is None and self._fatal_error is None:
            try:
                if self._mode == "overlay":
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
                if reply.get("event") == "activityGrip":
                    self._activity_grip_region = _valid_region(reply.get("region"))
                    continue
                if reply.get("event") == "previewDemand":
                    self._preview_demand = bool(reply.get("menuOpen")) or bool(reply.get("activityOpen"))
                    self._telemetry.append({"type": "preview_demand", "active": self._preview_demand})
                    if self.on_preview_demand is not None:
                        self.on_preview_demand(self._preview_demand)
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
        operation_name = str(operation.get("op"))
        # showTerminal replaces the capsule in the renderer, so it shares the capsule's
        # dedupe slot -- otherwise the clearThought that takes a finished card down would
        # be suppressed as a repeat of the clearThought that preceded the card.
        capsule_ops = {"showThought", "clearThought", "showTerminal"}
        dedupe_key = "thought" if operation_name in capsule_ops else operation_name
        if dedupe_key in {"thought", "previewAction", "moveCursor"} and not self._render_changed(dedupe_key, operation):
            return {"ok": True}
        return await self._send_envelope({"operation": operation}, allow_stopping=allow_stopping)

    def _render_changed(self, key: str, payload: Any) -> bool:
        """Return whether `payload` differs from the last render cached under `key`, updating the cache.

        Shared by `_send_operation` (deduping thought/previewAction/moveCursor state) and
        `_render_shell_rail` (deduping the shell rail's commands/overflow payload) -- both compare a
        canonical JSON signature against `self._last_render` before sending.
        """
        signature = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        if self._last_render.get(key) == signature:
            return False
        self._last_render[key] = signature
        return True

    async def _send_command(
        self,
        command: dict[str, Any],
        *,
        timeout: float = _OPERATION_TIMEOUT_SECONDS,
        allow_stopping: bool = False,
    ) -> dict[str, Any]:
        return await self._send_envelope({"command": command}, timeout=timeout, allow_stopping=allow_stopping)

    async def _send_command_expecting(
        self,
        command: dict[str, Any],
        expected_state: str,
        error_message: str,
        *,
        echo_key: "str | None" = None,
        echo_value: Any = None,
        timeout: float = _OPERATION_TIMEOUT_SECONDS,
        allow_stopping: bool = False,
    ) -> dict[str, Any]:
        """Send `command` and raise `error_message` unless the reply's `state` is `expected_state`.

        If `echo_key` is given, the reply must also echo `echo_value` under that key (used by the
        capture ops to confirm the reply matches the `capture_id` just sent, not a stale one).
        """
        reply = await self._send_command(command, timeout=timeout, allow_stopping=allow_stopping)
        if reply.get("state") != expected_state or (echo_key is not None and reply.get(echo_key) != echo_value):
            raise MacOSPresentationError(error_message)
        return reply

    async def _render_capsule(self) -> None:
        if self._queue_active:
            return
        text = " · ".join(part for part in (self._action_status, self._reasoning) if part)
        if not text and not self._active_keys:
            await self._send_operation({"op": "clearThought"})
            return
        operation: dict[str, Any] = {"op": "showThought", "markdown": text}
        if self._active_keys:
            operation["keys"] = self._active_keys
        await self._send_operation(operation)

    async def _render_terminal(self) -> None:
        """Morph the cursor badge into the renderer's run-command card.

        Placement belongs to the renderer: the card hangs off the cursor with the same
        edge-aware flipping the thought capsule gets, so the command reads where the
        operator is already looking rather than under the menu bar. With no command
        staged this falls back to the capsule, whose ``showThought``/``clearThought``
        is what takes a finished card down.
        """
        if not self._terminal_command:
            await self._render_capsule()
            return
        if self._queue_active:
            return
        await self._send_operation(
            {
                "op": "showTerminal",
                "presentation": {
                    "command": self._terminal_command,
                    "running": self._terminal_running,
                    "failed": self._terminal_failed,
                    # Omitted rather than sent empty: the renderer draws the output
                    # block only when there is output, so a command that has printed
                    # nothing yet keeps the card at command-only height instead of
                    # reserving two blank lines it may never fill.
                    **({"output": self._terminal_output} if self._terminal_output else {}),
                },
            }
        )

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
        self._active_keys = visual.get("keys")
        self._reset_terminal()

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

    def _record_shell(self, event: ShellPresentationEvent) -> None:
        """Log one shell lifecycle event as telemetry; both modes count the same commands."""
        self._telemetry.append(
            {
                "type": "background_command" if event.run_in_background else "shell_command",
                **asdict(event),
            }
        )

    async def _present_shell(self, event: ShellPresentationEvent) -> None:
        """Foreground: the run-command card by the cursor. Background: the rail.

        A foreground command has a cursor to hang off, so the renderer's card carries it
        and the rail stays out of the way -- one command on screen once, where the
        operator is already looking. A background command has no cursor moment of its
        own, so the rail under the menu bar remains its only surface.
        """
        self._record_shell(event)
        if event.run_in_background:
            await self._track_shell_rail(event)
            return

        if event.state in {"starting", "running"}:
            self._shell_started_at.setdefault(event.task_id, time.monotonic())
            # The card's own header says "RUN COMMAND", so the capsule keeps only the
            # reasoning; the badge under the card is reset before the card covers it.
            self._action_status = ""
            self._active_keys = None
            await self._send_operation(
                {
                    "op": "previewAction",
                    "presentation": {"badge": {"type": "loop"}, "queue": None, "transientEffects": []},
                }
            )
            # Keyed on task_id, not on the command text: the same command run twice
            # in a row is two tasks, and matching on text would let the second one
            # open showing the first one's output.
            if event.task_id != self._terminal_task_id:
                self._terminal_task_id = event.task_id
                self._terminal_output = ""
            if event.output is not None:
                self._terminal_output = event.output
            self._terminal_command = event.command
            self._terminal_running = True
            self._terminal_failed = False
            await self._render_terminal()
            return

        started_at = self._shell_started_at.pop(event.task_id, time.monotonic())
        remaining = _SHELL_MINIMUM_DWELL_SECONDS - (time.monotonic() - started_at)
        if remaining > 0 and not await self._sleep(remaining):
            return
        # The finished command reads in two beats, because the card and the capsule are
        # the same box in the renderer and cannot share it: the card holds with its caret
        # stopped (tinted, if the command did not exit clean) long enough to read the
        # command, then it gives the box back to a one-line verdict carrying the exit
        # code -- which reads better as a number beside the cursor than inside a card
        # whose body is the command itself.
        self._terminal_running = False
        self._terminal_failed = event.state != "completed"
        if event.output is not None:
            self._terminal_output = event.output
        await self._render_terminal()
        if not await self._sleep(_SHELL_COMMAND_TERMINAL_HOLD_SECONDS):
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
        self._reset_terminal()
        await self._render_capsule()
        await self._sleep(_SHELL_TERMINAL_HOLD_SECONDS)
        self._clear_action_labels()
        await self._render_capsule()

    async def _track_shell_rail(self, event: ShellPresentationEvent) -> None:
        """Mirror a shell lifecycle event into the rail under the menu bar.

        The rail carries the commands with no cursor of their own to hang a card off:
        every command in a status-mode run, and a foreground run's background commands.
        It keeps each one visible while it runs and for a hold after it finishes, so a
        command sent to the operator's Mac is never invisible.
        """
        self._shell_rail[event.task_id] = event
        await self._render_shell_rail()
        if event.state in _SHELL_TERMINAL_STATES:
            task = asyncio.create_task(self._remove_from_shell_rail_after_hold(event.task_id, event))
            self._shell_rail_removals.add(task)
            task.add_done_callback(self._shell_rail_removals.discard)

    async def _render_shell_rail(self) -> None:
        # Newest first: the most recent command lands at the top, just under the menu bar.
        events = list(reversed(self._shell_rail.values()))
        commands = [asdict(event) for event in events[:_SHELL_RAIL_ROWS]]
        overflow = max(0, len(events) - _SHELL_RAIL_ROWS)
        if not self._render_changed("shellCommands", [commands, overflow]):
            return
        await self._send_command({"op": "shellCommands", "commands": commands, "overflow": overflow})

    async def _remove_from_shell_rail_after_hold(self, task_id: str, terminal: ShellPresentationEvent) -> None:
        if not await self._sleep(_SHELL_COMMAND_TERMINAL_HOLD_SECONDS):
            return
        if self._shell_rail.get(task_id) == terminal:
            self._shell_rail.pop(task_id, None)
            await self._render_shell_rail()

    async def _lead(self) -> bool:
        return await self._sleep(_OVERLAY_LEAD_SECONDS)

    async def _sleep(self, seconds: float) -> bool:
        if self.cancellation.cancelled:
            return False
        sleeper, _cancelled, done = await race_sleep_against_cancellation(seconds, self.cancellation)
        return sleeper in done

    def _validate_status_ready(self, reply: dict[str, Any]) -> MacOSPresentationCapabilities:
        """The status-mode handshake: no page, so no viewport geometry and never a Stop region."""
        if reply.get("protocol_version") != OVERLAY_PROTOCOL_VERSION:
            raise MacOSPresentationError("Overlay returned an incompatible protocol version.")
        if reply.get("mode") != "status" or reply.get("stop_control") != "menu_bar":
            raise MacOSPresentationError("Overlay host did not start in status mode.")
        scale = reply.get("backing_scale")
        return MacOSPresentationCapabilities(
            protocol_version=OVERLAY_PROTOCOL_VERSION,
            viewport_width=0,
            viewport_height=0,
            backing_scale=float(scale) if _positive_finite(scale) else 1.0,
            hotkey=reply.get("hotkey") is True,
            stop_region=None,
        )

    def _validate_ready(self, reply: dict[str, Any]) -> MacOSPresentationCapabilities:
        if reply.get("protocol_version") != OVERLAY_PROTOCOL_VERSION:
            raise MacOSPresentationError("Overlay returned an incompatible protocol version.")
        width, height, scale = reply.get("width"), reply.get("height"), reply.get("backing_scale")
        stop_region = _valid_region(reply.get("stop_region"))
        if not _positive_finite(width) or not _positive_finite(height) or not _positive_finite(scale):
            raise MacOSPresentationError("Overlay returned invalid viewport capabilities.")
        # The Stop control is a menu bar status item. Its frame comes back as `stop_region` so
        # model clicks on it are refused; a host whose menu bar sits on another display reports
        # the control without a region, and then there is nothing on this screen to block.
        if self._show_stop_button and stop_region is None and reply.get("stop_control") != "menu_bar":
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

    def _validate_capture_geometry(self, width: int, height: int) -> "tuple[float, float]":
        capabilities = self._status.capabilities
        if capabilities is None:
            raise MacOSPresentationError("Overlay capabilities are unavailable.")
        return self._validate_geometry(capabilities, width, height)

    @staticmethod
    def _validate_geometry(
        capabilities: MacOSPresentationCapabilities, width: int, height: int
    ) -> "tuple[float, float]":
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
        return x_scale, y_scale

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
