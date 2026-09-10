"""Tests for CuaDriver retry and uncertain-action boundaries."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from yutori.navigator.macos import transport as transport_module
from yutori.navigator.macos.transport import (
    CuaDriverConnectionError,
    CuaDriverToolError,
    CuaDriverTransport,
    CuaDriverUncertainActionError,
    find_cua_driver_binary,
)


def _running_transport() -> CuaDriverTransport:
    transport = CuaDriverTransport()
    transport._process = SimpleNamespace(returncode=None)
    return transport


def test_driver_resolution_prefers_the_pinned_package_binary_over_path(tmp_path, monkeypatch):
    package_binary = tmp_path / "package-cua-driver"
    stale_path_binary = tmp_path / "stale-cua-driver"
    package_binary.touch()
    stale_path_binary.touch()
    monkeypatch.setitem(sys.modules, "cua_driver", SimpleNamespace(get_binary_path=lambda: package_binary))
    monkeypatch.setattr(transport_module.shutil, "which", lambda _name: str(stale_path_binary))

    assert find_cua_driver_binary() == package_binary


async def test_read_only_call_restarts_and_retries_once(monkeypatch):
    transport = _running_transport()
    calls = 0
    restarts = 0

    async def call_once(_name, _arguments, *, timeout_seconds):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CuaDriverConnectionError("eof")
        return {"structuredContent": {"ok": True}}

    async def restart():
        nonlocal restarts
        restarts += 1

    monkeypatch.setattr(transport, "_call_tool_once", call_once)
    monkeypatch.setattr(transport, "_restart", restart)
    result = await transport.call_tool("get_desktop_state", {}, read_only=True)
    assert result["structuredContent"]["ok"] is True
    assert calls == 2
    assert restarts == 1


async def test_mutation_is_never_retried_after_lost_acknowledgement(monkeypatch):
    transport = _running_transport()
    calls = 0
    restarts = 0

    async def call_once(_name, _arguments, *, timeout_seconds):
        nonlocal calls
        calls += 1
        raise CuaDriverConnectionError("timeout")

    async def restart():
        nonlocal restarts
        restarts += 1

    monkeypatch.setattr(transport, "_call_tool_once", call_once)
    monkeypatch.setattr(transport, "_restart", restart)
    with pytest.raises(CuaDriverUncertainActionError, match="was not retried"):
        await transport.call_tool("click", {})
    assert calls == 1
    assert restarts == 1


async def test_tool_refusal_is_not_reclassified_as_a_connection_loss(monkeypatch):
    transport = _running_transport()

    async def call_once(_name, _arguments, *, timeout_seconds):
        raise CuaDriverToolError("permission denied")

    monkeypatch.setattr(transport, "_call_tool_once", call_once)
    with pytest.raises(CuaDriverToolError, match="permission denied"):
        await transport.call_tool("click", {})


def test_driver_resolution_prefers_the_host_binary_override(tmp_path, monkeypatch):
    host_binary = tmp_path / "host-cua-driver"
    package_binary = tmp_path / "package-cua-driver"
    host_binary.touch()
    package_binary.touch()
    monkeypatch.setitem(sys.modules, "cua_driver", SimpleNamespace(get_binary_path=lambda: package_binary))
    monkeypatch.setenv(transport_module.ENV_DRIVER_BINARY, str(host_binary))

    assert find_cua_driver_binary() == host_binary


def test_driver_resolution_rejects_a_missing_host_binary(tmp_path, monkeypatch):
    monkeypatch.setenv(transport_module.ENV_DRIVER_BINARY, str(tmp_path / "absent"))
    with pytest.raises(transport_module.CuaDriverError, match="missing cua-driver binary"):
        find_cua_driver_binary()


async def _spawn_recording_start(monkeypatch, transport: CuaDriverTransport) -> list[str]:
    """Run ``transport.start()`` against a stubbed subprocess and return the argv it spawned."""
    spawned: list[str] = []

    async def spawn(*argv: str):
        spawned.extend(argv)
        return SimpleNamespace(returncode=None, stderr=None)

    async def no_rpc(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(transport_module, "spawn_rpc_subprocess", spawn)
    monkeypatch.setattr(transport, "_request", no_rpc)
    monkeypatch.setattr(transport, "_notify", no_rpc)
    await transport.start()
    return spawned


async def test_transport_attaches_to_the_embedded_daemon_named_by_the_environment(tmp_path, monkeypatch):
    binary = tmp_path / "cua-driver"
    binary.touch()
    monkeypatch.setenv(transport_module.ENV_DRIVER_BINARY, str(binary))
    monkeypatch.setenv(transport_module.ENV_DRIVER_SOCKET, "/tmp/host.sock")

    spawned = await _spawn_recording_start(monkeypatch, CuaDriverTransport())

    assert spawned == [str(binary), "mcp", "--embedded", "--socket", "/tmp/host.sock"]


async def test_transport_explicit_arguments_override_the_environment(tmp_path, monkeypatch):
    binary = tmp_path / "cua-driver"
    binary.touch()
    monkeypatch.setenv(transport_module.ENV_DRIVER_SOCKET, "/tmp/host.sock")

    spawned = await _spawn_recording_start(monkeypatch, CuaDriverTransport(binary=binary, arguments=()))

    assert spawned == [str(binary), "mcp"]


def test_transport_spawns_a_plain_proxy_without_an_embedded_socket(monkeypatch):
    monkeypatch.delenv(transport_module.ENV_DRIVER_SOCKET, raising=False)
    assert CuaDriverTransport().arguments == []
    assert transport_module.embedded_daemon_arguments("/tmp/x.sock") == ["--embedded", "--socket", "/tmp/x.sock"]
