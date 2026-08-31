# Yutori Python SDK & CLI

[![PyPI version](https://img.shields.io/pypi/v/yutori.svg)](https://pypi.org/project/yutori/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

The official Python SDK and CLI for the [Yutori API](https://docs.yutori.com) — build agents that monitor, research, and browse the web, and operate computers with [Yutori](https://yutori.com/api).

The SDK offers sync and async clients with full type annotations, plus a `yutori` CLI for authentication and managing resources from the terminal.

## AI agent install (recommended)

Paste this into Claude Code, Codex, Cursor, Windsurf, or another coding agent:

```text
Use https://yutori.com/api/llms.txt and set up Yutori for me.
```

## Manual install

On macOS or Linux, the recommended setup is the one-line installer:

```bash
curl -fsSL https://yutori.com/install.sh | bash
```

Installs the global `yutori` CLI via `uv tool install` and prompts to add the SDK to your project, run `yutori auth login`, register the MCP server, install workflow skills, and verify with a browsing task.

Python 3.9+ is required for the SDK.

<details>
<summary>Non-interactive install (CI, pipe, AI coding agent)</summary>

The SDK install, auth, and verification steps are skipped — auth needs a browser, verification needs an API key. MCP server and workflow skills install automatically without prompts.

To scope the MCP install to one coding agent, set `YUTORI_INSTALL_CLIENT=<slug>` (e.g. `claude-code`, `codex`, `cursor`). Unset, it registers for `claude-code`, `codex`, `cursor`, and `gemini-cli`. Run `npx add-mcp list-agents` for the full slug list.

</details>

<details>
<summary>Uninstall the CLI later</summary>

```bash
curl -fsSL https://yutori.com/uninstall.sh | bash
```

Removes the global `yutori` CLI. Saved credentials at `~/.yutori/` are left in place so they survive reinstalls — `rm -rf ~/.yutori` manually if you want a clean slate. Set `YUTORI_UNINSTALL_ASSUME_YES=1` for scripted runs.

</details>

<details>
<summary>Install the package manually</summary>

```bash
pip install yutori
```

Or add it to an existing project with uv:

```bash
uv add yutori
```

</details>

<details>
<summary>Authenticate manually</summary>

Run this once to save your API key:

```bash
yutori auth login
```

This opens your browser to log in with your Yutori account and saves an API key to `~/.yutori/config.json`. The SDK and CLI automatically pick it up.

If you installed the package with `uv add`, run `uv run yutori auth login` instead.

Or use an env var / pass the key explicitly:

```python
from yutori import YutoriClient

client = YutoriClient()                  # Uses saved credentials or YUTORI_API_KEY
client = YutoriClient(api_key="yt-...")  # Or pass explicitly
```

Resolution order: explicit `api_key` > `YUTORI_API_KEY` env var > `~/.yutori/config.json`.

</details>

<details>
<summary>Configure MCP server and skills manually</summary>

The installer sets these up automatically when Node.js is available. To do it manually:

```bash
npx add-mcp -n yutori "uvx yutori-mcp"
npx -y skills add yutori-ai/yutori-mcp -g -y -a claude-code
```

The first command registers the Yutori MCP server with your editor. The second installs workflow skills scoped to your client — swap `-a claude-code` for another slug if needed (the skills CLI needs `git` on PATH).

</details>


## API Overview

The Yutori API provides four main capabilities:


| API           | Description                                                    | SDK Namespace     |
| ------------- | -------------------------------------------------------------- | ----------------- |
| **Navigator** | Browser- and computer-use models (Navigator n1.5, n2)         | `client.chat`  |
| **Browsing**  | One-time browser automation tasks                              | `client.browsing` |
| **Research**  | Deep web research using 100+ tools                             | `client.research` |
| **Scouting**  | Continuous web monitoring on a schedule                        | `client.scouts`   |


## Navigator API

The Navigator API serves Yutori's models: **Navigator n1.5** operates a webpage in a browser and **Navigator n2** operates a complete desktop. Both take a task and a screenshot and return the next actions as `tool_calls`; your code executes them, sends the results back, and calls the model again until it indicates stop. The endpoint follows the OpenAI Chat Completions interface, so `client.chat` is a drop-in OpenAI-compatible client.

### Navigator n1.5 (browser use)

Capture a screenshot of the page and send it with the task:

```python
from yutori import AsyncYutoriClient
from yutori.navigator import aplaywright_screenshot_to_data_url
from playwright.async_api import async_playwright

async with AsyncYutoriClient() as client, async_playwright() as p:
    browser = await p.chromium.launch()
    page = await browser.new_page()
    await page.goto("https://www.yutori.com")

    image_url = await aplaywright_screenshot_to_data_url(page)

    response = await client.chat.completions.create(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "List the team member names."},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
    )

    message = response.choices[0].message
    print(message.content)  # Model's thoughts
    for tool_call in message.tool_calls or []:
        # Execute the requested browser action on `page`, append the tool
        # result to the conversation, capture a fresh screenshot, and call
        # the model again...
        ...
```

This snippet shows a single model call. In practice, you'll run an agent loop: execute the returned actions on the page, capture a fresh screenshot, and call the model again until it returns text with no `tool_calls`. The complete agent loop lives in [examples/navigator_n1_5.py](examples/navigator_n1_5.py), with custom-tool variants alongside it.

<details>
<summary>References and request options</summary>

The SDK defaults to Navigator n1.5 (`n1.5-latest`). Navigator n1.5 requests support selectable tool sets, `disable_tools`, and structured JSON output via `json_schema` (returned as `response.parsed_json`). See the [Navigator reference](https://docs.yutori.com/reference/navigator) for model IDs, parameters, and the full action space.

</details>

If you'd rather not manage browser infrastructure, use the **Browsing API** below, which runs the Navigator n1.5 on Yutori's cloud browser.

### Navigator n2 (computer use)

Navigator n2 operates a full desktop. It produces `computer_batch` calls — an ordered sequence of GUI actions — plus `bash` and file-tool (`read`/`write`/`edit`) calls. You implement the computer environment; the SDK's agent loop calls it to execute each action and sends the results back to the model:

```python
from yutori import AsyncYutoriClient
from yutori.navigator import N2ComputerAgent

# Implement the async screenshot and input methods for your environment.
computer = MyComputer(...)

async with AsyncYutoriClient() as client:
    agent = N2ComputerAgent(
        computer=computer,
        completions=client.chat.completions,
    )

    async for step in agent.run("Open Calculator and compute 17 * 23."):
        ...  # each step yields the model's messages, tool calls, and tool results
```

Your `MyComputer` environment is responsible for tool implementations (they are system-dependent); the `N2ComputerAgent` harness is responsible for tool transformation and batching:

| Tool | Implemented by | What you write / reuse |
|---|---|---|
| `computer_batch` | SDK's `N2ComputerAgent` | the single-action GUI primitives the batch executes through (`click`, `type`, etc) — reference: [cua_adapter.py](examples/navigator_n2/cua_adapter.py) |
| `bash` | your `MyComputer` | `run_bash_command` — reference: [cua_adapter.py](examples/navigator_n2/cua_adapter.py) |
| `read`/`write`/`edit` | your `MyComputer` | inherit the SDK's [`ShellFileToolsMixin`](yutori/navigator/sandbox_tools.py), which implements all three over your sandbox's shell — or implement them natively, as [MacOSComputer](yutori/navigator/macos/computer.py) does |

The full contract is documented in [Navigator n2 loop](api.md#navigator-n2-loop).

#### Run in local Docker (Cua cookbook)

The [Cua cookbook](examples/navigator_n2/README.md) instantiates this agent loop in a local Docker container:

```bash
yutori auth login            # or export YUTORI_API_KEY=...

cd examples/navigator_n2
uv sync --python 3.12
uv run python remote_sandbox.py --auto-approve "Open Calculator and compute 17 * 23"
```

The script prints a `Watch the desktop live:` URL at startup — open it in a browser to follow along.

#### Run on a Daytona Linux VM

[examples/navigator_n2_daytona.py](examples/navigator_n2_daytona.py) runs the same agent on a [Daytona](https://www.daytona.io) Linux VM. Create an API key at [app.daytona.io](https://app.daytona.io), then:

```bash
yutori auth login            # or export YUTORI_API_KEY=...
export DAYTONA_API_KEY=...   # https://app.daytona.io

uv run https://raw.githubusercontent.com/yutori-ai/yutori-sdk-python/main/examples/navigator_n2_daytona.py \
    "Find the OS version and free disk space of this machine, and save a summary to a file on the desktop"
```

See [Run n2 on Daytona](https://docs.yutori.com/reference/n2-daytona) for a full walkthrough.

<details>
<summary>Drive your own local Mac</summary>

[Yutori MCP](https://github.com/yutori-ai/yutori-mcp) ships the local harness, built on the same `N2ComputerAgent`:

```bash
uvx yutori-mcp computer-use setup
uvx yutori-mcp computer-use run "In Calculator, compute 17 * 23 and report the result." --app Calculator
```

</details>

<details>
<summary>References, trained conventions, and long-run behavior</summary>

See the [Navigator n2 reference](https://docs.yutori.com/reference/n2) for the tools, actions, and coordinate system, and the [API reference](api.md#navigator-n2) for direct `client.chat.completions.create(...)` calls. A few conventions the model is trained around — the `bash` tool rather than a GUI terminal, image-returning reads, all-or-nothing tool sets — are covered in the [API reference](api.md#navigator-n2-loop), and the SDK ships the reference file-tool implementation (`ShellFileToolsMixin`) for any sandbox with a shell. Long runs are compacted automatically once the context grows; pass `compactor=None` to disable, and the run instead stops cleanly at the model's 128k context limit.

</details>

<details>
<summary>Agent-loop helpers</summary>

The `yutori.navigator` subpackage exposes optional helpers for typical agent loops:


| Helper                                                      | Purpose                                                                                                                        |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `aplaywright_screenshot_to_data_url(page)`                  | Capture a Playwright screenshot as a Navigator-optimized WebP data URL.                                                                  |
| `denormalize_coordinates(coords, width, height)`            | Map the Navigator 1000×1000 coordinate space to viewport pixels.                                                                         |
| `format_task_with_context(task, ...)`                       | Append location, timezone, and current date to a task message.                                                                           |
| `format_stop_and_summarize(task)`                           | Ask the model to summarize when hitting max steps or an error.                                                                           |
| `trimmed_messages_to_fit(messages, max_bytes, keep_recent)` | Drop older screenshots to stay under the API size limit.                                                                                 |
| `map_key_to_playwright(key)` / `map_keys_individual(keys)`  | Convert Navigator n1.5 lowercase key names to Playwright format.                                                                         |
| `yutori.navigator.tools`                                    | Packaged JS reference implementations for Navigator n1.5 browser tool sets (`extract_elements`, `find`, `set_element_value`, `execute_js`). |
| `N2ComputerAgent` / `TOOL_SET_COMPUTER_USE_LATEST`          | The stable Navigator n2 agent loop and current computer-use tool set.                                                       |
| `N2InlineCompactor` / `N2Compactor`                         | Default-on context compaction for long n2 trajectories (pass `compactor=None` to disable), and the protocol for a custom history rewrite policy. |


Full helper reference: [api.md](api.md).

</details>

## Browsing API

Run one-time browser automation tasks on Yutori's cloud browser (or on Yutori Local with the user's logged-in desktop sessions):

```python
task = client.browsing.create(
    task="Give me a list of all employees (names and titles) of Yutori.",
    start_url="https://yutori.com",
)

# Poll for completion
import time
while True:
    result = client.browsing.get(task["task_id"])
    if result["status"] in ("succeeded", "failed"):
        break
    time.sleep(5)

print(result)
```

Common options: `require_auth=True` for login flows, `browser="local"` for Yutori Local, `webhook_url=...` for async completion notifications. Failed tasks may include a `rejection_reason`.

`client.browsing.list()` enumerates your browsing tasks — omit `limit` to get them all, or pass `status` (`running`/`succeeded`/`failed`) and `cursor` to filter and paginate.

### Structured output

Define the output structure with a JSON Schema dict or a Pydantic model:

```python
from pydantic import BaseModel  # optional dependency

class Employee(BaseModel):
    name: str
    title: str

task = client.browsing.create(
    task="Give me a list of all employees (names and titles) of Yutori.",
    start_url="https://yutori.com",
    output_schema=Employee,  # Auto-converted to JSON Schema
    webhook_url="https://example.com/webhook",
)
```

The same `output_schema` pattern applies to `client.research.create` and `client.scouts.create`.

## Research API

Perform deep web research using 100+ MCP tools (search engines, APIs, data sources):

```python
task = client.research.create(
    query="What are the latest developments in quantum computing from the past week?",
    user_timezone="America/Los_Angeles",
)

# Poll for results
while True:
    result = client.research.get(task["task_id"])
    if result["status"] in ("succeeded", "failed"):
        break
    time.sleep(5)
```

Failed tasks may include a `rejection_reason`.

`client.research.list()` enumerates your research tasks — handy for exporting or recovering task IDs from a large batch. Omit `limit` to get them all, or pass `status` / `cursor` to filter and paginate:

```python
completed = client.research.list(status="succeeded")
for t in completed["tasks"]:
    print(t["task_id"], t["created_at"])
```

## Scouting API

Scouts run on a schedule to monitor the web and notify you when relevant updates occur:

```python
scout = client.scouts.create(
    query="News, product updates, and announcements about Yutori AI",
    output_interval=86400,  # Daily (seconds, min 1800)
    webhook_url="https://example.com/webhook",
)

# Manage scouts
scouts = client.scouts.list(status="active")
client.scouts.update(scout["id"], status="paused")
client.scouts.update(scout["id"], status="active")
updates = client.scouts.get_updates(scout["id"], limit=20)
client.scouts.delete(scout["id"])
```

## Async Usage

`AsyncYutoriClient` mirrors `YutoriClient` with `async` methods:

```python
import asyncio
from yutori import AsyncYutoriClient

async def main():
    async with AsyncYutoriClient() as client:
        usage = await client.get_usage()
        scouts = await client.scouts.list()
        print(usage, scouts)

asyncio.run(main())
```

## Error Handling

```python
from yutori import YutoriClient, APIError, APIConnectionError, AuthenticationError

try:
    client.get_usage()
except AuthenticationError as e:
    print(f"Invalid API key: {e}")
except APIConnectionError as e:
    print(f"Connection failed: {e}")
except APIError as e:
    print(f"API error (status {e.status_code}): {e.message}")
```

## CLI

```bash
# Authentication
yutori auth login      # Log in via browser
yutori auth status     # Show whether an API key is configured locally
yutori auth logout     # Remove saved credentials

# Scouts
yutori scouts list
yutori scouts create -q "monitor for news"
yutori scouts create -q "monitor for news" -i daily -tz America/New_York
yutori scouts get SCOUT_ID
yutori scouts delete SCOUT_ID

# Browsing
yutori browse list
yutori browse list --limit 20 --status succeeded
yutori browse run "extract all prices" https://example.com/products
yutori browse run "log in and continue" https://example.com/login --require-auth
yutori browse run "export dashboard data" https://example.com/dashboard --browser local
yutori browse get TASK_ID

# Research
yutori research list
yutori research list --limit 10 --status running
yutori research run "latest developments in quantum computing" -tz America/Los_Angeles
yutori research get TASK_ID

# Usage
yutori usage
```

Run `yutori --help` or `yutori <command> --help` for full options.

## Examples

See [examples/](examples/) for complete working examples: Navigator n1.5 browser loops, custom tools, and Navigator n2 on local Docker or Daytona infrastructure.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.

## Documentation

- [docs.yutori.com](https://docs.yutori.com) — API reference, model versions, and parameter details
- [platform.yutori.com](https://platform.yutori.com) — usage monitoring, billing, and API keys
- [api.md](api.md) — SDK and CLI surface reference

## License

Apache 2.0 — see [LICENSE](LICENSE).
