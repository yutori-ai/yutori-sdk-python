"""Pure window-record helpers for choosing which window of an app to drive."""

from __future__ import annotations

from typing import Any

_MIN_WINDOW_EDGE_POINTS = 100.0


def window_records(payload: "dict[str, Any] | None") -> list[dict[str, Any]]:
    """Return the usable window records from a ``launch_app`` or ``list_windows`` payload.

    A record needs a ``window_id`` and numeric ``bounds`` width/height; anything else
    (helper strips without geometry, malformed entries) is dropped.
    """
    if not isinstance(payload, dict):
        return []
    windows = payload.get("windows")
    if not isinstance(windows, list):
        return []
    records: list[dict[str, Any]] = []
    for window in windows:
        if not isinstance(window, dict) or not isinstance(window.get("window_id"), int):
            continue
        bounds = window.get("bounds")
        if not isinstance(bounds, dict):
            continue
        width, height = bounds.get("width"), bounds.get("height")
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            records.append(window)
    return records


def _min_edge(window: dict[str, Any]) -> float:
    bounds = window["bounds"]
    return float(min(bounds["width"], bounds["height"]))


def _area(window: dict[str, Any]) -> float:
    bounds = window["bounds"]
    return float(bounds["width"]) * float(bounds["height"])


def _z_index(window: dict[str, Any]) -> int:
    value = window.get("z_index")
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def select_target_window(
    windows: "list[dict[str, Any]]",
    *,
    prefer_window_id: "int | None" = None,
    exclude_window_id: "int | None" = None,
    min_edge_points: float = _MIN_WINDOW_EDGE_POINTS,
) -> "dict[str, Any] | None":
    """Pick the window to drive from ``list_windows``-shaped records.

    Preference order: the exact ``prefer_window_id`` when it is still listed; otherwise
    the frontmost (highest integer ``z_index``; ``null`` sorts last) content window on the
    current Space, where a content window has both edges at least ``min_edge_points``
    wide. Off-screen and minimized windows are eligible: window scope drives them in the
    background. If nothing qualifies, the largest window wins. ``exclude_window_id`` drops
    one window first, for moving off a window that is still listed but can no longer be
    driven.
    """
    if exclude_window_id is not None:
        windows = [window for window in windows if window.get("window_id") != exclude_window_id]
    if prefer_window_id is not None:
        for window in windows:
            if window.get("window_id") == prefer_window_id:
                return window
    content = [window for window in windows if _min_edge(window) >= min_edge_points]
    on_space = [window for window in content if window.get("on_current_space") is not False]
    for candidates in (on_space, content):
        if candidates:
            return max(candidates, key=lambda window: (_z_index(window), _area(window)))
    if windows:
        return max(windows, key=_area)
    return None
