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
