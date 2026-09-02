"""Tests for the unhide-without-activate helper behind window scope."""

from __future__ import annotations

import pytest

import yutori.navigator.macos.visibility as visibility

_ASN = "ASN:0x0-0x39c39c:"


def _fake_run(responses: dict[str, "str | None"]):
    calls: list[tuple[str, ...]] = []

    async def run(*command: str) -> "str | None":
        calls.append(command)
        if command[0].endswith("osascript"):
            return responses["osascript"]
        if command[1] == "find":
            return responses["find"]
        return responses["info"]

    run.calls = calls  # type: ignore[attr-defined]
    return run


async def test_unhide_application_requests_appkit_and_confirms_through_launchservices(monkeypatch):
    run = _fake_run({"osascript": "requested\n", "find": _ASN + "\n", "info": '"Calculator" ASN:0x0-0x39c39c: \n'})
    monkeypatch.setattr(visibility, "_run", run)
    assert await visibility.unhide_application(4242) is True
    script = run.calls[0]
    assert script[:4] == ("/usr/bin/osascript", "-l", "JavaScript", "-e")
    assert "runningApplicationWithProcessIdentifier(4242)" in script[4] and "app.unhide;" in script[4]
    assert run.calls[1] == ("lsappinfo", "find", "pid=4242")
    assert run.calls[2] == ("lsappinfo", "info", "-only", "hidden", _ASN)


@pytest.mark.parametrize(
    "output,expected",
    [
        ("[ NULL ]  [ NULL ]  (hidden) \n", True),
        ("[ NULL ]  [ NULL ]  \n", False),
        ('"Calculator" ASN:0x0-0x39c39c: \n    hidden = true\n', True),
        ('"Calculator" ASN:0x0-0x39c39c: \n    hidden = false\n', False),
        ('"Calculator" ASN:0x0-0x39c39c: \n    hidden = "1"\n', True),
        ("", None),
        ("   \n", None),
    ],
)
def test_parse_lsappinfo_hidden_reads_the_flag_and_key_value_layouts(output, expected):
    assert visibility.parse_lsappinfo_hidden(output) is expected


@pytest.mark.parametrize("info", ["[ NULL ]  [ NULL ]  (hidden) ", '"Calculator" ASN:0x0-0x39c39c: \n hidden = true'])
async def test_unhide_application_reports_false_when_the_app_stays_hidden(monkeypatch, info):
    run = _fake_run({"osascript": "requested", "find": _ASN, "info": info})
    monkeypatch.setattr(visibility, "_run", run)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(visibility.asyncio, "sleep", no_sleep)
    assert await visibility.unhide_application(4242) is False
    assert sum(1 for call in run.calls if call[1:2] == ("info",)) == visibility._VISIBILITY_POLLS


@pytest.mark.parametrize("osascript", [None, "missing", "execution error"])
async def test_unhide_application_fails_soft_when_appkit_cannot_be_reached(monkeypatch, osascript):
    run = _fake_run({"osascript": osascript, "find": _ASN, "info": ""})
    monkeypatch.setattr(visibility, "_run", run)
    assert await visibility.unhide_application(4242) is False
    assert len(run.calls) == 1


async def test_application_hidden_parses_lsappinfo(monkeypatch):
    monkeypatch.setattr(visibility, "_run", _fake_run({"osascript": "", "find": _ASN, "info": "(hidden)"}))
    assert await visibility.application_hidden(1) is True
    monkeypatch.setattr(visibility, "_run", _fake_run({"osascript": "", "find": "", "info": ""}))
    assert await visibility.application_hidden(1) is None
