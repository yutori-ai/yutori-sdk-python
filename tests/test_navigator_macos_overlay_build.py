"""Tests for secure, version-coexisting overlay preparation."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from yutori.navigator.macos import overlay_build
from yutori.navigator.macos.overlay_build import (
    MacOSOverlayPreparationError,
    check_macos_overlay,
    load_prepared_macos_overlay,
    prepare_macos_overlay,
)


def _install_fake_toolchain(monkeypatch) -> list[list[str]]:
    commands: list[list[str]] = []

    def run(command: list[str], *, timeout: int = 10) -> str:
        del timeout
        commands.append(command)
        if command[-1:] == ["--self-test"]:
            return json.dumps({"protocol_version": 2})
        if command[:2] == ["lipo", "-archs"]:
            return overlay_build._native_architecture()
        if command[-2:] == ["--find", "swiftc"]:
            return "/fake/swiftc"
        if command == ["/fake/swiftc", "--version"]:
            return "Swift 6.0"
        if command[-1:] == ["--show-sdk-version"]:
            return "15.0"
        if "swiftc" in command and "-o" in command:
            Path(command[command.index("-o") + 1]).write_bytes(b"fake-mach-o")
            return ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(overlay_build.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(overlay_build, "_run", run)
    return commands


def test_prepare_is_atomic_cached_and_read_only_check_does_not_mutate(tmp_path, monkeypatch):
    commands = _install_fake_toolchain(monkeypatch)
    prepared = prepare_macos_overlay(tmp_path)
    assert prepared.binary.read_bytes() == b"fake-mach-o"
    assert prepared.manifest["protocol_version"] == 2
    assert prepared.manifest["renderer_protocol_version"] == 3
    pointer = tmp_path / overlay_build._pointer_name()
    before = pointer.stat().st_mtime_ns

    checked = check_macos_overlay(tmp_path)
    assert checked.available and checked.prepared == prepared
    assert pointer.stat().st_mtime_ns == before

    compile_count = sum("-o" in command for command in commands)
    assert prepare_macos_overlay(tmp_path).manifest["key"] == prepared.manifest["key"]
    assert sum("-o" in command for command in commands) == compile_count


def test_integrity_tampering_fails_closed(tmp_path, monkeypatch):
    _install_fake_toolchain(monkeypatch)
    prepared = prepare_macos_overlay(tmp_path)
    prepared.html.write_text("tampered", encoding="utf-8")
    with pytest.raises(MacOSOverlayPreparationError, match="integrity"):
        load_prepared_macos_overlay(tmp_path)
    checked = check_macos_overlay(tmp_path)
    assert checked.available is False


def test_activity_page_is_prepared_and_covered_by_the_integrity_check(tmp_path, monkeypatch):
    """The status-mode activity window loads a second page, so it is hashed like the first."""
    _install_fake_toolchain(monkeypatch)
    prepared = prepare_macos_overlay(tmp_path)
    assert prepared.activity_html.name == "navigator-activity.html"
    assert prepared.activity_html.parent == prepared.html.parent
    assert 'id="n2-activity-transcript"' in prepared.activity_html.read_text(encoding="utf-8")
    prepared.activity_html.write_text("tampered", encoding="utf-8")
    with pytest.raises(MacOSOverlayPreparationError, match="integrity"):
        load_prepared_macos_overlay(tmp_path)


def test_cache_symlink_is_rejected_before_toolchain_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(overlay_build.platform, "system", lambda: "Darwin")
    target = tmp_path / "target"
    target.mkdir()
    cache = tmp_path / "cache"
    cache.symlink_to(target, target_is_directory=True)
    with pytest.raises(MacOSOverlayPreparationError, match="regular directory"):
        prepare_macos_overlay(cache)


def test_concurrent_preparation_compiles_one_entry(tmp_path, monkeypatch):
    commands = _install_fake_toolchain(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        prepared = list(executor.map(lambda _: prepare_macos_overlay(tmp_path), range(2)))
    assert prepared[0].manifest["key"] == prepared[1].manifest["key"]
    assert sum("-o" in command for command in commands) == 1
    assert not list(tmp_path.glob(".build-*"))
    assert not list(tmp_path.glob(".lock-*"))


def test_runtime_versions_use_distinct_current_pointers():
    first = overlay_build._pointer_name({"asset": "one"})
    second = overlay_build._pointer_name({"asset": "two"})
    assert first != second
    assert first.startswith("current-") and first.endswith(".json")


def test_non_macos_check_is_read_only_and_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(overlay_build.platform, "system", lambda: "Linux")
    before = set(os.listdir(tmp_path))
    checked = check_macos_overlay(tmp_path / "missing")
    assert checked.available is False
    assert set(os.listdir(tmp_path)) == before


def test_stop_item_region_explicitly_converts_cgfloat_arithmetic_to_double():
    """Older Swift/CoreFoundation combinations cannot infer the numeric-literal overload."""
    source = overlay_build._asset_directory().joinpath("macos-overlay-host.swift").read_text(encoding="utf-8")
    region = source.split("private func stopItemRegion", 1)[1].split("private func registerStopHotKey", 1)[0]
    for key in ("x", "y", "width", "height"):
        assert f'"{key}": Double(' in region


def test_menu_bar_icon_has_a_subtle_green_activity_dot():
    source = overlay_build._asset_directory().joinpath("macos-overlay-host.swift").read_text(encoding="utf-8")
    configuration = source.split("private func configureStatusButton", 1)[1].split("private func writeJSON", 1)[0]
    assert 'string: "\\u{25CF}"' in configuration
    assert ".foregroundColor: NSColor.systemGreen" in configuration
    assert "button.setAccessibilityLabel(toolTip)" in configuration
    assert "activityDotFontPoints: CGFloat = 6" in source
    assert source.count("configureStatusButton(button, toolTip:") == 2
    assert source.count("statusItem(withLength: NSStatusItem.variableLength)") == 2


def test_activity_shell_commands_do_not_inherit_the_phosphor_glow():
    """Dense command text must stay legible instead of blurring into a green bar."""
    css = overlay_build._asset_directory().joinpath("navigator-activity.css").read_text(encoding="utf-8")
    shell_card = css.split(".n2-entry-shell {", 1)[1].split("}", 1)[0]
    shell_body = css.split(".n2-shell-body {", 1)[1].split("}", 1)[0]
    shell_command = css.split(".n2-shell-command {", 1)[1].split("}", 1)[0]
    assert "text-shadow" not in shell_card
    assert "text-shadow: none" in shell_body
    assert "flex: 1 1 auto" in shell_command


def test_activity_entries_are_not_shrunk_by_the_transcript_flex_column():
    """A shell panel clips its overflow, so only `flex: none` keeps it at its text's height.

    Without it the panel loses its automatic minimum size and the scrolling column
    squeezes it flat -- worse the shorter the window -- until only the header shows.
    """
    css = overlay_build._asset_directory().joinpath("navigator-activity.css").read_text(encoding="utf-8")
    entry = css.split(".n2-entry {", 1)[1].split("}", 1)[0]
    assert "flex: none" in entry


def test_the_loop_mark_is_painted_with_flat_colour_not_a_gradient_reference():
    """The badge's loop is stroked through `url(#yutoriNavigatorLoopGradient)` in the bundle.

    In the WKWebView host that paint server has dropped out mid-run, leaving the solid crossover
    mask as a faint dark diamond where the loop should be. The host stylesheet paints the loop's
    stroke and travelling dot directly so the mark never depends on the gradient resolving.
    """
    css = overlay_build._asset_directory().joinpath("navigator-overlay.css").read_text(encoding="utf-8")
    stroke = css.split(".yutori-loop-path,\n.yutori-loop-mask-top {", 1)[1].split("}", 1)[0]
    assert "stroke: #a8fbfc !important" in stroke
    dot = css.split(".yutori-loop-dot {", 1)[1].split("}", 1)[0]
    assert "fill: #a8fbfc !important" in dot


def test_the_shell_rail_stands_down_while_the_activity_window_is_open():
    """One list of commands at a time: the window the operator opened, not the desktop."""
    css = overlay_build._asset_directory().joinpath("navigator-overlay.css").read_text(encoding="utf-8")
    hidden = css.split("html[data-n2-activity-open] #n2-shell-rail {", 1)[1].split("}", 1)[0]
    assert "display: none" in hidden

    source = overlay_build._asset_directory().joinpath("macos-overlay-host.swift").read_text(encoding="utf-8")
    visibility = source.split("private func railVisibilityScript", 1)[1].split("}", 1)[0]
    assert "toggleAttribute('data-n2-activity-open', \\(activityShown))" in visibility
    # Both the show and the hide path, and the close button that bypasses them.
    assert source.count("        syncRailVisibility()") == 3
    # A rail page that loads while the window is already open must start hidden.
    assert "railVisibilityScript()" in source.split("private func railStyleScript", 1)[1].split("}", 1)[0]
