"""Run stable Navigator n2 against a disposable X11 desktop in local Docker."""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from yutori.auth import get_auth_status, require_api_key

try:
    from .shared import parse_common_args, selected_tool_set
except ImportError:
    from shared import parse_common_args, selected_tool_set


IMAGE = "yutori-n2-direct-x11:local"
CONTAINER_CONFIG_PATH = "/root/.yutori/config.json"
CONTAINER_WORKDIR = "/work"
CONTAINER_NOVNC_PORT = 6080


def _authentication_args() -> list[str]:
    """Forward the normal Yutori credential source without putting a key in argv."""
    status = get_auth_status()
    if status.source == "env_var":
        return ["--env", "YUTORI_API_KEY"]
    if status.source == "config_file" and status.config_path:
        return [
            "--mount",
            f"type=bind,source={status.config_path},target={CONTAINER_CONFIG_PATH},readonly",
        ]
    if not status.authenticated:
        require_api_key()  # Raise the SDK's standard missing-credential error.
    raise RuntimeError(f"unsupported Yutori authentication source: {status.source!r}")


def _run_docker(command: list[str], **kwargs: object) -> "subprocess.CompletedProcess[bytes]":
    """Run a docker subprocess, translating a missing binary into a clear error."""
    try:
        return subprocess.run(command, **kwargs)
    except FileNotFoundError as error:
        raise SystemExit("docker is not installed") from error


def _run_checked(command: list[str], description: str, **kwargs: object) -> None:
    result = _run_docker(command, **kwargs)
    if result.returncode != 0:
        raise SystemExit(f"{description} failed with exit code {result.returncode}")


def _available_local_port() -> int:
    """Ask the OS for an unused loopback port for Docker's noVNC mapping."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def main(args: argparse.Namespace) -> None:
    # Reject missing credentials and invalid model inputs before Docker does work.
    authentication_args = _authentication_args()
    selected_tool_set(args.tool_set)

    if shutil.which("docker") is None:
        raise SystemExit("docker is not installed")
    _run_checked(
        ["docker", "info"],
        "Docker availability check",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    example_dir = Path(__file__).resolve().parent
    repo_root = example_dir.parents[1]
    dockerfile = example_dir / "Dockerfile.direct_x11"
    _run_checked(
        [
            "docker",
            "build",
            "--file",
            str(dockerfile),
            "--tag",
            IMAGE,
            str(example_dir),
        ],
        "Direct X11 image build",
    )

    local_x11_command = [
        "python",
        "/sdk/examples/navigator_n2/local_x11.py",
        args.task,
        "--tool-set",
        args.tool_set,
        "--max-steps",
        str(args.max_steps),
        "--workspace",
        CONTAINER_WORKDIR,
    ]
    if args.auto_approve:
        local_x11_command.extend(["--auto-approve", "--auto-approve-shell"])

    novnc_port = _available_local_port()
    docker_command = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--interactive",
        "--publish",
        f"127.0.0.1:{novnc_port}:{CONTAINER_NOVNC_PORT}",
    ]
    if sys.stdin.isatty() and sys.stdout.isatty():
        docker_command.append("--tty")
    docker_command.extend(
        [
            *authentication_args,
            "--mount",
            f"type=bind,source={repo_root},target=/sdk,readonly",
            "--workdir",
            CONTAINER_WORKDIR,
            "--env",
            "PYTHONPATH=/sdk",
            IMAGE,
            *local_x11_command,
        ]
    )

    print(f"Watch the desktop live: http://localhost:{novnc_port}/vnc.html", flush=True)
    result = _run_docker(docker_command)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return parse_common_args(__doc__, argv, auto_approve_default=True)


if __name__ == "__main__":
    try:
        main(parse_args())
    except KeyboardInterrupt:
        print("Interrupted.")
