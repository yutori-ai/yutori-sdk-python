"""Secure setup-time compilation and read-only discovery of the Swift overlay host."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..._fileutils import atomic_write_text

OVERLAY_PROTOCOL_VERSION = 2
RENDERER_PROTOCOL_VERSION = 3
OVERLAY_DEPLOYMENT_TARGET = "14.0"
_BINARY_NAME = "macos-overlay-host"
_ASSET_NAMES = (
    "macos-overlay-host.swift",
    "navigator-overlay.html",
    "navigator-overlay.css",
    "navigator-overlay.iife.js",
    "macos-overlay.js",
    "provenance.json",
)
_COMPILE_TIMEOUT_SECONDS = 60
_LOCK_STALE_SECONDS = 90
_LOCK_TIMEOUT_SECONDS = 100


class MacOSOverlayPreparationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedMacOSOverlay:
    binary: Path
    html: Path
    manifest: dict[str, Any]


@dataclass(frozen=True)
class MacOSOverlayCheck:
    available: bool
    reason: "str | None"
    prepared: "PreparedMacOSOverlay | None"


def macos_overlay_cache_directory() -> Path:
    return Path.home() / ".cache" / "yutori" / "navigator" / "overlay"


def _asset_directory() -> Path:
    return Path(__file__).with_name("assets")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _asset_hashes() -> dict[str, str]:
    directory = _asset_directory()
    hashes: dict[str, str] = {}
    for name in _ASSET_NAMES:
        path = directory / name
        if not path.is_file():
            raise MacOSOverlayPreparationError(f"macOS overlay asset is missing: {name}")
        hashes[name] = _sha256(path.read_bytes())
    return hashes


def _pointer_name(asset_hashes: "dict[str, str] | None" = None) -> str:
    """Keep each packaged runtime's pointer independent in a shared cache."""
    pointer_identity = {
        "asset_sha256": asset_hashes or _asset_hashes(),
        "protocol_version": OVERLAY_PROTOCOL_VERSION,
        "renderer_protocol_version": RENDERER_PROTOCOL_VERSION,
        "deployment_target": OVERLAY_DEPLOYMENT_TARGET,
    }
    digest = _sha256(json.dumps(pointer_identity, separators=(",", ":"), sort_keys=True).encode())
    return f"current-{digest}.json"


def _native_architecture() -> str:
    architecture = platform.machine().lower()
    if architecture == "arm64":
        return "arm64"
    if architecture in {"x86_64", "amd64"}:
        return "x86_64"
    raise MacOSOverlayPreparationError(f"Unsupported overlay architecture: {architecture}")


def _assert_secure(path: Path, kind: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise MacOSOverlayPreparationError(f"Overlay cache path is missing: {path}") from error
    expected = stat.S_ISREG(info.st_mode) if kind == "file" else stat.S_ISDIR(info.st_mode)
    if stat.S_ISLNK(info.st_mode) or not expected:
        raise MacOSOverlayPreparationError(f"Overlay cache {path} is not a regular {kind}.")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise MacOSOverlayPreparationError(f"Overlay cache {path} is owned by another user.")
    if info.st_mode & 0o022:
        raise MacOSOverlayPreparationError(f"Overlay cache {path} is writable by another user.")


def _run(command: list[str], *, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = getattr(error, "stderr", None) or str(error)
        raise MacOSOverlayPreparationError(
            f"Overlay preparation command failed: {' '.join(command)}: {detail}"
        ) from error
    return result.stdout.strip()


def _read_manifest(path: Path) -> dict[str, Any]:
    _assert_secure(path, "file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MacOSOverlayPreparationError("Overlay cache manifest is invalid.") from error
    required = {
        "protocol_version": OVERLAY_PROTOCOL_VERSION,
        "renderer_protocol_version": RENDERER_PROTOCOL_VERSION,
        "binary": _BINARY_NAME,
        "html": "navigator-overlay.html",
    }
    if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in required.items()):
        raise MacOSOverlayPreparationError("Overlay cache manifest is incompatible or incomplete.")
    if not isinstance(value.get("key"), str) or len(value["key"]) != 64:
        raise MacOSOverlayPreparationError("Overlay cache manifest has an invalid build identity.")
    return value


def _load_entry(cache_directory: Path, key: str, *, verify_packaged_assets: bool) -> PreparedMacOSOverlay:
    root = cache_directory.resolve(strict=True)
    entry = root / key
    _assert_secure(entry, "directory")
    resolved_entry = entry.resolve(strict=True)
    if resolved_entry.parent != root:
        raise MacOSOverlayPreparationError("Overlay cache entry escapes its cache directory.")
    manifest = _read_manifest(resolved_entry / "manifest.json")
    if manifest.get("key") != key or manifest.get("architecture") != _native_architecture():
        raise MacOSOverlayPreparationError("Overlay cache entry does not match this Mac.")

    expected_hashes = manifest.get("asset_sha256")
    if not isinstance(expected_hashes, dict):
        raise MacOSOverlayPreparationError("Overlay cache manifest has no asset hashes.")
    for name in (*_ASSET_NAMES, _BINARY_NAME):
        path = resolved_entry / name
        _assert_secure(path, "file")
        if _sha256(path.read_bytes()) != expected_hashes.get(name):
            raise MacOSOverlayPreparationError(f"Overlay cache integrity check failed for {name}.")
    if verify_packaged_assets and any(expected_hashes.get(name) != digest for name, digest in _asset_hashes().items()):
        raise MacOSOverlayPreparationError("Overlay cache does not match the packaged overlay assets.")
    return PreparedMacOSOverlay(
        binary=resolved_entry / _BINARY_NAME,
        html=resolved_entry / "navigator-overlay.html",
        manifest=manifest,
    )


def load_prepared_macos_overlay(cache_directory: "str | Path | None" = None) -> PreparedMacOSOverlay:
    cache = Path(cache_directory) if cache_directory is not None else macos_overlay_cache_directory()
    _assert_secure(cache, "directory")
    pointer = cache / _pointer_name()
    _assert_secure(pointer, "file")
    try:
        key = json.loads(pointer.read_text(encoding="utf-8")).get("key")
    except (OSError, json.JSONDecodeError, AttributeError) as error:
        raise MacOSOverlayPreparationError("Overlay cache pointer is invalid.") from error
    if not isinstance(key, str) or len(key) != 64:
        raise MacOSOverlayPreparationError("Overlay cache pointer is invalid.")
    return _load_entry(cache, key, verify_packaged_assets=True)


def check_macos_overlay(cache_directory: "str | Path | None" = None) -> MacOSOverlayCheck:
    """Inspect prepared assets without compiling or mutating the cache."""
    if platform.system() != "Darwin":
        return MacOSOverlayCheck(False, "The macOS overlay is available only on macOS.", None)
    try:
        prepared = load_prepared_macos_overlay(cache_directory)
    except MacOSOverlayPreparationError as error:
        return MacOSOverlayCheck(False, str(error), None)
    return MacOSOverlayCheck(True, None, prepared)


def _acquire_lock(path: Path) -> None:
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            path.mkdir(mode=0o700)
            return
        except FileExistsError:
            _assert_secure(path, "directory")
            if time.time() - path.stat().st_mtime > _LOCK_STALE_SECONDS:
                shutil.rmtree(path)
                continue
            if time.monotonic() >= deadline:
                raise MacOSOverlayPreparationError("Timed out waiting for another overlay build.")
            time.sleep(0.25)


def _remove_owned_entry(path: Path) -> None:
    if not path.exists():
        return
    _assert_secure(path, "directory")
    shutil.rmtree(path)


def prepare_macos_overlay(
    cache_directory: "str | Path | None" = None,
    *,
    rebuild: bool = False,
) -> PreparedMacOSOverlay:
    """Compile, self-test, and atomically install the bundled Swift host."""
    if platform.system() != "Darwin":
        raise MacOSOverlayPreparationError("The macOS overlay can only be prepared on macOS.")
    cache = Path(cache_directory) if cache_directory is not None else macos_overlay_cache_directory()
    if os.path.lexists(cache):
        _assert_secure(cache, "directory")
    else:
        cache.mkdir(parents=True, mode=0o700, exist_ok=True)
    _assert_secure(cache, "directory")
    cache.chmod(0o700)

    compiler = _run(["xcrun", "--sdk", "macosx", "--find", "swiftc"])
    compiler_version = _run([compiler, "--version"])
    sdk_version = _run(["xcrun", "--sdk", "macosx", "--show-sdk-version"])
    architecture = _native_architecture()
    flags = [
        "-O",
        "-framework",
        "AppKit",
        "-framework",
        "Carbon",
        "-framework",
        "CoreVideo",
        "-framework",
        "QuartzCore",
        "-framework",
        "WebKit",
        "-target",
        f"{architecture}-apple-macosx{OVERLAY_DEPLOYMENT_TARGET}",
    ]
    packaged_hashes = _asset_hashes()
    pointer = cache / _pointer_name(packaged_hashes)
    identity = {
        "asset_sha256": packaged_hashes,
        "protocol_version": OVERLAY_PROTOCOL_VERSION,
        "renderer_protocol_version": RENDERER_PROTOCOL_VERSION,
        "compiler_version": compiler_version,
        "sdk_version": sdk_version,
        "architecture": architecture,
        "deployment_target": OVERLAY_DEPLOYMENT_TARGET,
        "flags": flags,
    }
    key = _sha256(json.dumps(identity, separators=(",", ":"), sort_keys=True).encode())
    lock = cache / f".lock-{key}"
    _acquire_lock(lock)
    try:
        entry = cache / key
        if rebuild:
            _remove_owned_entry(entry)
        elif entry.exists():
            try:
                prepared = _load_entry(cache, key, verify_packaged_assets=True)
            except MacOSOverlayPreparationError:
                _remove_owned_entry(entry)
            else:
                atomic_write_text(pointer, f"{json.dumps({'key': key})}\n")
                return prepared

        temporary = Path(tempfile.mkdtemp(prefix=f".build-{key}-", dir=cache))
        temporary.chmod(0o700)
        try:
            for name in _ASSET_NAMES:
                destination = temporary / name
                shutil.copyfile(_asset_directory() / name, destination)
                destination.chmod(0o600)
            binary = temporary / _BINARY_NAME
            _run(
                ["xcrun", "--sdk", "macosx", "swiftc", str(temporary / _ASSET_NAMES[0]), *flags, "-o", str(binary)],
                timeout=_COMPILE_TIMEOUT_SECONDS,
            )
            binary.chmod(0o700)
            if architecture not in _run(["lipo", "-archs", str(binary)]).split():
                raise MacOSOverlayPreparationError(f"Overlay binary does not contain {architecture}.")
            try:
                self_test = json.loads(_run([str(binary), "--self-test"]))
            except json.JSONDecodeError as error:
                raise MacOSOverlayPreparationError("Overlay binary returned an invalid self-test.") from error
            if self_test.get("protocol_version") != OVERLAY_PROTOCOL_VERSION:
                raise MacOSOverlayPreparationError("Overlay binary failed its protocol self-test.")

            manifest = {
                **identity,
                "key": key,
                "binary": _BINARY_NAME,
                "html": "navigator-overlay.html",
                "asset_sha256": {**packaged_hashes, _BINARY_NAME: _sha256(binary.read_bytes())},
            }
            manifest_path = temporary / "manifest.json"
            manifest_path.write_text(f"{json.dumps(manifest, indent=2, sort_keys=True)}\n", encoding="utf-8")
            manifest_path.chmod(0o600)
            os.replace(temporary, entry)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        atomic_write_text(pointer, f"{json.dumps({'key': key})}\n")
        return _load_entry(cache, key, verify_packaged_assets=True)
    finally:
        if lock.exists():
            _assert_secure(lock, "directory")
            lock.rmdir()
