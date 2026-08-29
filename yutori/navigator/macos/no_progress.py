"""Telemetry-only period-1/period-2 no-progress detection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .polling import frame_difference, frame_signature
from .types import N2Observation

MATERIAL_PIXEL_DELTA = 12
NEGLIGIBLE_CHANGE_FRACTION = 0.005


def _text_class(value: str) -> str:
    if not value:
        return "empty"
    if len(value) <= 12:
        return "short"
    if len(value) <= 80:
        return "medium"
    return "long"


def _normalized_value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        if key in {"action", "name", "key", "direction", "modifier", "button"}:
            return value.lower()
        return f"{key}:text-{_text_class(value)}"
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(value / 10) * 10 if "coordinate" in key.lower() or key.lower() in {"x", "y"} else value
    if isinstance(value, list):
        return [_normalized_value(key, entry) for entry in value]
    if isinstance(value, dict):
        return {nested_key: _normalized_value(nested_key, value[nested_key]) for nested_key in sorted(value)}
    return value


def action_signature(action: str, arguments: dict[str, Any], *, refused: bool = False) -> str:
    return json.dumps(
        {
            "action": action.lower(),
            "status": "refused" if refused else "executed",
            "args": _normalized_value("", arguments),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True)
class _ProgressSample:
    action: str
    frame: bytes
    changed_fraction: float


class NoProgressWatchdog:
    """Detect repeated visual cycles without altering execution decisions."""

    def __init__(self) -> None:
        self._last_frame: "bytes | None" = None
        self._pending_action: "str | None" = None
        self._samples: list[_ProgressSample] = []
        self._last_pattern_actions: list[str] = []
        self.triggers = 0

    def record_action(self, action: str, arguments: dict[str, Any], *, refused: bool = False) -> None:
        if self._suspends(action, arguments):
            self.reset()
            return
        self._pending_action = action_signature(action, arguments, refused=refused)

    def record_frame(self, frame: "N2Observation | str") -> None:
        signature = frame_signature(frame)
        if signature is None:
            return
        if self._last_frame is not None and self._pending_action is not None:
            if self._last_pattern_actions and self._pending_action not in self._last_pattern_actions:
                self._clear_cycle()
            changed_fraction = frame_difference(
                self._last_frame,
                signature,
                pixel_tolerance=MATERIAL_PIXEL_DELTA - 1,
            )
            two_actions_ago = self._samples[-2].frame if len(self._samples) >= 2 else None
            if changed_fraction > NEGLIGIBLE_CHANGE_FRACTION and (
                two_actions_ago is None
                or frame_difference(two_actions_ago, signature, pixel_tolerance=MATERIAL_PIXEL_DELTA - 1)
                > NEGLIGIBLE_CHANGE_FRACTION
            ):
                self._clear_cycle(preserve_samples=True)
            self._samples.append(_ProgressSample(self._pending_action, signature, changed_fraction))
            self._samples = self._samples[-6:]
            self._detect_cycle()
            self._pending_action = None
        self._last_frame = signature

    def reset(self) -> None:
        self._pending_action = None
        self._clear_cycle()

    def _detect_cycle(self) -> None:
        actions = self._cycle_actions(1) or self._cycle_actions(2)
        if actions is None:
            return
        if actions != self._last_pattern_actions:
            self.triggers += 1
        self._last_pattern_actions = actions

    def _cycle_actions(self, period: int) -> "list[str] | None":
        samples = self._samples[-period * 3 :]
        if len(samples) != period * 3:
            return None
        actions = [sample.action for sample in samples[:period]]
        if len(set(actions)) != period:
            return None
        if any(sample.action != actions[index % period] for index, sample in enumerate(samples[period:], start=period)):
            return None
        if period == 1:
            repeats = all(sample.changed_fraction <= NEGLIGIBLE_CHANGE_FRACTION for sample in samples)
        else:
            repeats = all(
                frame_difference(samples[index].frame, sample.frame, pixel_tolerance=MATERIAL_PIXEL_DELTA - 1)
                <= NEGLIGIBLE_CHANGE_FRACTION
                for index, sample in enumerate(samples[period:])
            )
        return sorted(actions) if repeats else None

    def _clear_cycle(self, *, preserve_samples: bool = False) -> None:
        if not preserve_samples:
            self._samples = []
        self._last_pattern_actions = []

    @staticmethod
    def _suspends(action: str, arguments: dict[str, Any]) -> bool:
        name = action.lower()
        if name == "wait":
            return True
        if name in {"bash", "shell_command", "run_command"} and arguments.get("run_in_background") is True:
            return True
        if name != "computer_batch" or not isinstance(arguments.get("actions"), list):
            return False
        for member in arguments["actions"]:
            if not isinstance(member, dict):
                continue
            member_name = member.get("name") or member.get("action")
            if member_name == "wait":
                return True
        return False
