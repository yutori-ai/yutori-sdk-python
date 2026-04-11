# Yutori Python SDK & CLI API Reference

This document provides a comprehensive reference to the Yutori Python SDK.

## Types

```python
from yutori import (
    YutoriClient,
    AsyncYutoriClient,
    YutoriSDKError,
    AuthenticationError,
    APIError,
)
```

### Exception Types

| Exception | Description |
|-----------|-------------|
| `YutoriSDKError` | Base exception for all SDK errors |
| `AuthenticationError` | Invalid or missing API key (HTTP 401 or 403) |
| `APIError` | General API error with `status_code`, `message`, and `response` attributes |

## Client

### YutoriClient

Synchronous client for the Yutori API.

```python
client = YutoriClient(
    api_key="yt-...",              # Optional (auto-resolves from env var or CLI login)
    base_url="https://api.yutori.com/v1",  # Optional
    timeout=30.0,                  # Optional, in seconds
)
```

#### Methods

| Method | HTTP | Endpoint | Returns |
|--------|------|----------|---------|
| `client.get_usage(period=None)` | GET | `/v1/usage` | `dict` |
| `client.close()` | - | - | `None` |

#### get_usage

```python
usage = client.get_usage(period="7d")
```

**Parameters:**
- `period` (str, optional): Time range for activity counts - `"24h"` (default), `"7d"`, `"30d"`, or `"90d"`.

**Returns:** Dictionary containing:
- `num_active_scouts` (int): Number of active (non-paused, non-completed) scouts.
- `active_scout_ids` (list[str]): UUIDs of active scouts.
- `rate_limits` (dict): API Gateway rate limits with `requests_today`, `daily_limit`, `remaining_requests`, `reset_at`, and `status` (`"available"` or `"unavailable"`).
- `n1_rate_limits` (dict): n1 API rate limits with `requests_today`, `daily_limit`, `remaining_requests`, `reset_at`, and `per_second_limit`.
- `activity` (dict): Activity counts for the requested period with `period`, `scout_runs`, `browsing_tasks`, `research_tasks`, and `n1_calls`.

### AsyncYutoriClient

Asynchronous client with identical interface. All methods are coroutines.

```python
async with AsyncYutoriClient(api_key="yt-...") as client:
    usage = await client.get_usage()
```

## Namespaces

### client.chat

n1 API - Pixels-to-actions LLM for browser control. OpenAI SDK compatible.

| Method | HTTP | Endpoint | Returns |
|--------|------|----------|---------|
| `client.chat.completions.create(messages, model="n1-latest", **kwargs)` | POST | `/v1/chat/completions` | `ChatCompletion` |

#### chat.completions.create

```python
response = client.chat.completions.create(
    model="n1-latest",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe the screenshot and search for Yutori."},
                {"type": "image_url", "image_url": {"url": "https://example.com/screenshot.jpg"}}
            ]
        }
    ]
)

# Access the response
message = response.choices[0].message
print(message.content)  # Model's thoughts

# Get tool calls (browser actions)
if message.tool_calls:
    for tool_call in message.tool_calls:
        print(f"Action: {tool_call.function.name}")
        print(f"Arguments: {tool_call.function.arguments}")
```

**Parameters:**
- `messages` (Iterable[ChatCompletionMessageParam]): Chat messages following OpenAI format. Include screenshots as `image_url` content blocks.
- `model` (str): Model ID. Default: `"n1-latest"`.
- `**kwargs`: Additional OpenAI-compatible parameters (e.g., `temperature`, `tools`, `tool_choice`, `response_format`). n1.5 also accepts `tool_set`, `disable_tools`, and `json_schema`.

**Returns:** `ChatCompletion` object from the OpenAI SDK with `choices[0].message` containing the model's response and optional `tool_calls` for browser actions. When `json_schema` is provided on n1.5, the parsed structured output is available on the top-level completion object as `response.parsed_json`.

Live model IDs and parameter docs are maintained at `https://docs.yutori.com/reference/n1` and `https://docs.yutori.com/reference/n1-5`.

---

### yutori.navigator

Opt-in helper utilities for custom navigator agent loops. These do not change the raw `client.chat.completions.create(...)` interface.

| Helper | Description |
|--------|-------------|
| `estimate_messages_size_bytes(messages)` | Estimate the JSON-serialized byte size of a messages list. |
| `trim_images_to_fit(messages, max_bytes=..., keep_recent=...)` | Remove older screenshot blocks in place until the payload fits within the target size. |
| `trimmed_messages_to_fit(messages, max_bytes=..., keep_recent=...)` | Return a trimmed copy of the messages list without mutating caller state. |
| `create_trimmed(completions, messages, ...)` | Trim a copy of the messages list, then call sync chat completions. |
| `acreate_trimmed(completions, messages, ...)` | Async version of `create_trimmed(...)`. |
| `screenshot_to_data_url(image_bytes, ...)` | Convert screenshot bytes into a `data:image/webp;base64,...` URL optimized for n1. |
| `playwright_screenshot_to_data_url(page, ...)` | Capture and convert a sync Playwright screenshot optimized for n1. |
| `aplaywright_screenshot_to_data_url(page, ...)` | Async version of `playwright_screenshot_to_data_url(...)`. |
| `extract_text_content(content)` | Normalize assistant content across strings, text blocks, and object-backed forms, returning joined text or `None`. |
| `RunHooksBase` | Async no-op lifecycle hook base class with `on_agent_start`, `on_llm_start`, `on_llm_end`, `on_tool_start`, `on_tool_end`, and `on_agent_end`. |

`RunHooksBase` mirrors the lifecycle phases of higher-level agent loops, but it is not wired into `client.chat` automatically. It is intended for consumers building their own orchestration, tracing, or UI layers around n1.

Pillow is included in the base SDK install, so no extra is needed to use the screenshot conversion helpers.

### yutori.navigator.tools

Packaged JavaScript reference implementations for n1.5 expanded browser tools.

| Helper | Description |
|--------|-------------|
| `EXTRACT_ELEMENTS_SCRIPT` | JS source for the `extract_elements` reference implementation. |
| `FIND_SCRIPT` | JS source for the `find` reference implementation. |
| `GET_ELEMENT_BY_REF_SCRIPT` | JS source for resolving an element ref to viewport coordinates. |
| `SET_ELEMENT_VALUE_SCRIPT` | JS source for the `set_element_value` reference implementation. |
| `EXECUTE_JS_SCRIPT` | JS source for the `execute_js` reference implementation. |
| `load_tool_script(name)` | Load a packaged tool script by filename. |
| `evaluate_tool_script(page, script, *args)` | Async helper that runs a packaged tool script via `page.evaluate(...)` and normalizes the result. |
| `coerce_result(raw)` | Normalize raw Playwright evaluation output into a dict payload. |

```python
from yutori.navigator.tools import FIND_SCRIPT, evaluate_tool_script

result = await evaluate_tool_script(page, FIND_SCRIPT, "pricing")
```

---

### client.browsing

Browsing API - One-time browser automation tasks.

| Method | HTTP | Endpoint | Returns |
|--------|------|----------|---------|
| `client.browsing.create(task, start_url, ...)` | POST | `/v1/browsing/tasks` | `dict` |
| `client.browsing.get(task_id)` | GET | `/v1/browsing/tasks/{task_id}` | `dict` |

#### browsing.create

```python
task = client.browsing.create(
    task="Give me a list of all employees of Yutori.",
    start_url="https://yutori.com",
    max_steps=75,                  # Optional (1-100)
    agent=None,                    # Optional
    require_auth=False,            # Optional, for login flows
    browser=None,                  # Optional: "cloud" or "local"
    output_schema=None,            # Optional, JSON schema
    webhook_url=None,              # Optional
    webhook_format=None,           # Optional: "scout", "slack", "zapier"
)
```

**Parameters:**
- `task` (str): Natural language description of the browsing task.
- `start_url` (str): URL to start browsing from.
- `max_steps` (int, optional): Maximum agent steps (1-100).
- `agent` (str, optional): Agent to use. Options: `"navigator-n1-latest"`, `"claude-sonnet-4-5-computer-use-2025-01-24"`.
- `require_auth` (bool, optional): Use auth-optimized browser for login flows.
- `browser` (str, optional): Browser backend - `"cloud"` (default) or `"local"` for Yutori Local with the user's logged-in desktop sessions.
- `output_schema` (dict | BaseModel, optional): JSON schema dict, a Pydantic BaseModel class, or a BaseModel instance (auto-converted via `model_json_schema()` for v2 or `schema()` for v1).
- `webhook_url` (str, optional): URL for completion notifications.
- `webhook_format` (str, optional): Webhook format - `"scout"` (default), `"slack"`, or `"zapier"`.

**Returns:** Dictionary containing `task_id` and task metadata. Failed tasks may include `rejection_reason`.

#### browsing.get

```python
result = client.browsing.get("task_id")
```

**Parameters:**
- `task_id` (str): The unique identifier of the task.

**Returns:** Dictionary with `status` (`"queued"`, `"running"`, `"succeeded"`, `"failed"`) and results if completed.

---

### client.research

Research API - Deep web research using 100+ MCP tools.

| Method | HTTP | Endpoint | Returns |
|--------|------|----------|---------|
| `client.research.create(query, ...)` | POST | `/v1/research/tasks` | `dict` |
| `client.research.get(task_id)` | GET | `/v1/research/tasks/{task_id}` | `dict` |

#### research.create

```python
task = client.research.create(
    query="What are the latest developments in quantum computing?",
    user_timezone="America/Los_Angeles",  # Optional
    user_location="San Francisco, CA",    # Optional
    browser=None,                         # Optional: "cloud" or "local"
    output_schema=None,                   # Optional, JSON schema
    webhook_url=None,                     # Optional
    webhook_format=None,                  # Optional: "scout", "slack", "zapier"
)
```

**Parameters:**
- `query` (str): Natural language research query.
- `user_timezone` (str, optional): Timezone, e.g., `"America/Los_Angeles"`.
- `user_location` (str, optional): Location, e.g., `"San Francisco, CA, US"`.
- `browser` (str, optional): Browser backend - `"cloud"` (default) or `"local"` for Yutori Local with the user's logged-in desktop sessions.
- `output_schema` (dict | BaseModel, optional): JSON schema dict, a Pydantic BaseModel class, or a BaseModel instance (auto-converted via `model_json_schema()` for v2 or `schema()` for v1).
- `webhook_url` (str, optional): URL for completion notifications.
- `webhook_format` (str, optional): Webhook format - `"scout"` (default), `"slack"`, or `"zapier"`.

**Returns:** Dictionary containing `task_id` and task metadata. Failed tasks may include `rejection_reason`.

#### research.get

```python
result = client.research.get("task_id")
```

**Parameters:**
- `task_id` (str): The unique identifier of the task.

**Returns:** Dictionary with `status` (`"queued"`, `"running"`, `"succeeded"`, `"failed"`) and results if completed.

---

### client.scouts

Scouting API - Continuous web monitoring on a schedule.

| Method | HTTP | Endpoint | Returns |
|--------|------|----------|---------|
| `client.scouts.list(limit=None, status=None)` | GET | `/v1/scouting/tasks` | `dict` |
| `client.scouts.get(scout_id)` | GET | `/v1/scouting/tasks/{scout_id}` | `dict` |
| `client.scouts.create(query, ...)` | POST | `/v1/scouting/tasks` | `dict` |
| `client.scouts.update(scout_id, ...)` | POST/PATCH | `/v1/scouting/tasks/{scout_id}/...` | `dict` |
| `client.scouts.delete(scout_id)` | DELETE | `/v1/scouting/tasks/{scout_id}` | `dict` |
| `client.scouts.get_updates(scout_id, ...)` | GET | `/v1/scouting/tasks/{scout_id}/updates` | `dict` |

#### scouts.list

```python
scouts = client.scouts.list(
    limit=20,           # Optional
    status="active",    # Optional: "active", "paused", "done"
)
```

**Parameters:**
- `limit` (int, optional): Maximum number of scouts to return (mapped to API `page_size`).
- `status` (str, optional): Filter by status - `"active"`, `"paused"`, or `"done"`.

**Returns:** Dictionary containing `scouts` list and pagination info.

#### scouts.get

```python
scout = client.scouts.get("scout_id")
```

**Parameters:**
- `scout_id` (str): The unique identifier of the scout.

**Returns:** Dictionary with scout details including `id`, `query`, `status`, `next_run_timestamp`, and optional `rejection_reason`.

#### scouts.create

```python
scout = client.scouts.create(
    query="Tell me about the latest news about Yutori",
    output_interval=86400,         # Optional, seconds (default: 86400 = daily)
    start_timestamp=None,          # Optional, Unix timestamp (0 = immediately)
    user_timezone="America/Los_Angeles",  # Optional
    user_location="San Francisco, CA",    # Optional
    output_schema=None,            # Optional, JSON schema
    skip_email=False,              # Optional
    webhook_url=None,              # Optional
    webhook_format=None,           # Optional: "scout", "slack", "zapier"
    is_public=False,               # Optional
)
```

**Parameters:**
- `query` (str): Natural language description of what to monitor.
- `output_interval` (int, optional): Seconds between runs. Minimum: 1800. Default: 86400 (daily).
- `start_timestamp` (int, optional): Unix timestamp to start. 0 = immediately.
- `user_timezone` (str, optional): Timezone, e.g., `"America/Los_Angeles"`.
- `user_location` (str, optional): Location, e.g., `"San Francisco, CA, US"`.
- `output_schema` (dict | BaseModel, optional): JSON schema dict, a Pydantic BaseModel class, or a BaseModel instance (auto-converted via `model_json_schema()` for v2 or `schema()` for v1).
- `skip_email` (bool, optional): Disable email notifications.
- `webhook_url` (str, optional): URL for completion notifications.
- `webhook_format` (str, optional): Webhook format - `"scout"` (default), `"slack"`, or `"zapier"`.
- `is_public` (bool, optional): Whether the scout is publicly visible.

**Returns:** Dictionary containing created scout details and optional `rejection_reason`.

#### scouts.update

```python
# Pause a scout
client.scouts.update("scout_id", status="paused")

# Resume a scout
client.scouts.update("scout_id", status="active")

# Archive a scout
client.scouts.update("scout_id", status="done")

# Update fields (cannot combine with status change)
client.scouts.update(
    "scout_id",
    query="Updated query",
    output_interval=7200,
    skip_email=True,
)
```

**Parameters:**
- `scout_id` (str): The unique identifier of the scout.
- `status` (str, optional): New status - `"active"`, `"paused"`, or `"done"`.
- `query` (str, optional): Updated monitoring query.
- `output_interval` (int, optional): Updated interval between runs.
- `user_timezone` (str, optional): Updated timezone.
- `user_location` (str, optional): Updated location.
- `output_schema` (dict | BaseModel, optional): JSON schema dict, a Pydantic BaseModel class, or a BaseModel instance (auto-converted via `model_json_schema()` for v2 or `schema()` for v1).
- `skip_email` (bool, optional): Updated email notification setting.
- `webhook_url` (str, optional): Updated webhook URL.
- `webhook_format` (str, optional): Updated webhook format.

**Returns:** Dictionary containing updated scout details.

**Note:** Status changes and field updates cannot be combined in a single call.

#### scouts.delete

```python
client.scouts.delete("scout_id")
```

**Parameters:**
- `scout_id` (str): The unique identifier of the scout.

**Returns:** Empty dictionary on success.

#### scouts.get_updates

```python
updates = client.scouts.get_updates(
    "scout_id",
    limit=20,     # Optional
    cursor=None,  # Optional, for pagination
)
```

**Parameters:**
- `scout_id` (str): The unique identifier of the scout.
- `limit` (int, optional): Maximum number of updates to return.
- `cursor` (str, optional): Pagination cursor from previous response.

**Returns:** Dictionary containing `updates` list and pagination info.

## Authentication

The SDK supports three authentication methods, resolved in this order:

1. **API Key Parameter** (highest priority):
   ```python
   client = YutoriClient(api_key="yt-...")
   ```

2. **Environment Variable:**
   ```python
   # Set YUTORI_API_KEY in your environment
   client = YutoriClient()  # Reads from YUTORI_API_KEY env var
   ```

3. **CLI Login** (saved credentials):
   ```bash
   yutori auth login
   ```
   This opens a browser for Clerk OAuth authentication and saves an API key to `~/.yutori/config.json`. The SDK automatically reads from this file when no explicit key or env var is set.
   ```python
   client = YutoriClient()  # Uses key from ~/.yutori/config.json
   ```

**Resolution order:** explicit `api_key` param > `YUTORI_API_KEY` env var > `~/.yutori/config.json`.

API keys start with `yt-` and can be created at [platform.yutori.com](https://platform.yutori.com) or via `yutori auth login`.

### CLI Auth Commands

| Command | Description |
|---------|-------------|
| `yutori --version` | Show CLI version and exit |
| `yutori auth login` | Authenticate via browser (Clerk OAuth + PKCE), saves API key to `~/.yutori/config.json` |
| `yutori auth status` | Show current auth status (source, masked key) |
| `yutori auth logout` | Remove saved credentials from `~/.yutori/config.json` |

### CLI Resource Commands

| Command | Description |
|---------|-------------|
| `yutori browse run TASK URL [OPTIONS]` | Start a browsing task (`--max-steps`, `--agent`, `--require-auth`, `--browser`) |
| `yutori browse get TASK_ID` | Get browsing task status and result |
| `yutori research run QUERY [OPTIONS]` | Start a research task (`--timezone`, `--location`, `--browser`) |
| `yutori research get TASK_ID` | Get research task status and result |
| `yutori scouts list` | List scouts (`--limit`, `--status`) |
| `yutori scouts get SCOUT_ID` | Get scout details |
| `yutori scouts create -q QUERY` | Create a scout (`--interval`, `--timezone`, etc.) |
| `yutori scouts delete SCOUT_ID` | Delete a scout |
| `yutori usage` | Show API usage statistics |

## Dependencies

- `httpx>=0.26.0,<0.28.0` - HTTP client for browsing, research, and scouting APIs
- `openai>=1.0.0` - OpenAI SDK for the n1 chat API (provides `ChatCompletion` types)
- `typer>=0.9.0` - CLI framework
- `rich>=13.0.0` - Terminal formatting for CLI output

## Error Handling

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
