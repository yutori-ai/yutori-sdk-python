"""Boot the image's virtual X11 desktop, then run the requested command."""

from __future__ import annotations

import contextlib
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

VNC_PORT = 5900
NOVNC_PORT = 6080
_READY_ATTEMPTS = 40
_READY_INTERVAL_SECONDS = 0.25


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def _start(command: list[str], environment: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _wait_until_ready(
    is_ready: Callable[[], bool],
    *,
    name: str,
    process: subprocess.Popen[bytes] | None = None,
) -> None:
    """Poll ``is_ready`` for up to ``_READY_ATTEMPTS`` tries, ``_READY_INTERVAL_SECONDS`` apart.

    When ``process`` is given, an early exit is reported immediately instead of being
    retried until the attempt budget runs out.
    """
    for _ in range(_READY_ATTEMPTS):
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"{name} exited with status {process.returncode}")
        if is_ready():
            return
        time.sleep(_READY_INTERVAL_SECONDS)
    raise RuntimeError(f"{name} did not become ready")


def _wait_for_display(environment: dict[str, str]) -> None:
    def is_ready() -> bool:
        result = subprocess.run(
            ["xdpyinfo", "-display", environment["DISPLAY"]],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    _wait_until_ready(is_ready, name="Xvfb")


def _wait_for_port(port: int, process: subprocess.Popen[bytes], name: str) -> None:
    def is_ready() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=_READY_INTERVAL_SECONDS):
                return True
        except OSError:
            return False

    _wait_until_ready(is_ready, name=name, process=process)


def _wait_for_window_manager(environment: dict[str, str], process: subprocess.Popen[bytes]) -> None:
    def is_ready() -> bool:
        result = subprocess.run(
            ["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0 and b"_NET_SUPPORTING_WM_CHECK(WINDOW)" in result.stdout

    _wait_until_ready(is_ready, name="Openbox", process=process)


def main(command: list[str]) -> int:
    if not command:
        raise SystemExit("the X11 container needs a command to run")

    environment = os.environ.copy()
    environment.update(DISPLAY=":99", XDG_SESSION_TYPE="x11", XAUTHORITY="/tmp/direct-x11.Xauthority")
    authority_path = Path(environment["XAUTHORITY"])
    authority_path.touch(mode=0o600)
    subprocess.run(
        [
            "xauth",
            "-f",
            str(authority_path),
            "add",
            environment["DISPLAY"],
            "MIT-MAGIC-COOKIE-1",
            secrets.token_hex(16),
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )

    processes: list[subprocess.Popen[bytes]] = []
    try:
        processes.append(
            _start(
                [
                    "Xvfb",
                    environment["DISPLAY"],
                    "-screen",
                    "0",
                    "1280x720x24",
                    "-auth",
                    str(authority_path),
                    "-nolisten",
                    "tcp",
                ],
                environment,
            )
        )
        _wait_for_display(environment)
        vnc = _start(
            [
                "x11vnc",
                "-display",
                environment["DISPLAY"],
                "-auth",
                str(authority_path),
                "-rfbport",
                str(VNC_PORT),
                "-localhost",
                "-forever",
                "-shared",
                "-viewonly",
                "-nopw",
            ],
            environment,
        )
        processes.append(vnc)
        _wait_for_port(VNC_PORT, vnc, "x11vnc")
        novnc = _start(
            [
                "websockify",
                "--web=/usr/share/novnc",
                str(NOVNC_PORT),
                f"localhost:{VNC_PORT}",
            ],
            environment,
        )
        processes.append(novnc)
        _wait_for_port(NOVNC_PORT, novnc, "noVNC")
        openbox = _start(["openbox"], environment)
        processes.append(openbox)
        _wait_for_window_manager(environment, openbox)
        return subprocess.run(command, env=environment).returncode
    finally:
        for process in reversed(processes):
            _stop(process)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
