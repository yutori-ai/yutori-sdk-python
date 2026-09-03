"""Complete macOS computer-use handler backed by one persistent CuaDriver transport."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import glob
import io
import os
import re
import signal
import subprocess
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from PIL import Image

from .frontmost import FrontmostApp, frontmost_app
from .no_progress import NoProgressWatchdog
from .polling import (
    FRAME_DIFF_TOLERANT_FRACTION,
    FRAME_POLL_ACTION_MAX_MS,
    FramePollResult,
    frame_difference,
    frame_poll_wait_budget_ms,
    frame_signature,
    poll_until_frame_changes,
)
from .presentation import MacOSPresentationController
from .preview import WindowPreviewStreamer
from .process_lifecycle import cancel_and_drain, race_sleep_against_cancellation
from .sanitize import sanitize_command_preview
from .transport import (
    CuaDriverConnectionError,
    CuaDriverToolError,
    CuaDriverTransport,
    CuaDriverUncertainActionError,
)
from .types import (
    CancellationLatch,
    MacOSActionOutcome,
    MacOSPresentationStatus,
    MacOSWindowTarget,
    N2Observation,
    ShellPresentationEvent,
)
from .visibility import unhide_application
from .windows import select_target_window, window_records

_CAPTURE_ATTEMPTS = 3
_CAPTURE_RETRY_SECONDS = 0.25
_SHELL_RESULT_MAX_CHARACTERS = 8_000
_SHELL_RESULT_TRUNCATION_SUFFIX = "\n[result truncated]"
_SHELL_EMPTY_SUCCESS_OUTPUT = "Command exited with code 0 and produced no output."
_MAX_OBSERVATION_LONG_SIDE = 1920
_OBSERVATION_QUALITY = 80
_SHELL_ENV_BLOCKLIST = {"BASH_ENV", "ENV", "YUTORI_API_KEY"}
_VCS_DIRECTORIES = {".git", ".hg", ".svn"}
_GLOB_RESULT_LIMIT = 100
_DELIVERY_BACKGROUND = "background"
_DELIVERY_FOREGROUND = "foreground"
# Driver refusal codes that mean the driven window is gone (or never belonged to the
# target process) versus ones that only invalidate the frame the coordinates came from.
_WINDOW_LOSS_CODES = frozenset({"window_id_not_found", "window_owner_pid_mismatch"})
_STALE_FRAME_CODES = frozenset({"px_frame_mismatch", "px_capture_unavailable"})
# The window is still listed by WindowServer but has no accessibility window: a closed
# dialog that lingers as a zombie record, or a window on another Space. Input cannot reach
# it; another window of the same app usually can be driven instead.
_WINDOW_UNRESOLVED_CODES = frozenset({"off_space_or_ax_unresolved"})
_UNRESOLVED_CAPTURE_REASON = "ax_window_unresolved"
# Background delivery refused up front; fronting the window (the foreground rung) makes the
# keystrokes unambiguous or un-minimizes the window, so these behave like "did not land".
_ESCALATABLE_REFUSAL_CODES = frozenset({"same_pid_keyboard_ambiguity", "minimized_or_hidden_window"})
# Window scope shows its progress in a menu bar item, a shell rail, and the activity window
# instead of the full-screen overlay.
_STATUS_TITLE = "Yutori n2 is working in a window in the background"
_THUMBNAIL_LONG_SIDE = 720  # 360pt in the menu, rendered at 2x for Retina menu bars
_THUMBNAIL_QUALITY = 70


class MacOSComputerError(RuntimeError):
    pass


class MacOSRecoverableActionError(MacOSComputerError):
    recoverable = True


class MacOSActionRefusedError(MacOSRecoverableActionError):
    pass


class MacOSUncertainActionError(MacOSRecoverableActionError):
    def __init__(self, message: str, observation: "N2Observation | None" = None) -> None:
        super().__init__(message)
        self.observation = observation


class MacOSFocusChangedError(MacOSUncertainActionError):
    """Keystrokes were withheld because the frontmost app changed since the last screenshot.

    Desktop-scope keyboard delivery goes to whatever application is frontmost. When that is
    no longer the application the model was looking at, the safe outcome is to send nothing
    and hand the model a fresh frame; the attached observation is that frame.
    """


class MacOSBackgroundDeliveryError(MacOSUncertainActionError):
    """A window-scope action was posted in the background but the driver reports it did not land.

    Strict window scope never fronts the target on its own; the model gets the driver's
    verdict, a fresh frame of the window, and can retry through a different control or
    shortcut. ``outcome`` is the parsed driver envelope for that attempt.
    """

    def __init__(
        self,
        message: str,
        observation: "N2Observation | None" = None,
        outcome: "MacOSActionOutcome | None" = None,
    ) -> None:
        super().__init__(message, observation)
        self.outcome = outcome


class MacOSTargetWindowChangedError(MacOSUncertainActionError):
    """The driven window disappeared mid-action and the adapter rebound to another window of the app.

    The model's coordinates were measured against a window that no longer exists, so the
    action is not retried; the attached observation is the first frame of the new window.
    """


class MacOSTargetCrashedError(MacOSComputerError):
    pass


@dataclass(frozen=True)
class _ProcessIdentity:
    pid: int
    group: int
    started_at: str


@dataclass
class _BackgroundProcess:
    task_id: str
    process: asyncio.subprocess.Process
    identity: _ProcessIdentity
    command: str
    output_path: Path
    status_path: Path
    terminal_state: "str | None" = None
    monitor: "asyncio.Task[None] | None" = None


# Shared by both supervisor scripts below: a background subshell that polls
# whether the shell's original parent is still its direct parent (not merely
# alive — reparented onto init after a crash counts as gone) and force-kills
# the whole process group the moment it isn't. This is what keeps a spawned
# foreground/background shell from outliving the Python process that started
# it. The two supervisors previously hand-duplicated this block; kept in one
# place so the parent-liveness contract can't drift between them.
_PARENT_DEATH_WATCHER = """(
  trap 'exit 0' TERM
  while /bin/kill -0 "$parent_pid" 2>/dev/null; do
    current_parent=$(/bin/ps -o ppid= -p "$$" | /usr/bin/tr -d '[:space:]')
    [ "$current_parent" = "$parent_pid" ] || break
    /bin/sleep 0.2
  done
  /bin/kill -KILL 0
) &
watcher=$!""".strip()


_FOREGROUND_SUPERVISOR = f"""
parent_pid=$1
shell_kind=$2
{_PARENT_DEATH_WATCHER}
if [ "$shell_kind" = bash ]; then
  /bin/bash --noprofile --norc -s
else
  /bin/sh -s
fi
status=$?
/bin/kill "$watcher" 2>/dev/null || true
wait "$watcher" 2>/dev/null || true
exit "$status"
""".strip()


_BACKGROUND_SUPERVISOR = f"""
parent_pid=$1
status_path=$2
{_PARENT_DEATH_WATCHER}
/bin/bash --noprofile --norc -s
status=$?
printf '%s\\n' "$status" > "$status_path"
wait "$watcher"
""".strip()


def _structured(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("structuredContent") or result.get("structured_content") or {}
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> "str | None":
    return value if isinstance(value, str) else None


def _parse_action_outcome(
    tool: str,
    requested_delivery: str,
    structured: dict[str, Any],
    *,
    escalated: bool = False,
) -> MacOSActionOutcome:
    """Read the driver's action envelope (effect/route/delivery/escalation) defensively.

    Missing fields count as a landed action: pixel input on macOS is normally reported as
    ``unverifiable`` and older payloads carry no envelope at all. The next rung is read from
    ``escalation.target`` (cua-driver 0.23) or ``escalation.recommended`` (the documented name).
    """
    delivery = structured.get("delivery")
    escalation = structured.get("escalation") if isinstance(structured.get("escalation"), dict) else {}
    refusal = structured.get("refusal")
    return MacOSActionOutcome(
        tool=tool,
        requested_delivery=requested_delivery,
        effect=_text(structured.get("effect")),
        route=_text(structured.get("route")),
        reported_delivery=_text(delivery.get("mode")) if isinstance(delivery, dict) else _text(delivery),
        escalated=escalated,
        refusal_code=_text(refusal.get("code")) if isinstance(refusal, dict) else _text(structured.get("code")),
        recommended=_text(escalation.get("recommended")) or _text(escalation.get("target")),
        escalation_reason=_text(escalation.get("reason")),
    )


def _error_code(error: CuaDriverToolError) -> "str | None":
    code = getattr(error, "code", None)
    if isinstance(code, str):
        return code
    message = str(error)
    known = (*_WINDOW_LOSS_CODES, *_STALE_FRAME_CODES, *_WINDOW_UNRESOLVED_CODES, *_ESCALATABLE_REFUSAL_CODES)
    return next((candidate for candidate in known if candidate in message), None)


def _capture_unresolved(structured: dict[str, Any]) -> bool:
    """Whether a window capture came back for a window that no accessibility window backs."""
    reason = structured.get("degraded_reason")
    return isinstance(reason, str) and reason.startswith(_UNRESOLVED_CAPTURE_REASON)


def _is_window_loss(error: CuaDriverToolError) -> bool:
    return _error_code(error) in _WINDOW_LOSS_CODES


def _decode_inline_frame(result: dict[str, Any], tool_name: str) -> tuple[bytes, int, int]:
    """Return the inline PNG frame of a capture result, cross-checked against its reported size."""
    structured = _structured(result)
    width, height = structured.get("screenshot_width"), structured.get("screenshot_height")
    image_data = next(
        (
            part.get("data")
            for part in result.get("content") or []
            if isinstance(part, dict) and part.get("type") == "image" and part.get("data")
        ),
        None,
    )
    if not isinstance(image_data, str):
        raise MacOSComputerError(f"{tool_name} returned no inline pixel frame")
    pixels = base64.b64decode(image_data, validate=True)
    with Image.open(io.BytesIO(pixels)) as image:
        decoded_width, decoded_height = image.size
    if isinstance(width, int) and width != decoded_width:
        raise ValueError(f"reported screenshot width {width} != decoded width {decoded_width}")
    if isinstance(height, int) and height != decoded_height:
        raise ValueError(f"reported screenshot height {height} != decoded height {decoded_height}")
    if decoded_width <= 0 or decoded_height <= 0:
        raise MacOSComputerError(f"{tool_name} returned an empty frame")
    return pixels, decoded_width, decoded_height


def _shell_environment() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if name not in _SHELL_ENV_BLOCKLIST}


def _format_shell_result(output: str, exit_code: int) -> str:
    if exit_code == 0 and not output:
        return _SHELL_EMPTY_SUCCESS_OUTPUT
    marker = f"[exit code {exit_code}]" if exit_code else ""
    budget = _SHELL_RESULT_MAX_CHARACTERS - (len(marker) + 1 if marker else 0)
    if len(output) > budget:
        output = output[: budget - len(_SHELL_RESULT_TRUNCATION_SUFFIX)] + _SHELL_RESULT_TRUNCATION_SUFFIX
    if not marker:
        return output
    if not output:
        return marker
    return f"{output}{'' if output.endswith(chr(10)) else chr(10)}{marker}"


def _bash_cwd_wrapper(command: str, sentinel: str) -> str:
    return f"{command}\n__yutori_rc=$?\nprintf '\\n{sentinel}%s' \"$PWD\"\nexit $__yutori_rc\n"


def _split_bash_cwd(text: str, sentinel: str) -> tuple[str, "str | None"]:
    output, marker, reported = text.rpartition(f"\n{sentinel}")
    return (output, reported or None) if marker else (text, None)


def _path_mtime_descending(path: Path) -> tuple[float, str]:
    try:
        return (-path.stat().st_mtime, str(path))
    except OSError:
        return (0, str(path))


def _process_identity(pid: int) -> "_ProcessIdentity | None":
    try:
        completed = subprocess.run(
            ["/bin/ps", "-o", "pgid=", "-o", "lstart=", "-p", str(pid)],
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    group_text, separator, started_at = completed.partition(" ")
    try:
        group = int(group_text)
    except ValueError:
        return None
    return _ProcessIdentity(pid, group, started_at.strip()) if separator and group > 0 and started_at.strip() else None


class MacOSComputer:
    """Async macOS desktop session with capture, input, presentation, and shell lifecycle."""

    def __init__(
        self,
        transport: "CuaDriverTransport | None" = None,
        *,
        owns_transport: "bool | None" = None,
        session: "str | None" = None,
        presentation: bool = True,
        show_stop_button: bool = True,
        allow_local_shell: bool = False,
        execution_deadline: "float | None" = None,
        cancellation: "CancellationLatch | None" = None,
        target_pid: "int | None" = None,
        recover_target: "Callable[[], Awaitable[int | None]] | None" = None,
        overlay_cache_directory: "str | Path | None" = None,
        known_secrets: "Sequence[str]" = (),
        verify_focus: bool = True,
        frontmost_probe: "Callable[[], Awaitable[FrontmostApp | None]] | None" = None,
        scope: Literal["desktop", "window"] = "desktop",
        target_window: "MacOSWindowTarget | None" = None,
        allow_foreground_fallback: bool = False,
    ) -> None:
        if transport is not None and owns_transport is None:
            raise ValueError("owns_transport must be explicit when transport is injected")
        if scope not in ("desktop", "window"):
            raise ValueError("scope must be 'desktop' or 'window'")
        if target_window is not None and scope != "window":
            raise ValueError("target_window requires scope='window'")
        self.transport = transport or CuaDriverTransport()
        self.owns_transport = True if transport is None else bool(owns_transport)
        self.session = session or f"yutori-n2-{uuid.uuid4().hex[:12]}"
        self.presentation_requested = presentation
        self.show_stop_button = show_stop_button
        self.allow_local_shell = allow_local_shell
        self.execution_deadline = execution_deadline
        self.cancellation = cancellation or CancellationLatch()
        self.target_pid = target_window.pid if target_window is not None else target_pid
        self.recover_target = recover_target
        self.scope = scope
        self.allow_foreground_fallback = allow_foreground_fallback
        self._target_window = target_window
        self._window_capture: "tuple[int, int] | None" = None
        self._action_outcomes: list[MacOSActionOutcome] = []
        self._preview: "WindowPreviewStreamer | None" = None
        self._delivery_counts: dict[str, int] = {
            "background_attempts": 0,
            "foreground_escalations": 0,
            "fallback_skips": 0,
            "background_refusals": 0,
            "window_rebinds": 0,
        }
        self.overlay_cache_directory = overlay_cache_directory
        self.verify_focus = verify_focus
        self._frontmost_probe = frontmost_probe or frontmost_app
        self._observed_frontmost: "FrontmostApp | None" = None
        self._focus_guard_trips = 0
        values = (known_secrets,) if isinstance(known_secrets, str) else known_secrets
        self._known_secrets = tuple(secret for secret in values if secret)
        self.presentation: "MacOSPresentationController | None" = None
        self._session_started = False
        self._emulated_held_keys: list[str] = []
        self._native_size: "tuple[int, int] | None" = None
        self._initial_png: "bytes | None" = None
        self._capture_id = 0
        self._captures = 0
        self._current_observation: "N2Observation | None" = None
        self._bash_cwd = str(Path.home())
        self._file_snapshots: dict[Path, str] = {}
        self._left_mouse_down = False
        self._held_mouse_start: "tuple[int, int] | None" = None
        self._pointer: "tuple[int, int] | None" = None
        self._background: dict[str, _BackgroundProcess] = {}
        self._foreground_processes: set[asyncio.subprocess.Process] = set()
        self._shell_events: list[ShellPresentationEvent] = []
        self._deadline_task: "asyncio.Task[None] | None" = None
        self._target_recoveries = 0
        self._native_cursor = "current"
        self._presentation_failure: "str | None" = None
        self._codec: "str | None" = None
        self._no_progress = NoProgressWatchdog()
        self._closed = False
        self._timings: dict[str, float] = {
            "action_ms": 0,
            "capture_ms": 0,
            "encode_ms": 0,
            "shell_ms": 0,
            "polling_ms": 0,
        }

    async def __aenter__(self) -> "MacOSComputer":
        if self.execution_deadline is not None:
            if self.execution_deadline <= time.monotonic():
                self.cancellation.request("deadline")
                self.cancellation.raise_if_cancelled()
            self._deadline_task = asyncio.create_task(self._watch_deadline())
        try:
            self.cancellation.raise_if_cancelled()
            await self._await_with_cancellation(self.transport.start())
            if self.window_mode:
                # Window scope never grabs the desktop: the first frame is the target window,
                # taken after set_window_target(). The user's pointer stays theirs, so the
                # driver's synthetic cursor is switched off instead of themed, and the
                # full-screen overlay is not started.
                await self._call_tool("start_session", {"session": self.session})
                self._session_started = True
                self._native_cursor = "hidden"
                with suppress(Exception):
                    await self._configure_cursor(False)
                if self.presentation_requested:
                    await self._start_status_presentation()
                else:
                    self._presentation_failure = "window_mode"
                return self
            await self._call_tool(
                "start_session",
                {"session": self.session, "capture_scope": "desktop"},
            )
            self._session_started = True
            self._initial_png, width, height = await self._capture_png()
            self._native_size = (width, height)
            self._native_cursor = await self._select_native_cursor()
            if self.presentation_requested:
                await self._start_presentation(width, height)
            return self
        except BaseException:
            await self.aclose()
            raise

    async def __aexit__(self, exc_type: Any, _exc: Any, _traceback: Any) -> None:
        try:
            await self.aclose()
        except Exception:
            if exc_type is None:
                raise

    @property
    def current_observation(self) -> "N2Observation | None":
        return self._current_observation

    @property
    def timings(self) -> dict[str, int]:
        return {
            **{key: round(value) for key, value in self._timings.items()},
            "screenshots": self._captures,
        }

    @property
    def no_progress_triggers(self) -> int:
        return self._no_progress.triggers

    @property
    def target_recovery_attempts(self) -> int:
        return self._target_recoveries

    @property
    def shell_events(self) -> tuple[ShellPresentationEvent, ...]:
        return tuple(self._shell_events)

    @property
    def window_mode(self) -> bool:
        return self.scope == "window"

    @property
    def target_window(self) -> "MacOSWindowTarget | None":
        return self._target_window

    @property
    def last_action_outcome(self) -> "MacOSActionOutcome | None":
        return self._action_outcomes[-1] if self._action_outcomes else None

    @property
    def action_outcomes(self) -> tuple[MacOSActionOutcome, ...]:
        return tuple(self._action_outcomes)

    @property
    def delivery_counts(self) -> dict[str, int]:
        return dict(self._delivery_counts)

    @property
    def preview_frames_sent(self) -> int:
        """Frames streamed to the live view (status-item runs), for telemetry."""
        return self._preview.frames_sent if self._preview is not None else 0

    @property
    def window_target_info(self) -> "dict[str, Any] | None":
        target = self._target_window
        if target is None:
            return None
        capture = self._window_capture
        return {
            "pid": target.pid,
            "window_id": target.window_id,
            "title": target.title,
            "app_name": target.app_name,
            "capture_width": capture[0] if capture else None,
            "capture_height": capture[1] if capture else None,
        }

    async def set_window_target(self, target: "MacOSWindowTarget | None") -> None:
        """Bind (or clear) the window this window-scope session captures and drives.

        The next screenshot is the first frame of that window and the model's coordinates
        are window-local from then on, so every frame-derived state is dropped here.
        """
        if not self.window_mode:
            raise MacOSComputerError("set_window_target requires scope='window'.")
        if self._left_mouse_down:
            raise MacOSRecoverableActionError("Release the held mouse button before changing the target window.")
        self.cancellation.raise_if_cancelled()
        self._bind_window_target(target)
        await self._announce_target()

    async def _announce_target(self) -> None:
        target = self._target_window
        if self.presentation is not None and target is not None:
            await self.presentation.present({"type": "status", "text": f"Driving {target.describe()}"})

    async def _push_thumbnail(self, observation: N2Observation) -> None:
        """Status mode: hand the menu bar item a small copy of the frame the model just received."""
        if self.presentation is None:
            return
        target = self._target_window
        caption = f"Frame {observation.capture_id}" + (f" of {target.describe()}" if target is not None else "")
        try:
            thumbnail = await asyncio.to_thread(self._thumbnail_jpeg, observation.encoded_bytes)
        except (OSError, ValueError):
            return
        await self.presentation.show_thumbnail(thumbnail, caption=caption)

    @staticmethod
    def _thumbnail_jpeg(image_bytes: bytes) -> bytes:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = source.convert("RGB")
            image.thumbnail((_THUMBNAIL_LONG_SIDE, _THUMBNAIL_LONG_SIDE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=_THUMBNAIL_QUALITY)
            return output.getvalue()

    def _bind_window_target(self, target: "MacOSWindowTarget | None") -> None:
        self._target_window = target
        self._initial_png = None
        self._native_size = None
        self._current_observation = None
        self._observed_frontmost = None
        self._window_capture = None
        self._no_progress.reset()
        if target is not None:
            self.target_pid = target.pid

    async def unhide_app(self, pid: int) -> bool:
        """Show a hidden app's windows behind the user's without activating it.

        ``launch_app`` starts apps hidden, and the driver refuses raw keyboard input to a hidden
        window; window-scope callers unhide the target before binding it. Returns whether the
        app is now visible (advisory: a False leaves the driver to refuse keys itself).
        """
        return bool(await self._await_with_cancellation(unhide_application(pid)))

    async def resolve_window_target(
        self,
        pid: int,
        *,
        prefer_window_id: "int | None" = None,
        exclude_window_id: "int | None" = None,
    ) -> "MacOSWindowTarget | None":
        """Pick the window of ``pid`` to drive, or None while it has no usable window."""
        record = select_target_window(
            window_records(await self.list_windows(pid)),
            prefer_window_id=prefer_window_id,
            exclude_window_id=exclude_window_id,
        )
        if record is None:
            return None
        return MacOSWindowTarget(
            pid=pid,
            window_id=int(record["window_id"]),
            title=_text(record.get("title")),
            app_name=_text(record.get("app_name")),
        )

    def record_model_action(self, name: str, arguments: dict[str, Any], *, refused: bool = False) -> None:
        self._no_progress.record_action(name, arguments, refused=refused)

    @property
    def presentation_status(self) -> MacOSPresentationStatus:
        if self.presentation is not None:
            status = self.presentation.status
            return replace(status, codec=self._codec or status.codec)
        return MacOSPresentationStatus(
            requested=self.presentation_requested,
            available=False,
            state="unavailable",
            cursor=self._native_cursor,
            degradation_reason=self._presentation_failure if self.presentation_requested else None,
            codec=self._codec,
            fallback=self._native_cursor,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._deadline_task is not None:
            self._deadline_task.cancel()
            await asyncio.gather(self._deadline_task, return_exceptions=True)
            self._deadline_task = None
        await self._cancel_shell_processes()
        if self._preview is not None:
            await self._preview.aclose()
            self._preview = None
        if self.presentation is not None:
            await self.presentation.stop()
            if not self.window_mode:
                # Window scope never showed the agent cursor; leave the user's pointer alone.
                await self._restore_native_cursor()
        if self._session_started:
            with suppress(Exception):
                await self.transport.call_tool("end_session", {"session": self.session})
            self._session_started = False
        if self.owns_transport:
            await self.transport.close()

    async def screenshot(self, text: "str | None" = None) -> N2Observation:
        del text
        self.cancellation.raise_if_cancelled()
        started_at = time.monotonic()
        self._capture_id += 1
        self._captures += 1
        capture_id = self._capture_id
        png_bytes = self._initial_png
        self._initial_png = None
        hidden = False
        if png_bytes is None:
            if self.presentation is not None:
                hidden = await self.presentation.before_capture(capture_id)
            try:
                png_bytes, width, height = await self._capture_png()
            finally:
                if hidden and self.presentation is not None:
                    geometry = (
                        (width, height) if "width" in locals() and "height" in locals() else self._native_size or (1, 1)
                    )
                    await self.presentation.after_capture(
                        capture_id,
                        *geometry,
                    )
        else:
            assert self._native_size is not None
            width, height = self._native_size
        self._native_size = (width, height)
        self._timings["capture_ms"] += (time.monotonic() - started_at) * 1000
        observation = await self._encode_observation(capture_id, png_bytes, width, height)
        self._current_observation = observation
        self._no_progress.record_frame(observation)
        if self.window_mode:
            await self._push_thumbnail(observation)
        if self.verify_focus and not self.window_mode:
            self._observed_frontmost = await self._probe_frontmost()
        await self._ensure_target_alive()
        return observation

    async def get_dimensions(self) -> tuple[int, int]:
        if self._native_size is None:
            await self.screenshot()
        assert self._native_size is not None
        return self._native_size

    async def get_environment(self) -> Literal["mac"]:
        return "mac"

    def _merged_modifiers(self, modifier: "Sequence[str] | None") -> list[str]:
        return list(dict.fromkeys([*self._emulated_held_keys, *(modifier or ())]))

    async def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        count: int = 1,
        modifier: "Sequence[str] | None" = None,
    ) -> None:
        self._refuse_stop_point(x, y)
        arguments = self._action_args(x=x, y=y, button=button, count=count)
        modifiers = self._merged_modifiers(modifier)
        if modifiers:
            arguments["modifier"] = modifiers
            if self.window_mode:
                # macOS only observes modifier state for a frontmost target, so the driver
                # requires foreground delivery for modified clicks.
                if not self.allow_foreground_fallback:
                    raise MacOSRecoverableActionError(
                        "Modified clicks need foreground delivery, which this window-scope session does not "
                        "allow; use key_press for the shortcut or an unmodified click instead."
                    )
                arguments["delivery_mode"] = _DELIVERY_FOREGROUND
        await self._mutate("click", arguments)
        self._pointer = (x, y)

    async def double_click(self, x: int, y: int, modifier: "Sequence[str] | None" = None) -> None:
        await self.click(x, y, count=2, modifier=modifier)

    async def triple_click(self, x: int, y: int, modifier: "Sequence[str] | None" = None) -> None:
        await self.click(x, y, count=3, modifier=modifier)

    async def move(self, x: int, y: int) -> None:
        self._refuse_stop_point(x, y)
        self._pointer = (x, y)
        if self.window_mode:
            # Window scope has no pointer to move: the real cursor stays with the user and the
            # synthetic cursor is off, so a hover is recorded for a later drag but never posted.
            return
        if not self._left_mouse_down:
            await self._mutate("move_cursor", self._action_args(x=x, y=y))

    async def drag(self, path: list[dict[str, int]]) -> None:
        if len(path) < 2:
            raise ValueError("drag path must contain at least two points")
        if self._emulated_held_keys:
            raise MacOSRecoverableActionError("The pinned Cua Driver cannot drag with a held modifier.")
        start, end = path[0], path[-1]
        self._refuse_stop_point(start["x"], start["y"])
        self._refuse_stop_point(end["x"], end["y"])
        await self._mutate(
            "drag",
            self._action_args(
                from_x=start["x"],
                from_y=start["y"],
                to_x=end["x"],
                to_y=end["y"],
            ),
        )
        self._pointer = (end["x"], end["y"])

    async def left_mouse_down(self, x: "int | None" = None, y: "int | None" = None) -> None:
        if (x is None) != (y is None):
            raise ValueError("mouse_down coordinates must include both x and y")
        point = (x, y) if x is not None and y is not None else self._pointer
        if point is None:
            raise MacOSRecoverableActionError("mouse_down requires coordinates after the pointer has moved.")
        self._refuse_stop_point(*point)
        self._pointer = point
        self._held_mouse_start = point
        self._left_mouse_down = True

    async def left_mouse_up(self, x: "int | None" = None, y: "int | None" = None) -> None:
        if (x is None) != (y is None):
            raise ValueError("mouse_up coordinates must include both x and y")
        point = (x, y) if x is not None and y is not None else self._pointer
        if self._held_mouse_start is None or point is None:
            raise MacOSRecoverableActionError("mouse_up requires a preceding mouse_down with known coordinates.")
        self._refuse_stop_point(*point)
        start = self._held_mouse_start
        try:
            if start == point:
                await self.click(*point)
            else:
                await self.drag([{"x": start[0], "y": start[1]}, {"x": point[0], "y": point[1]}])
        finally:
            self._left_mouse_down = False
            self._held_mouse_start = None

    async def release_held_mouse_button(self) -> None:
        if self._left_mouse_down:
            await self.left_mouse_up()

    async def scroll(
        self,
        x: int,
        y: int,
        scroll_x: int,
        scroll_y: int,
        modifier: "Sequence[str] | None" = None,
    ) -> None:
        if self._merged_modifiers(modifier):
            raise MacOSRecoverableActionError(
                "scroll with a held modifier is not supported; use key_press and an unmodified scroll"
            )
        if scroll_x == 0 and scroll_y == 0:
            return
        self._refuse_stop_point(x, y)
        width, height = await self.get_dimensions()
        horizontal = abs(scroll_x) > abs(scroll_y)
        delta = scroll_x if horizontal else scroll_y
        direction = ("right" if delta > 0 else "left") if horizontal else ("down" if delta > 0 else "up")
        dimension = width if horizontal else height
        amount = max(1, min(50, round(abs(delta) / max(1, dimension * 0.1))))
        await self._mutate(
            "scroll",
            self._action_args(x=x, y=y, direction=direction, amount=amount, by="line"),
        )

    async def type(self, text: str) -> None:
        if self._emulated_held_keys:
            raise MacOSRecoverableActionError("The pinned Cua Driver cannot hold a modifier while typing text.")
        await self._guard_frontmost("type_text")
        await self._mutate("type_text", self._action_args(text=text, delay_ms=0))

    async def keypress(self, keys: "Sequence[str] | str") -> None:
        sequence = [keys] if isinstance(keys, str) else list(keys)
        if self._emulated_held_keys:
            sequence = self._merged_modifiers(sequence)
        await self._guard_frontmost("press_key" if len(sequence) == 1 else "hotkey")
        if len(sequence) == 1:
            await self._mutate("press_key", self._action_args(key=sequence[0]))
        else:
            await self._mutate("hotkey", self._action_args(keys=sequence))

    async def key_down(self, key: str) -> None:
        """Emulate a single held modifier until the next n2 batch action.

        Cua Driver's pinned public protocol exposes atomic modified clicks and
        key chords, not low-level key-down/key-up RPCs. Keeping this state in
        the adapter lets those atomic actions preserve n2's held-modifier
        semantics without claiming a physical key remains down across RPCs.
        """
        if key not in {"ctrl", "shift", "alt", "cmd"}:
            raise MacOSRecoverableActionError(
                f"The pinned Cua Driver can only emulate held modifier keys, not {key!r}."
            )
        if key not in self._emulated_held_keys:
            self._emulated_held_keys.append(key)

    async def key_up(self, key: str) -> None:
        self._emulated_held_keys = [held_key for held_key in self._emulated_held_keys if held_key != key]

    async def hold_key(self, key: str, ms: int = 1000) -> None:
        if not 0 <= ms <= 300_000:
            raise ValueError("hold_key must be between 0 and 300 seconds")
        await self.key_down(key)
        try:
            await self._sleep(ms / 1000)
        finally:
            await self.key_up(key)

    async def wait(self, ms: int = 1000) -> None:
        if not 0 <= ms <= 300_000:
            raise ValueError("wait must be between 0 and 300 seconds")
        await self._sleep(ms / 1000)

    async def wait_for_change(self, requested_ms: int, reference: N2Observation) -> N2Observation:
        """Replace one standalone wait with a deadline-bounded tolerant frame poll."""
        result = await poll_until_frame_changes(
            capture=self.screenshot,
            reference=reference,
            mode="tolerant",
            budget_ms=frame_poll_wait_budget_ms(requested_ms),
            deadline=self.execution_deadline,
            cancellation=self.cancellation,
        )
        return await self._settle_frame_poll(result)

    async def poll_after_action(
        self,
        action_name: str,
        reference: N2Observation,
        first_frame: N2Observation,
    ) -> N2Observation:
        """Return as soon as an ordinary GUI action materially changes the desktop."""
        if action_name.lower() not in {
            "left_click",
            "click",
            "right_click",
            "middle_click",
            "double_click",
            "triple_click",
            "scroll",
            "drag",
            "key_press",
            "key",
        }:
            return first_frame
        result = await poll_until_frame_changes(
            capture=self.screenshot,
            reference=reference,
            first_frame=first_frame,
            mode="strict",
            budget_ms=FRAME_POLL_ACTION_MAX_MS,
            deadline=self.execution_deadline,
            cancellation=self.cancellation,
        )
        return await self._settle_frame_poll(result, fallback=first_frame)

    async def _settle_frame_poll(
        self, result: FramePollResult, *, fallback: "N2Observation | None" = None
    ) -> N2Observation:
        """Bank a completed poll's time/cancellation and resolve its frame.

        A missing ``fallback`` means the caller had no frame of its own to fall
        back to (:meth:`wait_for_change`'s blind wait), so a fresh capture
        stands in instead.
        """
        self.add_polling_time(max(0, result.waited_ms - result.capture_ms))
        if result.outcome == "aborted":
            self.cancellation.raise_if_cancelled()
        if isinstance(result.last_frame, N2Observation):
            return result.last_frame
        if fallback is not None:
            return fallback
        return await self.screenshot()

    async def run_shell_command(
        self,
        command: str,
        cwd: "str | None" = None,
        timeout_seconds: int = 10,
    ) -> str:
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("shell_command timeout_seconds must be between 1 and 30")
        return await self._run_foreground_shell(
            command,
            cwd=cwd,
            timeout=float(timeout_seconds),
            tool_name="shell_command",
        )

    async def run_bash_command(
        self,
        command: str,
        timeout: float = 120.0,
        run_in_background: bool = False,
    ) -> str:
        if isinstance(timeout, bool) or not 0 <= timeout <= 600:
            raise ValueError("bash timeout must be between 0 and 600")
        self._require_local_shell()
        if run_in_background:
            return await self._run_background_shell(command)
        sentinel = f"__YUTORI_N2_BASH_CWD_{uuid.uuid4().hex}__"
        result, reported_cwd = await self._run_foreground_shell(
            _bash_cwd_wrapper(command, sentinel),
            cwd=self._bash_cwd,
            timeout=float(timeout) if timeout > 0 else None,
            tool_name="bash",
            presentation_command=command,
            bash=True,
            cwd_sentinel=sentinel,
        )
        self._bash_cwd = reported_cwd or self._bash_cwd
        return result

    async def read_file(self, file_path: str, offset: int = 1, limit: int = 2_000) -> str:
        self._require_local_shell()
        if offset < 1:
            raise ValueError("read.offset must be a positive 1-based line number")
        path = self._resolve_file_path(file_path)
        text = await asyncio.to_thread(self._read_text_file, path)
        self._file_snapshots[path] = text
        lines = text.splitlines()
        return "\n".join(
            f"{line_number:6}\t{line}"
            for line_number, line in enumerate(lines[offset - 1 : offset - 1 + limit], start=offset)
        )

    async def write_file(self, file_path: str, content: str) -> str:
        self._require_local_shell()
        path = self._resolve_file_path(file_path)
        await asyncio.to_thread(self._write_text_file, path, content)
        self._file_snapshots[path] = content
        return f"Wrote {len(content)} characters to {path}."

    async def edit_file(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        self._require_local_shell()
        path = self._resolve_file_path(file_path)
        if old_string == "":
            if await asyncio.to_thread(path.exists):
                raise MacOSRecoverableActionError(
                    f"Cannot create {path}: it already exists; use a non-empty old_string to edit it."
                )
            await asyncio.to_thread(self._write_text_file, path, new_string)
            self._file_snapshots[path] = new_string
            return f"File created successfully at: {file_path}"
        if path not in self._file_snapshots:
            raise MacOSRecoverableActionError(f"Read or write {path} before editing it.")
        text = await asyncio.to_thread(self._read_text_file, path)
        matches = text.count(old_string)
        if not matches:
            raise MacOSRecoverableActionError(f"Could not find the requested text in {path}.")
        if matches > 1 and not replace_all:
            raise MacOSRecoverableActionError(
                f"Found {matches} matches in {path}; use replace_all to edit every occurrence."
            )
        replacements = matches if replace_all else 1
        updated = text.replace(old_string, new_string, -1 if replace_all else 1)
        await asyncio.to_thread(self._write_text_file, path, updated)
        self._file_snapshots[path] = updated
        return f"Edited {path}: replaced {replacements} occurrence(s)."

    async def grep_files(
        self,
        pattern: str,
        path: "str | None" = None,
        glob_pattern: "str | None" = None,
        file_type: "str | None" = None,
        output_mode: str = "files_with_matches",
        ignore_case: bool = False,
        show_line_numbers: "bool | None" = None,
        before_context: "int | None" = None,
        after_context: "int | None" = None,
        context: "int | None" = None,
        head_limit: "int | None" = 250,
        multiline: bool = False,
    ) -> str:
        self._require_local_shell()
        root = self._resolve_file_path(path or self._bash_cwd)
        return await asyncio.to_thread(
            self._grep_files,
            pattern,
            root,
            glob_pattern,
            file_type,
            output_mode,
            ignore_case,
            show_line_numbers,
            before_context,
            after_context,
            context,
            head_limit,
            multiline,
        )

    async def glob_files(self, pattern: str, path: "str | None" = None) -> str:
        self._require_local_shell()
        root = self._resolve_file_path(path or self._bash_cwd)
        return await asyncio.to_thread(self._glob_files, pattern, root)

    async def launch_app(
        self,
        *,
        name: "str | None" = None,
        bundle_id: "str | None" = None,
        urls: "list[str] | None" = None,
    ) -> dict[str, Any]:
        arguments = {
            **({"name": name} if name else {}),
            **({"bundle_id": bundle_id} if bundle_id else {}),
            **({"urls": urls} if urls else {}),
        }
        result = await self._call_tool("launch_app", arguments)
        return _structured(result)

    def _resolve_file_path(self, file_path: str) -> Path:
        path = Path(file_path).expanduser()
        return path if path.is_absolute() else Path(self._bash_cwd) / path

    @staticmethod
    def _read_text_file(path: Path) -> str:
        data = path.read_bytes()
        if data.startswith(b"\xef\xbb\xbf"):
            return data.decode("utf-8-sig")
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return data.decode("utf-16")
        return data.decode("utf-8")

    @staticmethod
    def _write_text_file(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _iter_search_files(root: Path) -> list[Path]:
        if root.is_file():
            return [root]
        if not root.is_dir():
            return []
        files: list[Path] = []
        for directory, child_directories, filenames in os.walk(root):
            child_directories[:] = [name for name in child_directories if name not in _VCS_DIRECTORIES]
            files.extend(Path(directory, filename) for filename in filenames)
        return files

    @classmethod
    def _grep_files(
        cls,
        pattern: str,
        root: Path,
        glob_pattern: "str | None",
        file_type: "str | None",
        output_mode: str,
        ignore_case: bool,
        show_line_numbers: "bool | None",
        before_context: "int | None",
        after_context: "int | None",
        context: "int | None",
        head_limit: "int | None",
        multiline: bool,
    ) -> str:
        flags = re.MULTILINE | (re.IGNORECASE if ignore_case else 0)
        if multiline:
            flags |= re.DOTALL
        try:
            regex = re.compile(pattern, flags)
        except re.error as error:
            raise ValueError(f"invalid regex: {error}") from error

        def matches_file(file_path: Path) -> bool:
            if file_type and file_path.suffix != f".{file_type.lstrip('.')}":
                return False
            if not glob_pattern:
                return True
            try:
                relative = file_path.relative_to(root if root.is_dir() else root.parent)
            except ValueError:
                relative = file_path
            return fnmatch.fnmatch(str(relative), glob_pattern) or fnmatch.fnmatch(file_path.name, glob_pattern)

        files_with_matches: list[Path] = []
        counts: list[str] = []
        content: list[str] = []
        show_numbers = output_mode == "content" if show_line_numbers is None else show_line_numbers
        before = after = context if context is not None else None
        before = before if before is not None else before_context or 0
        after = after if after is not None else after_context or 0

        for file_path in cls._iter_search_files(root):
            if not matches_file(file_path):
                continue
            try:
                text = cls._read_text_file(file_path)
            except (OSError, UnicodeError):
                continue
            lines = text.splitlines() or [text]
            matches = [index for index, line in enumerate(lines) if regex.search(line)]
            if multiline and regex.search(text):
                matches = [0]
            if not matches:
                continue
            files_with_matches.append(file_path)
            counts.append(f"{file_path}:{len(matches)}")
            if output_mode != "content":
                continue
            emitted: set[int] = set()
            for index in matches:
                for line_index in range(max(0, index - before), min(len(lines), index + after + 1)):
                    if line_index in emitted:
                        continue
                    emitted.add(line_index)
                    prefix = f"{file_path}:{line_index + 1}:" if show_numbers else f"{file_path}:"
                    content.append(prefix + lines[line_index])

        if output_mode == "files_with_matches":
            result = [str(file_path) for file_path in sorted(files_with_matches, key=_path_mtime_descending)]
        elif output_mode == "count":
            result = counts
        else:
            result = content
        if head_limit not in {None, 0}:
            result = result[:head_limit]
        return "\n".join(result) if result else "No matches found."

    @staticmethod
    def _glob_files(pattern: str, root: Path) -> str:
        search_pattern = pattern if Path(pattern).is_absolute() else str(root / pattern)
        matches = [Path(match) for match in glob.glob(search_pattern, recursive=True) if Path(match).exists()]
        matches.sort(key=_path_mtime_descending)
        result = [str(match) for match in matches[:_GLOB_RESULT_LIMIT]]
        if len(matches) > _GLOB_RESULT_LIMIT:
            result.append(f"[... truncated to first {_GLOB_RESULT_LIMIT} of {len(matches)} matches ...]")
        return "\n".join(result) if result else "No files found."

    async def list_windows(self, pid: int) -> dict[str, Any]:
        result = await self._call_tool("list_windows", {"pid": pid}, read_only=True)
        return _structured(result)

    async def bring_to_front(self, pid: int, window_id: "int | None" = None) -> None:
        arguments = {"pid": pid, **({"window_id": window_id} if window_id is not None else {})}
        await self._call_tool("bring_to_front", arguments)

    async def start_recording(self, output_directory: "str | Path") -> None:
        await self._call_tool(
            "start_recording",
            {"output_dir": str(output_directory), "record_video": True},
        )

    async def stop_recording(self) -> dict[str, Any]:
        # Recording cleanup must remain available after Stop/deadline cancellation.
        result = await self.transport.call_tool("stop_recording", {})
        return _structured(result)

    def add_polling_time(self, milliseconds: float) -> None:
        self._timings["polling_ms"] += milliseconds

    async def _start_status_presentation(self) -> None:
        """Window scope: the menu bar item, the shell rail, and the activity window's transcript."""
        controller = MacOSPresentationController(
            native_width=0,
            native_height=0,
            cancellation=self.cancellation,
            cache_directory=self.overlay_cache_directory,
            show_stop_button=self.show_stop_button,
            mode="status",
            title=_STATUS_TITLE,
        )
        try:
            await controller.start()
            await controller.reveal()
            self.presentation = controller
        except Exception as error:
            self._presentation_failure = f"status_item_start_failed:{type(error).__name__}"
            await controller.stop()
            return
        # The live frame: while the menu is open or the activity window is shown, stream the
        # driven window over a dedicated driver connection so the model's own captures and
        # actions never wait behind it.
        self._preview = WindowPreviewStreamer(
            target=lambda: self._target_window,
            sink=controller.show_preview_frame,
            transport_factory=lambda: CuaDriverTransport(binary=getattr(self.transport, "binary", None)),
            cancellation=self.cancellation,
        )
        controller.on_preview_demand = self._preview.set_active

    async def _start_presentation(self, width: int, height: int) -> None:
        controller = MacOSPresentationController(
            native_width=width,
            native_height=height,
            cancellation=self.cancellation,
            cache_directory=self.overlay_cache_directory,
            show_stop_button=self.show_stop_button,
            restore_native_cursor=self._restore_native_cursor,
        )
        try:
            await controller.start()
            await self._configure_cursor(False)
            await controller.reveal()
            self.presentation = controller
        except Exception as error:
            self._presentation_failure = f"overlay_start_failed:{type(error).__name__}"
            await controller.stop()
            await self._restore_native_cursor()

    async def _call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        read_only: bool = False,
        timeout_seconds: "float | None" = None,
    ) -> dict[str, Any]:
        """Run one driver RPC while making Stop effective during the await."""
        self.cancellation.raise_if_cancelled()
        if not read_only:
            self._initial_png = None
        try:
            return await self._await_with_cancellation(
                self.transport.call_tool(
                    name,
                    arguments,
                    read_only=read_only,
                    timeout_seconds=timeout_seconds,
                )
            )
        except CuaDriverConnectionError:
            self.cancellation.request("transport_failure")
            await asyncio.sleep(0)
            raise

    async def _await_with_cancellation(self, awaitable: Awaitable[Any]) -> Any:
        operation = asyncio.create_task(awaitable)
        stopped = asyncio.create_task(self.cancellation.wait())
        try:
            done, _ = await asyncio.wait({operation, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if operation in done:
                return operation.result()
            raise asyncio.CancelledError(stopped.result())
        finally:
            await cancel_and_drain(operation, stopped)

    async def _capture_png(self) -> tuple[bytes, int, int]:
        if self.window_mode:
            return await self._capture_window_png()
        return await self._capture_desktop_png()

    async def _capture_desktop_png(self) -> tuple[bytes, int, int]:
        last_error: "Exception | None" = None
        for attempt in range(_CAPTURE_ATTEMPTS):
            if attempt:
                await self._sleep(_CAPTURE_RETRY_SECONDS)
            result = await self._call_tool(
                "get_desktop_state",
                {"session": self.session},
                read_only=True,
            )
            try:
                return _decode_inline_frame(result, "get_desktop_state")
            except (ValueError, OSError, MacOSComputerError) as error:
                last_error = error
        raise MacOSComputerError(f"get_desktop_state returned no usable frame after 3 attempts: {last_error}")

    async def _capture_window_png(self) -> tuple[bytes, int, int]:
        """Grab the driven window only, following it if the driver says the window went away."""
        last_error: "Exception | None" = None
        for attempt in range(_CAPTURE_ATTEMPTS):
            if attempt:
                await self._sleep(_CAPTURE_RETRY_SECONDS)
            target = self._require_window_target()
            try:
                result = await self._call_tool(
                    "get_window_state",
                    {
                        "session": self.session,
                        "pid": target.pid,
                        "window_id": target.window_id,
                        "include_screenshot": True,
                        # The tree is not consumed; the schema minimums bound the AX walk.
                        "max_elements": 1,
                        "max_depth": 1,
                    },
                    read_only=True,
                )
            except CuaDriverToolError as error:
                last_error = error
                if _is_window_loss(error):
                    await self._rebind_window_target(_error_code(error) or "window_lost")
                    continue
                if _error_code(error) in _STALE_FRAME_CODES:
                    continue
                raise
            if _capture_unresolved(_structured(result)) and await self._rebind_to_alternative_window() is not None:
                # A zombie record of a closed dialog renders blank; the app's live window is next.
                last_error = MacOSComputerError(f"window {target.window_id} has no accessibility window")
                continue
            try:
                pixels, width, height = _decode_inline_frame(result, "get_window_state")
            except (ValueError, OSError, MacOSComputerError) as error:
                last_error = error
                continue
            self._window_capture = (width, height)
            return pixels, width, height
        raise MacOSComputerError(f"get_window_state returned no usable frame after 3 attempts: {last_error}")

    def _require_window_target(self) -> MacOSWindowTarget:
        if self._target_window is None:
            raise MacOSComputerError(
                "Window scope needs a target window: call set_window_target() before capturing or acting."
            )
        return self._target_window

    async def _encode_observation(self, capture_id: int, png_bytes: bytes, width: int, height: int) -> N2Observation:
        started_at = time.monotonic()
        encoded: "tuple[bytes, str] | None" = None
        if self.presentation is not None:
            encoded = await self.presentation.encode_observation(png_bytes)
        if encoded is None:
            encoded = self._encode_with_pillow(png_bytes)
        encoded_bytes, codec = encoded
        self._codec = codec
        with Image.open(io.BytesIO(encoded_bytes)) as image:
            encoded_width, encoded_height = image.size
        self._timings["encode_ms"] += (time.monotonic() - started_at) * 1000
        return N2Observation(
            capture_id=capture_id,
            native_width=width,
            native_height=height,
            encoded_width=encoded_width,
            encoded_height=encoded_height,
            media_type=f"image/{codec}",
            encoded_bytes=encoded_bytes,
        )

    @staticmethod
    def _encode_with_pillow(png_bytes: bytes) -> tuple[bytes, str]:
        with Image.open(io.BytesIO(png_bytes)) as source:
            image = source.convert("RGB")
            image.thumbnail((_MAX_OBSERVATION_LONG_SIDE, _MAX_OBSERVATION_LONG_SIDE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            try:
                image.save(output, format="WEBP", quality=_OBSERVATION_QUALITY)
                return output.getvalue(), "webp"
            except OSError:
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=_OBSERVATION_QUALITY)
                return output.getvalue(), "jpeg"

    async def _mutate(self, tool: str, arguments: dict[str, Any]) -> None:
        self.cancellation.raise_if_cancelled()
        started_at = time.monotonic()
        try:
            if self.window_mode:
                await self._mutate_window(tool, arguments)
            else:
                await self._call_tool(tool, arguments)
        except CuaDriverUncertainActionError as error:
            observation = await self._fresh_observation()
            raise MacOSUncertainActionError(str(error), observation) from error
        finally:
            self._timings["action_ms"] += (time.monotonic() - started_at) * 1000
        await self._ensure_target_alive()

    async def _mutate_window(self, tool: str, arguments: dict[str, Any]) -> None:
        """Deliver one window-scope action, escalating to foreground only when allowed and needed.

        ``unverifiable``/``partial`` count as landed: pixel input is normally unverifiable and a
        partial effect has already changed the window, so re-sending would double-act. For the
        same reason a foreground retry is skipped when the window already changed after the
        background attempt: the driver's ``delivery_failed`` verdict can be wrong for part of a
        keystroke sequence (typing "15*15" twice into Calculator was observed live), and the
        model sees the actual state on its next frame either way.
        """
        requested = str(arguments.get("delivery_mode", _DELIVERY_BACKGROUND))
        escalated = requested == _DELIVERY_FOREGROUND
        self._delivery_counts["foreground_escalations" if escalated else "background_attempts"] += 1
        reference = self._current_observation
        outcome = await self._deliver_window_action(tool, arguments, requested, escalated=escalated)
        if outcome.landed:
            return
        observation = await self._fresh_observation()
        if not escalated and self.allow_foreground_fallback:
            if self._frame_changed(reference, observation):
                self._delivery_counts["fallback_skips"] += 1
                return
            self._delivery_counts["foreground_escalations"] += 1
            outcome = await self._deliver_window_action(
                tool,
                {**arguments, "delivery_mode": _DELIVERY_FOREGROUND},
                _DELIVERY_FOREGROUND,
                escalated=True,
            )
            if outcome.landed:
                return
            observation = await self._fresh_observation()
        self._delivery_counts["background_refusals"] += 1
        target = self._target_window
        where = target.describe() if target is not None else "the target window"
        detail = f"effect={outcome.effect or 'unknown'}"
        if outcome.recommended:
            detail += f", recommended={outcome.recommended}"
        if outcome.escalation_reason:
            detail += f", reason={outcome.escalation_reason}"
        raise MacOSBackgroundDeliveryError(
            f"{tool} was posted to {where} with {outcome.requested_delivery} delivery but the driver reports it "
            f"did not land ({detail}). Check the attached frame; try a keyboard shortcut or a different control.",
            observation,
            outcome,
        )

    async def _deliver_window_action(
        self,
        tool: str,
        arguments: dict[str, Any],
        requested: str,
        *,
        escalated: bool,
    ) -> MacOSActionOutcome:
        try:
            result = await self._call_tool(tool, arguments)
        except CuaDriverToolError as error:
            code = _error_code(error)
            if code in _ESCALATABLE_REFUSAL_CODES:
                # Nothing was delivered; report it like a non-landing action so the fallback
                # policy decides between a foreground retry and a recoverable refusal.
                outcome = MacOSActionOutcome(
                    tool=tool,
                    requested_delivery=requested,
                    effect="refused",
                    route=None,
                    reported_delivery=None,
                    escalated=escalated,
                    refusal_code=code,
                    recommended=_DELIVERY_FOREGROUND,
                    escalation_reason=code,
                )
                self._action_outcomes.append(outcome)
                return outcome
            if _is_window_loss(error):
                previous = self._target_window
                current = await self._rebind_window_target(code or "window_lost")
                observation = await self._fresh_observation()
                raise MacOSTargetWindowChangedError(
                    f"{tool} was not delivered: {previous.describe() if previous else 'the target window'} is gone "
                    f"({code or 'window lost'}); now driving {current.describe()}. Check the attached frame and "
                    "retry against it.",
                    observation,
                ) from error
            if code in _STALE_FRAME_CODES:
                observation = await self._fresh_observation()
                raise MacOSUncertainActionError(
                    f"{tool} was refused ({code}); retry against the attached frame.", observation
                ) from error
            if code in _WINDOW_UNRESOLVED_CODES:
                previous = self._target_window
                where = previous.describe() if previous is not None else "the target window"
                current = await self._rebind_to_alternative_window()
                observation = await self._fresh_observation()
                if current is not None:
                    raise MacOSTargetWindowChangedError(
                        f"{tool} was not delivered: {where} can no longer be driven ({code}); now driving "
                        f"{current.describe()}. Check the attached frame and retry against it.",
                        observation,
                    ) from error
                self._delivery_counts["background_refusals"] += 1
                raise MacOSBackgroundDeliveryError(
                    f"{tool} was refused ({code}): {where} is on another Space or has no accessibility "
                    "surface, so background input cannot reach it and the app has no other window to drive. "
                    "Check the attached frame.",
                    observation,
                ) from error
            raise
        outcome = _parse_action_outcome(tool, requested, _structured(result), escalated=escalated)
        self._action_outcomes.append(outcome)
        return outcome

    async def _fresh_observation(self) -> "N2Observation | None":
        with suppress(Exception):
            return await self.screenshot()
        return None

    @staticmethod
    def _frame_changed(reference: "N2Observation | None", current: "N2Observation | None") -> bool:
        """Whether the window materially changed between two frames (unknown frames count as unchanged)."""
        if reference is None or current is None:
            return False
        before, after = frame_signature(reference), frame_signature(current)
        if before is None or after is None:
            return False
        return frame_difference(before, after) > FRAME_DIFF_TOLERANT_FRACTION

    @property
    def focus_guard_trips(self) -> int:
        return self._focus_guard_trips

    async def _probe_frontmost(self) -> "FrontmostApp | None":
        try:
            return await self._await_with_cancellation(self._frontmost_probe())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the probe is advisory; failing open keeps input flowing
            return None

    async def _guard_frontmost(self, tool: str) -> None:
        """Refuse desktop-scope keystrokes when focus moved since the model's last frame.

        The driver delivers ``type_text``/``press_key``/``hotkey`` in desktop scope to the
        frontmost application without a target check. Comparing the frontmost app now with
        the one recorded at the last screenshot catches a dialog, notification, slow launch,
        or app switch that landed after the model decided what to type. On a mismatch the
        keys are not sent and the model receives the current frame instead.
        """
        if self.window_mode or not self.verify_focus or self._observed_frontmost is None:
            return
        current = await self._probe_frontmost()
        if current is None or current.pid == self._observed_frontmost.pid:
            return
        expected = self._observed_frontmost
        self._focus_guard_trips += 1
        observation = None
        with suppress(Exception):
            observation = await self.screenshot()
        raise MacOSFocusChangedError(
            f"{tool} was not sent: the frontmost application changed from {expected.describe()} to "
            f"{current.describe()} after the last screenshot. Check the attached frame and retry the keys "
            "if that application is the intended target.",
            observation,
        )

    def _action_args(self, **arguments: Any) -> dict[str, Any]:
        """Session, coordinate frame, and delivery for one input RPC.

        Desktop scope is the legacy contract: screen-absolute pixels delivered to the
        frontmost app. Window scope addresses one window through the ``target`` object (the
        driver refuses it alongside the legacy scope/pid/window_id fields) with window-local
        pixels and background delivery, so the user's focus is never taken.
        """
        if not self.window_mode:
            return {
                "session": self.session,
                "scope": "desktop",
                "delivery_mode": _DELIVERY_FOREGROUND,
                **arguments,
            }
        target = self._require_window_target()
        return {
            "session": self.session,
            "target": {"kind": "window", "pid": target.pid, "window_id": target.window_id},
            "delivery_mode": _DELIVERY_BACKGROUND,
            **arguments,
        }

    def _refuse_stop_point(self, x: int, y: int) -> None:
        if self.window_mode or self.presentation is None or self._native_size is None:
            return
        width, height = self._native_size
        normalized = (x / width * 1000, y / height * 1000)
        if self.presentation.blocks_point(normalized):
            raise MacOSActionRefusedError("Action refused because it intersects the Stop control.")

    async def _select_native_cursor(self) -> str:
        try:
            await self._configure_cursor(True)
        except Exception:
            return "cursorless"
        for theme in ("yutori.default", "cua.default"):
            try:
                await self._call_tool(
                    "set_agent_cursor_theme",
                    {"session": self.session, "theme_id": theme},
                )
                return theme
            except CuaDriverToolError:
                continue
        return "current"

    async def _configure_cursor(self, enabled: bool) -> None:
        await self.transport.call_tool(
            "set_agent_cursor_enabled",
            {"session": self.session, "enabled": enabled},
        )
        if enabled:
            await self.transport.call_tool(
                "set_agent_cursor_motion",
                {"session": self.session, "idle_hide_ms": 0},
            )

    async def _restore_native_cursor(self) -> str:
        try:
            await self._configure_cursor(True)
        except Exception:
            return "cursorless"
        return self._native_cursor

    def _require_local_shell(self) -> None:
        if not self.allow_local_shell:
            raise PermissionError("Local shell execution requires allow_local_shell=True")

    async def _spawn_supervised_shell(
        self,
        argv: "Sequence[str]",
        *,
        cwd: "str | None",
        stdout: Any,
        task_id: str,
        preview: str,
        run_in_background: bool,
        on_start_failure: "Callable[[], None] | None" = None,
    ) -> asyncio.subprocess.Process:
        """Spawn one parent-death-supervised shell, presenting cancel/failure identically for both shell kinds."""
        try:
            self.cancellation.raise_if_cancelled()
            return await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=_shell_environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=stdout,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except asyncio.CancelledError:
            await self._present_shell(ShellPresentationEvent(task_id, preview, run_in_background, "cancelled"))
            if on_start_failure is not None:
                on_start_failure()
            raise
        except Exception:
            await self._present_shell(ShellPresentationEvent(task_id, preview, run_in_background, "failed"))
            if on_start_failure is not None:
                on_start_failure()
            raise

    async def _run_foreground_shell(
        self,
        command: str,
        *,
        cwd: "str | None",
        timeout: "float | None",
        tool_name: str,
        presentation_command: "str | None" = None,
        bash: bool = False,
        cwd_sentinel: "str | None" = None,
    ) -> Any:
        self._require_local_shell()
        raw_presentation_command = presentation_command or command
        preview = sanitize_command_preview(raw_presentation_command, known_secrets=self._known_secrets)
        task_id = f"shell-{uuid.uuid4().hex[:8]}"
        await self._present_shell(ShellPresentationEvent(task_id, preview, False, "starting"))
        started_at = time.monotonic()
        process = await self._spawn_supervised_shell(
            ["/bin/sh", "-c", _FOREGROUND_SUPERVISOR, "yutori-shell", str(os.getpid()), "bash" if bash else "sh"],
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            task_id=task_id,
            preview=preview,
            run_in_background=False,
        )
        self._foreground_processes.add(process)
        await self._present_shell(ShellPresentationEvent(task_id, preview, False, "running"))
        try:
            stdout = await self._communicate(process, timeout, command.encode())
        except TimeoutError:
            await self._present_shell(ShellPresentationEvent(task_id, preview, False, "timed_out"))
            raise
        except asyncio.CancelledError:
            await self._present_shell(ShellPresentationEvent(task_id, preview, False, "cancelled"))
            raise
        finally:
            self._foreground_processes.discard(process)
            self._timings["shell_ms"] += (time.monotonic() - started_at) * 1000
        text = stdout.decode("utf-8", errors="replace") if stdout else ""
        output, reported_cwd = _split_bash_cwd(text, cwd_sentinel) if cwd_sentinel else (text, None)
        exit_code = int(process.returncode or 0)
        state = "completed" if exit_code == 0 else "failed"
        await self._present_shell(ShellPresentationEvent(task_id, preview, False, state, exit_code))
        rendered = _format_shell_result(output, exit_code)
        return (rendered, reported_cwd) if cwd_sentinel else rendered

    async def _run_background_shell(self, command: str) -> str:
        preview = sanitize_command_preview(command, known_secrets=self._known_secrets)
        task_id = f"bash-{uuid.uuid4().hex[:8]}"
        await self._present_shell(ShellPresentationEvent(task_id, preview, True, "starting"))
        descriptor, output_path_text = tempfile.mkstemp(prefix=f"yutori-n2-{task_id}-", suffix=".log")
        output_path = Path(output_path_text)
        status_path = output_path.with_suffix(".status")
        log_file = os.fdopen(descriptor, "wb")
        try:
            process = await self._spawn_supervised_shell(
                ["/bin/sh", "-c", _BACKGROUND_SUPERVISOR, "yutori-background", str(os.getpid()), str(status_path)],
                cwd=self._bash_cwd,
                stdout=log_file,
                task_id=task_id,
                preview=preview,
                run_in_background=True,
                on_start_failure=lambda: output_path.unlink(missing_ok=True),
            )
        finally:
            log_file.close()
        try:
            assert process.stdin is not None
            process.stdin.write(command.encode())
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()
        except BaseException:
            self._kill_process_group(process)
            await process.wait()
            output_path.unlink(missing_ok=True)
            raise
        identity = _process_identity(process.pid)
        if identity is None:
            self._kill_process_group(process)
            await process.wait()
            await self._present_shell(ShellPresentationEvent(task_id, preview, True, "failed"))
            raise MacOSComputerError("background process identity could not be established")
        background = _BackgroundProcess(task_id, process, identity, preview, output_path, status_path)
        self._background[task_id] = background
        background.monitor = asyncio.create_task(self._monitor_background(background))
        await self._present_shell(ShellPresentationEvent(task_id, preview, True, "running"))
        return (
            f"Started background task {task_id} (pid {process.pid}).\n"
            f"Output file: {output_path}\n"
            f"Cancel with: kill -- -{identity.group}"
        )

    async def _communicate(
        self,
        process: asyncio.subprocess.Process,
        timeout: "float | None",
        input_data: bytes,
    ) -> bytes:
        communication = asyncio.create_task(process.communicate(input_data))
        cancellation = asyncio.create_task(self.cancellation.wait())
        effective_timeout = timeout
        if self.execution_deadline is not None:
            remaining = max(0.0, self.execution_deadline - time.monotonic())
            effective_timeout = remaining if effective_timeout is None else min(effective_timeout, remaining)
        try:
            done, _ = await asyncio.wait(
                {communication, cancellation},
                timeout=effective_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if communication in done:
                stdout, _ = communication.result()
                return stdout or b""
            self._kill_process_group(process)
            await process.wait()
            if cancellation in done:
                raise asyncio.CancelledError(self.cancellation.cause)
            raise TimeoutError(f"command was killed after exceeding its {effective_timeout:g}-second timeout")
        except asyncio.CancelledError:
            if process.returncode is None:
                self._kill_process_group(process)
                await process.wait()
            raise
        finally:
            await cancel_and_drain(communication, cancellation)

    async def _monitor_background(self, background: _BackgroundProcess) -> None:
        try:
            return_code: "int | None" = None
            while background.process.returncode is None:
                try:
                    return_code = int(background.status_path.read_text(encoding="utf-8").strip())
                    break
                except (FileNotFoundError, OSError, ValueError):
                    await asyncio.sleep(0.1)
            if return_code is None:
                return_code = int(background.process.returncode or 0)
            state = "completed" if return_code == 0 else "failed"
            background.terminal_state = state
            await self._present_shell(
                ShellPresentationEvent(background.task_id, background.command, True, state, int(return_code))
            )
        except asyncio.CancelledError:
            return

    async def _cancel_shell_processes(self) -> None:
        foreground = tuple(self._foreground_processes)
        for process in foreground:
            if process.returncode is None:
                self._kill_process_group(process)
        await asyncio.gather(*(process.wait() for process in foreground), return_exceptions=True)
        backgrounds = tuple(self._background.values())
        for background in backgrounds:
            if background.monitor is not None:
                background.monitor.cancel()
        await asyncio.gather(
            *(background.monitor for background in backgrounds if background.monitor is not None),
            return_exceptions=True,
        )
        for background in backgrounds:
            was_running = background.terminal_state is None
            if background.process.returncode is None and self._identity_matches(background.identity):
                self._kill_process_group(background.process)
            elif background.process.returncode is None:
                background.process.kill()
            if was_running:
                background.terminal_state = "cancelled"
                await self._present_shell(
                    ShellPresentationEvent(background.task_id, background.command, True, "cancelled")
                )
            await background.process.wait()
            with suppress(OSError):
                background.status_path.unlink()
        self._background.clear()

    async def _present_shell(self, event: ShellPresentationEvent) -> None:
        self._shell_events.append(event)
        if self.presentation is not None:
            await self.presentation.present({"type": "shell", "event": event})

    async def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            self.cancellation.raise_if_cancelled()
            return
        timeout: float | None = None
        if self.execution_deadline is not None:
            remaining = max(0.0, self.execution_deadline - time.monotonic())
            # The sleeper owns ordinary waits; an equal timeout races it and can falsely latch the deadline.
            if remaining <= seconds:
                timeout = remaining
        sleeper, cancellation, done = await race_sleep_against_cancellation(seconds, self.cancellation, timeout=timeout)
        if sleeper not in done:
            if cancellation not in done:
                self.cancellation.request("deadline")
                await asyncio.sleep(0)
            raise asyncio.CancelledError(self.cancellation.cause)

    async def _watch_deadline(self) -> None:
        assert self.execution_deadline is not None
        remaining = self.execution_deadline - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)
        self.cancellation.request("deadline")

    async def _ensure_target_alive(self) -> None:
        if self.target_pid is None or self._pid_alive(self.target_pid):
            return
        recovered = await self._recover_target_pid()
        if recovered is None:
            await self._fail_target_crash(f"Target application {self.target_pid} exited.")
        self.target_pid = recovered
        if self.window_mode and (self._target_window is None or self._target_window.pid != recovered):
            # recover_target may already have bound a window of the relaunched process.
            await self._rebind_window_target("target_recovered")

    async def _recover_target_pid(self) -> "int | None":
        while self.recover_target is not None and self._target_recoveries < 2:
            self._target_recoveries += 1
            recovered = await self._await_with_cancellation(self.recover_target())
            if recovered is not None and self._pid_alive(recovered):
                return recovered
        return None

    async def _fail_target_crash(self, message: str) -> None:
        self.cancellation.request("target_crash")
        await asyncio.sleep(0)
        raise MacOSTargetCrashedError(message)

    async def _rebind_to_alternative_window(self) -> "MacOSWindowTarget | None":
        """Move to another live window of the target app, or None when it has no other window."""
        previous = self._target_window
        pid = self.target_pid if self.target_pid is not None else (previous.pid if previous is not None else None)
        if pid is None or not self._pid_alive(pid):
            return None
        target = await self.resolve_window_target(
            pid, exclude_window_id=previous.window_id if previous is not None else None
        )
        if target is None:
            return None
        self._delivery_counts["window_rebinds"] += 1
        self._bind_window_target(target)
        await self._announce_target()
        return target

    async def _rebind_window_target(self, reason: str) -> MacOSWindowTarget:
        """Follow the target app to another of its windows after the driver reported ours gone."""
        self._delivery_counts["window_rebinds"] += 1
        previous = self._target_window
        pid = self.target_pid if self.target_pid is not None else (previous.pid if previous is not None else None)
        if pid is None:
            raise MacOSComputerError(f"Cannot rebind the target window ({reason}): no target process is known.")
        if not self._pid_alive(pid):
            recovered = await self._recover_target_pid()
            if recovered is None:
                await self._fail_target_crash(f"Target application {pid} exited.")
            assert recovered is not None
            self.target_pid = pid = recovered
            if self._target_window is not None and self._target_window.pid == recovered:
                return self._target_window
        prefer = previous.window_id if previous is not None else None
        target = await self.resolve_window_target(pid, prefer_window_id=prefer)
        if target is None:
            await self._sleep(_CAPTURE_RETRY_SECONDS)
            target = await self.resolve_window_target(pid, prefer_window_id=prefer)
        if target is None:
            await self._fail_target_crash(f"Target application {pid} has no window left to drive ({reason}).")
        assert target is not None
        self._bind_window_target(target)
        await self._announce_target()
        return target

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except ProcessLookupError:
            return False

    @staticmethod
    def _kill_process_group(process: asyncio.subprocess.Process) -> None:
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        with suppress(ProcessLookupError):
            process.kill()

    @staticmethod
    def _identity_matches(identity: _ProcessIdentity) -> bool:
        return _process_identity(identity.pid) == identity
