"""Public values shared by the macOS computer and presentation runtime."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class N2Observation:
    """One desktop capture with native and encoded geometry kept separate."""

    capture_id: int
    native_width: int
    native_height: int
    encoded_width: int
    encoded_height: int
    media_type: str
    encoded_bytes: bytes

    @property
    def base64(self) -> str:
        return base64.b64encode(self.encoded_bytes).decode("ascii")

    @property
    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.base64}"


@dataclass(frozen=True)
class MacOSPresentationCapabilities:
    protocol_version: int
    viewport_width: int
    viewport_height: int
    backing_scale: float
    hotkey: bool
    stop_region: "tuple[float, float, float, float] | None"


@dataclass(frozen=True)
class MacOSPresentationStatus:
    """A snapshot of optional presentation health; safe to serialize as telemetry."""

    requested: bool
    available: bool
    state: str
    cursor: str
    capabilities: "MacOSPresentationCapabilities | None" = None
    degradation_reason: "str | None" = None
    codec: "str | None" = None
    fallback: "str | None" = None


@dataclass(frozen=True)
class MacOSWindowTarget:
    """One application window driven in window scope: pid plus WindowServer window id."""

    pid: int
    window_id: int
    title: "str | None" = None
    app_name: "str | None" = None

    def describe(self) -> str:
        name = self.app_name or "target application"
        return f"{name} (pid {self.pid}, window {self.window_id})"


@dataclass(frozen=True)
class MacOSActionOutcome:
    """What the driver reported about one window-scope input action; safe to serialize as telemetry."""

    tool: str
    requested_delivery: str
    effect: "str | None"
    route: "str | None"
    reported_delivery: "str | None"
    escalated: bool
    refusal_code: "str | None"
    recommended: "str | None" = None
    escalation_reason: "str | None" = None

    @property
    def landed(self) -> bool:
        """False when the driver says the action did not take effect or wants a foreground retry."""
        return self.effect not in {"suspected_noop", "refused"} and self.recommended != "foreground"


ShellLifecycleState = Literal[
    "starting",
    "running",
    "completed",
    "failed",
    "timed_out",
    "cancelled",
]


@dataclass(frozen=True)
class ShellPresentationEvent:
    """Sanitized shell identity and lifecycle metadata; command output is excluded."""

    task_id: str
    command: str
    run_in_background: bool
    state: ShellLifecycleState
    exit_code: "int | None" = None


class N2Presentation(Protocol):
    """Presentation sink consumed by the n2 loop and macOS computer."""

    @property
    def status(self) -> MacOSPresentationStatus: ...

    async def present(self, event: dict[str, Any]) -> None: ...


_CANCELLATION_PRIORITIES = {
    "operator_stop": 0,
    "target_crash": 1,
    "deadline": 2,
    "transport_failure": 3,
    "model_request": 4,
}


class CancellationLatch:
    """Latch one cancellation cause, resolving same-loop requests by priority."""

    def __init__(self) -> None:
        self._cause: "str | None" = None
        self._pending: "str | None" = None
        self._commit_scheduled = False
        self._event = asyncio.Event()

    @property
    def cause(self) -> "str | None":
        return self._cause or self._pending

    @property
    def cancelled(self) -> bool:
        return self.cause is not None

    def request(self, cause: str) -> None:
        if self._cause is not None:
            return
        if self._pending is None or self._priority(cause) < self._priority(self._pending):
            self._pending = cause
        if not self._commit_scheduled:
            self._commit_scheduled = True
            asyncio.get_running_loop().call_soon(self._commit)

    def _commit(self) -> None:
        self._commit_scheduled = False
        if self._cause is None and self._pending is not None:
            self._cause, self._pending = self._pending, None
            self._event.set()

    async def wait(self) -> str:
        await self._event.wait()
        assert self._cause is not None
        return self._cause

    def raise_if_cancelled(self) -> None:
        if self.cause is not None:
            raise asyncio.CancelledError(self.cause)

    @staticmethod
    def _priority(cause: str) -> int:
        return _CANCELLATION_PRIORITIES.get(cause, len(_CANCELLATION_PRIORITIES))
