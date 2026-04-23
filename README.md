# Yutori Python SDK & CLI

[![PyPI version](https://img.shields.io/pypi/v/yutori.svg)](https://pypi.org/project/yutori/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

The official Python SDK and CLI for the [Yutori API](https://docs.yutori.com) — build web agents that autonomously execute tasks on the web.

The SDK offers sync and async clients with full type annotations, plus a `yutori` CLI for authentication and managing resources from the terminal.

## Install

On macOS or Linux, the recommended setup is the one-line installer:

```bash
curl -fsSL https://yutori.com/install.sh | bash
```

This installs the global `yutori` CLI with `uv tool install`, then — in an interactive terminal — prompts (with sensible defaults) to add the SDK to your project, run `yutori auth login`, and run a verification browsing task. In a non-interactive session (CI, pipe) the auth and verification prompts are skipped with guidance on how to finish setup.

Python 3.9+ is required for the SDK.

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


## API Overview

The Yutori API provides four main capabilities:


| API           | Description                                | SDK Namespace     |
| ------------- | ------------------------------------------ | ----------------- |
| **Navigator** | Computer-use model for navigating websites | `client.chat`     |
| **Browsing**  | One-time browser automation tasks          | `client.browsing` |
| **Research**  | Deep web research using 100+ tools         | `client.research` |
| **Scouting**  | Continuous web monitoring on a schedule    | `client.scouts`   |


## Navigator API

The Navigator API provides a computer-use model for navigating websites. Capture a screenshot, send it to the model, and execute the returned tool calls. The endpoint follows the OpenAI Chat Completions interface, so `client.chat` is a drop-in OpenAI-compatible client:

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

This snippet shows a single model call. In practice, you'll usually run an agent loop: execute the returned actions on the page, capture a fresh screenshot, and call the model again until it emits `stop`. Complete agent loops live in [examples/](examples/).

The SDK defaults to `n1.5-latest`. `n1-latest` is still supported for callers that want the older model. n1.5 adds selectable tool sets, `disable_tools`, and structured JSON output via `json_schema` (returned as `response.parsed_json`). See [docs](https://docs.yutori.com/reference/n1-5) for model IDs, parameters, and the full action space.

### Agent-loop helpers

The `yutori.navigator` subpackage exposes optional helpers for typical agent loops:


| Helper                                                      | Purpose                                                                                                                        |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `aplaywright_screenshot_to_data_url(page)`                  | Capture a Playwright screenshot as a Navigator-optimized WebP data URL.                                                        |
| `denormalize_coordinates(coords, width, height)`            | Map the model's 1000×1000 coordinate space to viewport pixels.                                                                 |
| `format_task_with_context(task, ...)`                       | Append location, timezone, and current date to a task message.                                                                 |
| `format_stop_and_summarize(task)`                           | Ask the model to summarize when hitting max steps or an error.                                                                 |
| `trimmed_messages_to_fit(messages, max_bytes, keep_recent)` | Drop older screenshots to stay under the API size limit.                                                                       |
| `map_key_to_playwright(key)` / `map_keys_individual(keys)`  | Convert n1.5's lowercase key names to Playwright format.                                                                       |
| `yutori.navigator.tools`                                    | Packaged JS reference implementations for n1.5 expanded tools (`extract_elements`, `find`, `set_element_value`, `execute_js`). |


Full helper reference: [api.md](api.md).

If you'd rather not manage browser infrastructure, use the **Browsing API** below, which runs the Navigator on Yutori's cloud browser.

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

Use `browser="local"` if the research task needs a logged-in browser session. Failed tasks may include a `rejection_reason`.

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
from yutori import YutoriClient, APIError, AuthenticationError

try:
    client.get_usage()
except AuthenticationError as e:
    print(f"Invalid API key: {e}")
except APIError as e:
    print(f"API error (status {e.status_code}): {e.message}")
```

## CLI

```bash
# Authentication
yutori auth login      # Log in via browser
yutori auth status     # Show current auth status
yutori auth logout     # Remove saved credentials

# Scouts
yutori scouts list
yutori scouts create -q "monitor for news"
yutori scouts create -q "monitor for news" -i daily -tz America/New_York
yutori scouts get SCOUT_ID
yutori scouts delete SCOUT_ID

# Browsing
yutori browse run "extract all prices" https://example.com/products
yutori browse run "log in and continue" https://example.com/login --require-auth
yutori browse run "export dashboard data" https://example.com/dashboard --browser local
yutori browse get TASK_ID

# Research
yutori research run "latest developments in quantum computing" -tz America/Los_Angeles
yutori research get TASK_ID

# Usage
yutori usage
```

Run `yutori --help` or `yutori <command> --help` for full options.

## Examples

See [examples/](examples/) for complete working examples, including Navigator agent loops for both n1 and n1.5.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.

## Documentation

- [docs.yutori.com](https://docs.yutori.com) — API reference, model versions, and parameter details
- [platform.yutori.com](https://platform.yutori.com) — usage monitoring, billing, and API keys
- [api.md](api.md) — SDK and CLI surface reference

## License

Apache 2.0 — see [LICENSE](LICENSE).
