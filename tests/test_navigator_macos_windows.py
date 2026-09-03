"""Tests for the pure window-selection helpers behind window scope."""

from __future__ import annotations

from typing import Any

from yutori.navigator.macos.windows import select_target_window, window_records


def _window(
    window_id: int,
    *,
    width: float = 800,
    height: float = 600,
    z_index: "int | None" = 0,
    on_current_space: "bool | None" = True,
    is_on_screen: "bool | None" = True,
) -> dict[str, Any]:
    return {
        "window_id": window_id,
        "pid": 1,
        "bounds": {"x": 0, "y": 0, "width": width, "height": height},
        "z_index": z_index,
        "on_current_space": on_current_space,
        "is_on_screen": is_on_screen,
    }


def test_window_records_keeps_only_entries_with_an_id_and_numeric_bounds():
    payload = {
        "windows": [
            _window(1),
            {"window_id": "2", "bounds": {"width": 10, "height": 10}},
            {"window_id": 3},
            {"window_id": 4, "bounds": {"width": "wide", "height": 10}},
            "junk",
        ]
    }
    assert [window["window_id"] for window in window_records(payload)] == [1]
    assert window_records(None) == [] and window_records({"windows": "none"}) == []


def test_prefers_the_requested_window_when_it_is_still_listed():
    windows = [_window(1, z_index=9), _window(2, z_index=1)]
    assert select_target_window(windows, prefer_window_id=2)["window_id"] == 2
    assert select_target_window(windows, prefer_window_id=5)["window_id"] == 1


def test_prefers_the_frontmost_content_window_on_the_current_space():
    windows = [
        _window(1, z_index=3),
        _window(2, z_index=8, on_current_space=False),
        _window(3, z_index=5),
        _window(4, z_index=9, width=2000, height=40),  # menu-bar/helper strip
    ]
    assert select_target_window(windows)["window_id"] == 3


def test_null_z_index_sorts_behind_any_integer_and_area_breaks_ties():
    windows = [_window(1, z_index=None, width=4000, height=3000), _window(2, z_index=0, width=200, height=200)]
    assert select_target_window(windows)["window_id"] == 2
    tied = [_window(1, z_index=None, width=300, height=300), _window(2, z_index=None, width=400, height=400)]
    assert select_target_window(tied)["window_id"] == 2


def test_off_space_and_off_screen_windows_are_eligible_when_nothing_is_on_the_current_space():
    windows = [
        _window(1, z_index=None, on_current_space=False, is_on_screen=False, width=500, height=500),
        _window(2, z_index=2, on_current_space=False, is_on_screen=False),
    ]
    assert select_target_window(windows)["window_id"] == 2


def test_helper_strips_only_win_when_nothing_else_exists():
    strips = [_window(1, width=30, height=900), _window(2, width=2000, height=20)]
    assert select_target_window(strips)["window_id"] == 2
    assert select_target_window([]) is None


def test_exclude_window_id_moves_off_a_window_that_is_still_listed():
    windows = [_window(7, z_index=136, width=500, height=500), _window(9, z_index=83, width=1260, height=1084)]
    assert select_target_window(windows)["window_id"] == 7
    assert select_target_window(windows, exclude_window_id=7)["window_id"] == 9
    assert select_target_window(windows, prefer_window_id=7, exclude_window_id=7)["window_id"] == 9
    assert select_target_window([windows[0]], exclude_window_id=7) is None
