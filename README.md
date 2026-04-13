# Yutori Python SDK & CLI

[![PyPI version](https://img.shields.io/pypi/v/yutori.svg)](https://pypi.org/project/yutori/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

The official Python library and CLI for the Yutori API.

Yutori provides APIs for building web agents that autonomously execute tasks on the web. The SDK offers both synchronous and asynchronous clients with full type annotations, plus a CLI for authentication and managing resources from the terminal.

## Documentation

- [API Reference](https://docs.yutori.com)
- [Platform Dashboard](https://platform.yutori.com)

## Installation

```bash
pip install yutori
```

## Getting Started

### Authentication

The easiest way to authenticate is to run this in your terminal:

**Terminal:**
```bash
yutori auth login
```

This opens your browser to log in with your Yutori account and saves an API key to `~/.yutori/config.json`. The SDK and CLI automatically use this saved key.

Alternatively, you can set the `YUTORI_API_KEY` environment variable, or pass the key directly:

```python
from yutori import YutoriClient

# Uses saved credentials from `yutori auth login`, or YUTORI_API_KEY env var
client = YutoriClient()

# Or pass explicitly
client = YutoriClient(api_key="yt-...")
```

API key resolution order: explicit parameter > `YUTORI_API_KEY` env var > `~/.yutori/config.json`.

## API Overview

The Yutori API provides four main capabilities:

| API          | Description                               | SDK Namespace     |
| ------------ | ----------------------------------------- | ----------------- |
| **n1**       | Pixels-to-actions LLM for browser control | `client.chat`     |
| **Browsing** | One-time browser automation tasks         | `client.browsing` |
| **Research** | Deep web research using 100+ tools        | `client.research` |
| **Scouting** | Continuous web monitoring on a schedule   | `client.scouts`   |

## n1 API

The n1 API is a pixels-to-actions LLM that processes screenshots and predicts browser actions (click, type, scroll, etc.). It follows the OpenAI Chat Completions interface. In a typical agent loop you capture a screenshot, send it to the model, and execute the returned tool calls:

```python
from yutori import AsyncYutoriClient
from yutori.navigator import aplaywright_screenshot_to_data_url
from playwright.async_api import async_playwright

async with AsyncYutoriClient() as client, async_playwright() as p:
    browser = await p.chromium.launch()
    page = await browser.new_page()
    await page.goto("https://www.yutori.com")

    # Capture a screenshot optimized for n1
    image_url = await aplaywright_screenshot_to_data_url(page)

    response = await client.chat.completions.create(
        model="n1-latest",
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

    # Get the thoughts
    message = response.choices[0].message
    print(message.content)

    # Get the tool calls, such as browser interaction actions
    if message.tool_calls:
        for tool_call in message.tool_calls:
            print(f"Action: {tool_call.function.name}")
            print(f"Arguments: {tool_call.function.arguments}")
```

Live model IDs and parameter docs: [`n1`](https://docs.yutori.com/reference/n1) and [`n1.5`](https://docs.yutori.com/reference/n1-5). The SDK forwards standard OpenAI chat-completions parameters through `**kwargs`, including `tools`, `tool_choice`, and `response_format`. n1.5 also supports `tool_set`, `disable_tools`, and `json_schema`; when `json_schema` is provided, the parsed structured output is returned on the top-level completion object as `response.parsed_json`.

n1 tool calls use a normalized `1000x1000` coordinate space. The SDK provides public helpers so agent loops do not need to
re-implement that math:

```python
from yutori.navigator import denormalize_coordinates

coords = [500, 250]
x, y = denormalize_coordinates(coords, width=1280, height=800)
```

For agent loops that need user context (location, timezone, current date/time), the SDK provides formatting helpers:

```python
from yutori.navigator import format_task_with_context, format_stop_and_summarize

# Append user context to a task string
task = format_task_with_context(
    "Book a table for 2 tonight",
    user_timezone="America/Los_Angeles",
    user_location="San Francisco, CA, US",
)
# Result:
#   Book a table for 2 tonight
#
#   User's location: San Francisco, CA, US
#   User's timezone: America/Los_Angeles
#   Current Date: April 11, 2026
#   Current Time: 14:05:49 PDT
#   Today is: Saturday

# When hitting max steps or an error, send a stop-and-summarize message
# so the model returns a summary instead of nothing
stop_message = format_stop_and_summarize("Book a table for 2 tonight")
```

For screenshot-heavy agent loops, the SDK also provides opt-in trimming helpers under `yutori.navigator`:

```python
from yutori.navigator import estimate_messages_size_bytes, trimmed_messages_to_fit

if estimate_messages_size_bytes(messages) > 9_500_000:
    messages, size_bytes, removed = trimmed_messages_to_fit(
        messages,
        max_bytes=9_500_000,
        keep_recent=6,
    )

response = await client.chat.completions.create(
    model="n1-latest",
    messages=messages,
)
```

This keeps the raw OpenAI-compatible `client.chat.completions.create(...)` call unchanged, while giving Yutori users a safer
message-preparation helper for large screenshot histories. In long-lived loops, assign the trimmed copy back to your owned
history before the next step so old screenshots do not keep accumulating in memory. The size pre-check is there to avoid
deep-copying the full history on every step when trimming is not needed.

For n1.5 expanded browser tools, the SDK also ships the reference JavaScript implementations as packaged assets under
`yutori.navigator.tools`:

```python
from yutori.navigator.tools import EXTRACT_ELEMENTS_SCRIPT, evaluate_tool_script

dom_data = await evaluate_tool_script(page, EXTRACT_ELEMENTS_SCRIPT, "visible")
```

This lets downstream projects reuse the bundled JS directly instead of copying files out of `examples/`.

If you don't want to manage your own browser infrastructure, use the Browsing API which calls n1 on a cloud browser.

### n1.5

n1.5 extends the n1 API with selectable tool sets, structured JSON output, and a redesigned action space. It uses the same `client.chat.completions.create(...)` call with three additional parameters:

```python
from yutori.navigator import N1_5_MODEL, TOOL_SET_EXPANDED

response = client.chat.completions.create(
    model=N1_5_MODEL,
    messages=messages,
    tool_set=TOOL_SET_EXPANDED,               # Built-in tool set
    disable_tools=["hold_key", "drag"],        # Remove specific tools
    json_schema={                              # Request structured output
        "type": "object",
        "properties": {"names": {"type": "array", "items": {"type": "string"}}},
        "required": ["names"],
    },
)
```

**Parameters:**
- `tool_set` — Built-in tool set to activate. Use the constants `TOOL_SET_CORE` (`"browser_tools_core-20260403"`) or `TOOL_SET_EXPANDED` (`"browser_tools_expanded-20260403"`), which adds `extract_elements`, `find`, `set_element_value`, and `execute_js`.
- `disable_tools` — List of tool names to remove from the selected tool set.
- `json_schema` — JSON Schema dict for structured output. When provided, the model returns a `parsed_json` field on the response.

n1.5 also uses lowercase key names (e.g. `ctrl+c`, `enter`) instead of Playwright names. The SDK provides helpers to convert them:

```python
from yutori.navigator import map_key_to_playwright, map_keys_individual

# Single key or combo → Playwright format
map_key_to_playwright("ctrl+c")    # "Control+c"
map_key_to_playwright("enter")     # "Enter"

# For keyboard.down()/up() which need individual keys
map_keys_individual("ctrl+shift")  # ["Control", "Shift"]
```

See [examples/n1_5.py](examples/n1_5.py) for a complete n1.5 browsing agent.

## Browsing API

Run one-time browser automation tasks. An AI agent can operate either Yutori's cloud browser or Yutori Local on the desktop to complete your task.

```python
# Create a browsing task
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

For tasks that involve logging in on a cloud browser, use `require_auth` to pick an auth-optimized provider:

```python
task = client.browsing.create(
    task="Log in and export the latest invoice.",
    start_url="https://example.com/login",
    require_auth=True,
)
```

To use Yutori Local with the user's existing logged-in desktop sessions instead of the cloud:

```python
task = client.browsing.create(
    task="Export the latest invoice from my dashboard.",
    start_url="https://example.com/dashboard",
    browser="local",
)
```

Failed browsing tasks may include a `rejection_reason` field to explain why the task was rejected.

### Structured Output with Webhooks

You can define the output structure using a JSON schema dict or a Pydantic BaseModel class (Pydantic is optional):

```python
from pydantic import BaseModel  # optional dependency

class Employee(BaseModel):
    name: str
    title: str

task = client.browsing.create(
    task="Give me a list of all employees (names and titles) of Yutori.",
    start_url="https://yutori.com",
    max_steps=75,
    webhook_url="https://example.com/webhook",
    output_schema=Employee,  # auto-converted to JSON schema
)
```

<details>
<summary>Using a JSON schema dict instead</summary>

```python
task = client.browsing.create(
    task="Give me a list of all employees (names and titles) of Yutori.",
    start_url="https://yutori.com",
    max_steps=75,
    webhook_url="https://example.com/webhook",
    output_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "title": {"type": "string"}
            }
        }
    }
)
```

</details>

## Research API

Perform deep web research using 100+ MCP tools including search engines, APIs, and data sources.

```python
task = client.research.create(
    query="What are the latest developments in quantum computing from the past week?",
    user_timezone="America/Los_Angeles",
)

# Poll for results
import time
while True:
    result = client.research.get(task["task_id"])
    if result["status"] in ("succeeded", "failed"):
        break
    time.sleep(5)

print(result)
```

If the research task needs access to a logged-in browser session, use Yutori Local:

```python
task = client.research.create(
    query="Review the latest updates in our vendor dashboard and summarize them.",
    browser="local",
)
```

Failed research tasks may include a `rejection_reason` field to explain why the task was rejected.

### Structured Output

```python
from pydantic import BaseModel  # optional dependency

class Finding(BaseModel):
    title: str
    summary: str
    source_url: str

task = client.research.create(
    query="What are the latest developments in quantum computing?",
    user_timezone="America/Los_Angeles",
    webhook_url="https://example.com/webhook",
    output_schema=Finding,  # auto-converted to JSON schema
)
```

<details>
<summary>Using a JSON schema dict instead</summary>

```python
task = client.research.create(
    query="What are the latest developments in quantum computing?",
    user_timezone="America/Los_Angeles",
    webhook_url="https://example.com/webhook",
    output_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "source_url": {"type": "string"}
            }
        }
    }
)
```

</details>

## Scouting API

Scouts run on a configurable schedule to monitor the web and send notifications when relevant updates occur.

```python
from yutori import YutoriClient

client = YutoriClient(api_key="yt-...")

# Create a scout that monitors for updates
scout = client.scouts.create(
    query="Tell me about the latest news, product updates, and announcements about Yutori AI",
)
print(f"Created scout: {scout['id']}")

# List all active scouts
scouts = client.scouts.list(status="active")

# Get a specific scout
scout = client.scouts.get("scout_abc123")

# Pause a scout
client.scouts.update("scout_abc123", status="paused")

# Resume a scout
client.scouts.update("scout_abc123", status="active")

# Archive a scout
client.scouts.update("scout_abc123", status="done")

# Get scout updates
updates = client.scouts.get_updates("scout_abc123", limit=20)

# Delete a scout
client.scouts.delete("scout_abc123")
```

### Structured Output with Webhooks

```python
from pydantic import BaseModel  # optional dependency

class NewsItem(BaseModel):
    headline: str
    summary: str
    source_url: str

scout = client.scouts.create(
    query="Tell me about the latest news, product updates, and announcements about Yutori AI",
    output_interval=86400,  # Daily
    user_timezone="America/Los_Angeles",
    skip_email=True,
    webhook_url="https://example.com/webhook",
    output_schema=NewsItem,  # auto-converted to JSON schema
)
```

Scout responses may also include `rejection_reason` when a run or configuration is rejected.

<details>
<summary>Using a JSON schema dict instead</summary>

```python
scout = client.scouts.create(
    query="Tell me about the latest news, product updates, and announcements about Yutori AI",
    output_interval=86400,  # Daily
    user_timezone="America/Los_Angeles",
    skip_email=True,
    webhook_url="https://example.com/webhook",
    output_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "summary": {"type": "string"},
                "source_url": {"type": "string"}
            }
        }
    }
)
```

</details>

## Async Usage

The SDK provides an async client with the same interface:

```python
import asyncio
from yutori import AsyncYutoriClient

async def main():
    async with AsyncYutoriClient(api_key="yt-...") as client:
        # All methods are async
        usage = await client.get_usage()
        print(usage)

        scouts = await client.scouts.list()
        print(scouts)

        scout = await client.scouts.create(
            query="Monitor https://example.com for updates",
            output_interval=3600,
        )
        print(scout)

asyncio.run(main())
```

## Error Handling

The SDK raises typed exceptions for API errors:

```python
from yutori import YutoriClient, APIError, AuthenticationError

try:
    client = YutoriClient(api_key="invalid-key")
    client.get_usage()
except AuthenticationError as e:
    print(f"Invalid API key: {e}")
except APIError as e:
    print(f"API error (status {e.status_code}): {e.message}")
```

### Exception Types

| Exception             | Status Code | Description                        |
| --------------------- | ----------- | ---------------------------------- |
| `AuthenticationError` | 401, 403    | Invalid or missing API key         |
| `APIError`            | 4xx, 5xx    | General API error with status code |

## Configuration

```python
from yutori import YutoriClient

client = YutoriClient(
    api_key="yt-...",                          # Or: yutori auth login / YUTORI_API_KEY
    base_url="https://api.yutori.com/v1",      # Default
    timeout=30.0,                               # Request timeout in seconds
)
```

## CLI

The CLI provides commands for authentication and managing Yutori resources from the terminal.

```bash
# Version
yutori --version

# Authentication
yutori auth login       # Log in via browser
yutori auth status      # Show current auth status
yutori auth logout      # Remove saved credentials

# Scouts
yutori scouts list                          # List your scouts
yutori scouts get SCOUT_ID                  # Get scout details
yutori scouts create -q "monitor for news"  # Create a scout
yutori scouts create -q "monitor for news" -i daily -tz America/New_York
yutori scouts delete SCOUT_ID               # Delete a scout

# Browsing
yutori browse run "extract all prices" https://example.com/products
yutori browse run "log in and continue" https://example.com/login --require-auth
yutori browse run "export dashboard data" https://example.com/dashboard --browser local
yutori browse run "fill out the form" https://example.com --agent n1 --max-steps 50
yutori browse get TASK_ID

# Research
yutori research run "latest developments in quantum computing" --browser local
yutori research run "local events this weekend" -tz America/Los_Angeles --location "San Francisco, CA, US"
yutori research get TASK_ID

# Usage
yutori usage            # Show API usage statistics
```

Run `yutori --help` or `yutori <command> --help` for full option details.

## Requirements

- Python 3.9+
- [httpx](https://www.python-httpx.org/) for HTTP requests
- [openai](https://github.com/openai/openai-python) for the n1 chat API
- [typer](https://typer.tiangolo.com/) and [rich](https://rich.readthedocs.io/) for the CLI

## Examples

See [examples/](examples/) for complete working examples and setup instructions, including navigator-based browser agents for n1 and n1.5.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
