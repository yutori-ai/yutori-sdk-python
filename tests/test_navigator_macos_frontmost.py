"""Tests for the LaunchServices frontmost-application probe."""

from __future__ import annotations

import asyncio

import pytest

import yutori.navigator.macos.frontmost as frontmost_module
from yutori.navigator.macos.frontmost import FrontmostApp, frontmost_app, parse_lsappinfo_info

_INFO_OUTPUT = """"Google Chrome" ASN:0x0-0x2ec2ec: (in front)
    bundleID=[ NULL ]
    bundle path=[ NULL ]
    executable path=[ NULL ]
    pid = 88740 !cgsConnection !signalled type=[ NULL ]  flavor=[ NULL ]  Version=[ NULL ]  Arch=!!none
"""


def test_parse_lsappinfo_info_reads_pid_and_name():
    assert parse_lsappinfo_info(_INFO_OUTPUT) == FrontmostApp(pid=88740, name="Google Chrome")


def test_parse_lsappinfo_info_tolerates_a_missing_name():
    assert parse_lsappinfo_info("    pid = 42 !cgsConnection\n") == FrontmostApp(pid=42, name=None)


def test_parse_lsappinfo_info_returns_none_without_a_pid():
    assert parse_lsappinfo_info('"Finder" ASN:0x0-0x1: (in front)\n') is None


def test_describe_falls_back_to_the_pid():
    assert FrontmostApp(7, "Notes").describe() == "Notes (pid 7)"
    assert FrontmostApp(7).describe() == "pid 7"


async def test_frontmost_app_chains_the_two_lsappinfo_calls(monkeypatch):
    calls: list[tuple[str, ...]] = []

    async def fake_run(*command: str) -> str | None:
        calls.append(command)
        if command[1] == "front":
            return "ASN:0x0-0x2ec2ec:\n"
        return _INFO_OUTPUT

    monkeypatch.setattr(frontmost_module, "_run", fake_run)
    assert await frontmost_app() == FrontmostApp(pid=88740, name="Google Chrome")
    assert calls == [
        ("lsappinfo", "front"),
        ("lsappinfo", "info", "-only", "pid", "-only", "name", "ASN:0x0-0x2ec2ec:"),
    ]


async def test_frontmost_app_fails_open_when_launchservices_is_unavailable(monkeypatch):
    async def fake_run(*command: str) -> str | None:
        return None

    monkeypatch.setattr(frontmost_module, "_run", fake_run)
    assert await frontmost_app() is None


async def test_frontmost_app_rejects_an_unexpected_front_payload(monkeypatch):
    async def fake_run(*command: str) -> str | None:
        return "No front application\n"

    monkeypatch.setattr(frontmost_module, "_run", fake_run)
    assert await frontmost_app() is None


class HungProcess:
    def __init__(self):
        self.returncode = None
        self.killed = False
        self.waited = False

    async def communicate(self):
        await asyncio.sleep(60)

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        self.waited = True
        return self.returncode


async def test_timed_out_probe_kills_and_reaps_the_child_then_fails_open(monkeypatch):
    process = HungProcess()

    async def fake_exec(*_command, **_kwargs):
        return process

    monkeypatch.setattr(frontmost_module, "_LSAPPINFO_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(frontmost_module.asyncio, "create_subprocess_exec", fake_exec)
    assert await frontmost_app() is None
    assert process.killed and process.waited


async def test_cancelled_probe_kills_and_reaps_the_child(monkeypatch):
    process = HungProcess()

    async def fake_exec(*_command, **_kwargs):
        return process

    monkeypatch.setattr(frontmost_module.asyncio, "create_subprocess_exec", fake_exec)
    task = asyncio.create_task(frontmost_module._run("lsappinfo", "front"))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed and process.waited
