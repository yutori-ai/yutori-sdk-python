# Navigator n2 with public Cua

These runnable cookbooks use the public Python SDK loop, stable model id n2, and the public `cua-sandbox` package. They do not import an agent package or source code from another repository. Cua provides the local Docker runtime; the adapter and n2 loop in this repository are maintained by Yutori.

**Which file should I copy for my own environment?** If your code can run on (or inside) the machine it drives, start from [linux_adapter.py](linux_adapter.py) (in-process, no vendor in the chain). If your desktops sit behind a remote API — a sandbox provider or your own fleet service — start from [cua_adapter.py](cua_adapter.py) and swap its `self.sandbox.*` calls for your API's. The tool contracts are identical either way.

The cookbook environment is separate because `cua-sandbox` requires Python 3.11–3.13. The tested dependency is pinned in pyproject.toml, and the SDK source is used from this checkout.

On minimal Debian or Ubuntu images (including `python:3.12-slim`), install the compiler and Linux input headers that Cua's `evdev` dependency builds against:

~~~bash
apt-get update && apt-get install -y --no-install-recommends build-essential linux-libc-dev
~~~

~~~bash
cd examples/navigator_n2
uv sync --python 3.12
~~~

Authenticate Yutori in this environment, or set `YUTORI_API_KEY`:

~~~bash
yutori auth login
~~~

No Cua cloud credential is used. The Yutori key remains on the host and is never copied into the container.

## Local macOS

The local entrypoint uses yutori.navigator.macos.MacOSComputer, the public SDK runtime that yutori-mcp uses. Install the SDK macOS extra, grant Accessibility and Screen Recording to CuaDriver.app in an unlocked GUI session, and then run it:

~~~bash
pip install 'yutori[macos]==0.9.8'
uv run --extra macos python local_macos.py "Open Calculator and compute 17 * 23"
~~~

The local runtime can execute bash and file tools only because the example explicitly enables its local-shell option. Every local shell command remains confirmable even with --auto-approve.

## Local Linux (X11)

The Linux entrypoint is vendor-free: `linux_adapter.py` drives the desktop `$DISPLAY` points at directly — pyautogui for input (X11 wheel notches, key events, and drags are the native units n2's actions map onto), mss for screenshots, and local subprocesses for `bash` and the file tools. **X11 only**: on a Wayland session synthetic input fails or half-works through XWayland, so use an "on Xorg" session or a virtual display (Xvfb/x11vnc). This acts on a real machine, not a disposable sandbox: prefer a dedicated VM or virtual display, and shell commands stay confirmable even with `--auto-approve`.

~~~bash
uv sync --extra linux --python 3.12
uv run --extra linux python local_linux.py "Open the calculator and compute 17 * 23"
~~~

Non-ASCII text falls back to clipboard paste, which needs `xclip` installed in the session.

## Disposable Linux sandbox

The sandbox entrypoint adapts public Cua mouse, keyboard, screenshot, shell, and file interfaces to `N2ComputerAgent`. It creates a disposable Linux desktop in local Docker and destroys it when the process exits. Confirm Docker Desktop or Docker Engine is running, then run:

~~~bash
docker info
uv run python local_docker.py "Open Calculator and compute 17 * 23"
~~~

The script prints a `Watch the desktop live: http://localhost:<port>/vnc.html` link at startup — open it in a browser to follow the agent's actions on the sandbox's noVNC viewer. If a run hits `--max-steps`, the entrypoints take one summarize-only turn (no tool execution) and print the model's summary of progress before exiting.

The current sandbox image has no Calculator desktop entry, although `xcalc` is available. In the suggested task, n2 moves between the GUI and bash to confirm the missing launcher, discover and launch `xcalc`, and then complete the calculation visually.

This cookbook intentionally does not expose Cua cloud. Cua's current cloud path uses Fleet pools and provider-owned credentials rather than the retired image-based VM API in the pinned Python package; follow [Cua's current CLI documentation](https://cua.ai/docs/reference/cua-cli/cli-reference) if you need that infrastructure.

The adapter supports the five current n2 tools: `computer_batch`, `edit`, `read`, `write`, and `bash`. It executes normalized coordinates against the sandbox's native screen, preserves a bash working directory, and uses Cua key-down/key-up primitives to keep modifiers attached to their click or scroll gesture.

## Tool sets

Both examples always send an explicit immutable tool-set id. The default alias is latest, which resolves to computer_use_tools-20260830. Use an older published set only to replay a compatible trajectory:

| Alias | Tool set |
| --- | --- |
| latest | computer_use_tools-20260830 |
| batch-files | computer_use_tools-20260825 |
| full-batch | computer_use_tools-20260822 |
| screenshot-batch | computer_use_tools-20260821 |
| modifiers-batch | computer_use_tools-20260815 |
| bash-batch | computer_use_tools-20260812 |
| files-batch | computer_use_tools-20260808 |
| files | computer_use_tools-20260807 |
| hybrid-batch | computer_use_tools-20260729 |
| hybrid | computer_use_tools-20260728 |
| gui-batch | computer_use_tools-20260716 |
| gui | computer_use_tools-20260708 |

~~~bash
uv run python local_docker.py --tool-set gui "Open Calculator and compute 17 * 23"
~~~

The public n2 reference documents the current five tools, all 15 batch action types, the 20-action batch limit, screenshot compression, and the single-result-per-tool-call rule: https://docs.yutori.com/reference/n2
