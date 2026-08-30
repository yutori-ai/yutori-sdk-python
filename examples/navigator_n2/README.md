# Navigator n2 with public Cua

These runnable cookbooks use the public Python SDK loop, stable model id n2, and public Cua packages. They do not import an agent package or source code from another repository. Cua provides the local Docker runtime; the adapter and n2 loop in this repository are maintained by Yutori.

The cookbook environment is separate because Cua 0.1.6 requires Python 3.12 or 3.13. The tested Cua dependencies are pinned in pyproject.toml, and the SDK source is used from this checkout.

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
pip install 'yutori[macos]==0.9.4'
uv run --extra macos python local_macos.py "Open Calculator and compute 17 * 23"
~~~

The local runtime can execute bash and file tools only because the example explicitly enables its local-shell option. Every local shell command remains confirmable even with --auto-approve.

## Disposable Linux sandbox

The sandbox entrypoint adapts public Cua mouse, keyboard, screenshot, shell, and file interfaces to `N2ComputerAgent`. It creates a disposable Linux desktop in local Docker and destroys it when the process exits. Confirm Docker Desktop or Docker Engine is running, then run:

~~~bash
docker info
uv run python remote_sandbox.py "Open Calculator and compute 17 * 23"
~~~

This cookbook intentionally does not expose Cua cloud. Cua's current cloud path uses Fleet pools and provider-owned credentials rather than the retired image-based VM API in the pinned Python package; follow [Cua's current CLI documentation](https://cua.ai/docs/reference/cua-cli/cli-reference) if you need that infrastructure.

The adapter supports the five current n2 tools: `computer_batch`, `edit`, `read`, `write`, and `bash`. It executes normalized coordinates against the sandbox's native screen, preserves a bash working directory, and uses Cua key-down/key-up primitives to keep modifiers attached to their click or scroll gesture.

## Tool sets

Both examples always send an explicit immutable tool-set id. The default alias is latest, which resolves to computer_use_tools-20260825. Use an older published set only to replay a compatible trajectory:

| Alias | Tool set |
| --- | --- |
| latest | computer_use_tools-20260825 |
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
uv run python remote_sandbox.py --tool-set gui "Open Calculator and compute 17 * 23"
~~~

The public n2 reference documents the current five tools, all 15 batch action types, the 20-action batch limit, screenshot compression, and the single-result-per-tool-call rule: https://docs.yutori.com/reference/n2
