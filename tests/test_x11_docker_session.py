"""Characterization tests for the direct-X11 Docker cookbook's container entrypoint.

``x11_docker_session.py`` runs only inside the ``Dockerfile.direct_x11`` image (see its
``ENTRYPOINT``), so these tests exercise the polling helper directly rather than booting a
real X server.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from examples.navigator_n2 import x11_docker_session


def _fake_process(*, alive: bool, returncode: int | None = None) -> Any:
    return SimpleNamespace(poll=lambda: None if alive else returncode, returncode=returncode)


def test_wait_until_ready_returns_as_soon_as_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(x11_docker_session.time, "sleep", lambda seconds: calls.append(seconds))
    attempts = iter([False, False, True])

    x11_docker_session._wait_until_ready(lambda: next(attempts), name="thing")

    assert calls == [x11_docker_session._READY_INTERVAL_SECONDS] * 2


def test_wait_until_ready_raises_after_exhausting_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls = 0

    def fake_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1

    monkeypatch.setattr(x11_docker_session.time, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="thing did not become ready"):
        x11_docker_session._wait_until_ready(lambda: False, name="thing")

    assert sleep_calls == x11_docker_session._READY_ATTEMPTS


def test_wait_until_ready_reports_an_early_process_exit_without_waiting_out_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(x11_docker_session.time, "sleep", lambda _seconds: pytest.fail("should not sleep"))
    process = _fake_process(alive=False, returncode=17)

    with pytest.raises(RuntimeError, match="thing exited with status 17"):
        x11_docker_session._wait_until_ready(lambda: False, name="thing", process=process)


def test_wait_until_ready_ignores_process_liveness_when_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(x11_docker_session.time, "sleep", lambda _seconds: None)

    # No `process=` passed: readiness alone decides, matching `_wait_for_display`'s contract.
    x11_docker_session._wait_until_ready(lambda: True, name="thing")


def test_wait_for_port_checks_process_liveness_before_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(x11_docker_session.time, "sleep", lambda _seconds: pytest.fail("should not sleep"))
    process = _fake_process(alive=False, returncode=9)

    with pytest.raises(RuntimeError, match="x11vnc exited with status 9"):
        x11_docker_session._wait_for_port(5900, process, "x11vnc")


def test_wait_for_window_manager_checks_process_liveness_before_xprop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(x11_docker_session.time, "sleep", lambda _seconds: pytest.fail("should not sleep"))
    process = _fake_process(alive=False, returncode=3)

    with pytest.raises(RuntimeError, match="Openbox exited with status 3"):
        x11_docker_session._wait_for_window_manager({}, process)
