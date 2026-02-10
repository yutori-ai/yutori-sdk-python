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
| `client.get_usage()` | GET | `/v1/usage` | `dict` |
| `client.close()` | - | - | `None` |

`client.get_usage()` returns an API-defined dictionary. Current responses include summary counters such as `num_scouts` and `active_scout_ids`.

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
- `**kwargs`: Additional parameters (e.g., `temperature`).

**Returns:** `ChatCompletion` object from the OpenAI SDK with `choices[0].message` containing the model's response and optional `tool_calls` for browser actions.

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
    output_schema=None,            # Optional, JSON schema
    webhook_url=None,              # Optional
    webhook_format=None,           # Optional: "scout", "slack", "zapier"
)
```

**Parameters:**
- `task` (str): Natural language description of the browsing task.
- `start_url` (str): URL to start browsing from.
- `max_steps` (int, optional): Maximum agent steps (1-100).
- `agent` (str, optional): Agent to use. Options: `"navigator-n1-preview-2025-11"`, `"claude-sonnet-4-5-computer-use-2025-01-24"`.
- `require_auth` (bool, optional): Use auth-optimized browser for login flows.
- `output_schema` (dict, optional): JSON schema for structured output.
- `webhook_url` (str, optional): URL for completion notifications.
- `webhook_format` (str, optional): Webhook format - `"scout"` (default), `"slack"`, or `"zapier"`.

**Returns:** Dictionary containing `task_id` and task metadata.

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
    output_schema=None,                   # Optional, JSON schema
    webhook_url=None,                     # Optional
    webhook_format=None,                  # Optional: "scout", "slack", "zapier"
)
```

**Parameters:**
- `query` (str): Natural language research query.
- `user_timezone` (str, optional): Timezone, e.g., `"America/Los_Angeles"`.
- `user_location` (str, optional): Location, e.g., `"San Francisco, CA, US"`.
- `output_schema` (dict, optional): JSON schema for structured output.
- `webhook_url` (str, optional): URL for completion notifications.
- `webhook_format` (str, optional): Webhook format - `"scout"` (default), `"slack"`, or `"zapier"`.

**Returns:** Dictionary containing `task_id` and task metadata.

#### research.get

```python
result = client.research.get("task_id")
```

**Parameters:**
- `task_id` (str): The unique identifier of the task.

**Returns:** Dictionary with `status` and results if completed.

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

**Returns:** Dictionary with scout details including `id`, `query`, `status`, `next_run_timestamp`.

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
- `output_schema` (dict, optional): JSON schema for structured output.
- `skip_email` (bool, optional): Disable email notifications.
- `webhook_url` (str, optional): URL for completion notifications.
- `webhook_format` (str, optional): Webhook format - `"scout"` (default), `"slack"`, or `"zapier"`.
- `is_public` (bool, optional): Whether the scout is publicly visible.

**Returns:** Dictionary containing created scout details.

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
- `output_schema` (dict, optional): Updated JSON schema.
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
| `yutori browse run TASK URL [OPTIONS]` | Start a browsing task (`--max-steps`, `--agent`, `--require-auth` flag) |
| `yutori browse get TASK_ID` | Get browsing task status and result |
| `yutori research run QUERY [OPTIONS]` | Start a research task (`--timezone`, `--location`) |
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
