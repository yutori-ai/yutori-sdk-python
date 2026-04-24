# Yutori Python SDK & CLI API Reference

A dense reference to everything the Yutori Python SDK and CLI expose. The [README](README.md) has the human-facing quickstart; this file is for agents and consumers that need exact signatures, parameters, and import paths.

## Package layout

| Import | Purpose |
|--------|---------|
| `yutori` | Clients (`YutoriClient`, `AsyncYutoriClient`) and exceptions (`APIError`, `AuthenticationError`, `YutoriSDKError`) |
| `yutori.navigator` | Agent-loop helpers for the Navigator (n1 / n1.5) chat completions endpoint |
| `yutori.navigator.tools` | Packaged JavaScript reference implementations for n1.5 expanded browser tools |

All SDK calls go through `YutoriClient` / `AsyncYutoriClient`. The Navigator helpers are optional and do not change the shape of `client.chat.completions.create(...)`.

## Exceptions

```python
from yutori import YutoriSDKError, AuthenticationError, APIError
```

| Exception | Description |
|-----------|-------------|
| `YutoriSDKError` | Base class for all Yutori SDK errors. |
| `AuthenticationError` | Raised on HTTP 401/403 and when no API key can be resolved. |
| `APIError` | Raised for other non-2xx responses. Attributes: `status_code: int`, `message: str`, `response: httpx.Response \| None`. |

`client.chat` is backed by the OpenAI Python SDK, so HTTP errors from that path surface as `openai.OpenAIError` subclasses (e.g. `openai.APIError`, `openai.RateLimitError`), not wrapped into `yutori` exceptions.

## Client

### `YutoriClient`

Synchronous client.

```python
YutoriClient(
    api_key: str | None = None,
    *,
    base_url: str = "https://api.yutori.com/v1",
    timeout: float = 30.0,
)
```

**Methods:**

| Method | HTTP | Endpoint | Returns |
|--------|------|----------|---------|
| `get_usage(*, period=None)` | GET | `/v1/usage` | `dict` |
| `close()` | — | — | `None` |
| `__enter__` / `__exit__` | — | — | context-manager support |

**Namespaces** (all attributes of the client):

| Attribute | Class | Purpose |
|-----------|-------|---------|
| `client.chat` | `ChatNamespace` | Navigator (n1 / n1.5) chat completions |
| `client.browsing` | `BrowsingNamespace` | One-time browser automation |
| `client.research` | `ResearchNamespace` | One-time deep web research |
| `client.scouts` | `ScoutsNamespace` | Continuous monitoring scouts |

### `AsyncYutoriClient`

Identical surface to `YutoriClient` with `async` methods and `async with` support.

```python
async with AsyncYutoriClient(api_key="yt-...") as client:
    usage = await client.get_usage()
```

### `get_usage`

```python
usage = client.get_usage(period="7d")
```

**Parameters:**
- `period` (`str`, optional): Activity period. One of `"24h"` (default), `"7d"`, `"30d"`, `"90d"`.

**Returns:** Dictionary with:
- `num_active_scouts` (`int`)
- `active_scout_ids` (`list[str]`)
- `rate_limits` (`dict`): `requests_today`, `daily_limit`, `remaining_requests`, `reset_at`, `status` (`"available"` | `"unavailable"`)
- `navigator_rate_limits` (`dict`): `requests_today`, `daily_limit`, `remaining_requests`, `reset_at`, `per_second_limit`
- `activity` (`dict`): `period`, `scout_runs`, `browsing_tasks`, `research_tasks`, `navigator_calls`

The response also includes `n1_rate_limits` and `activity.n1_calls` as deprecated aliases of `navigator_rate_limits` and `navigator_calls` respectively. They will be removed in a future release — prefer the `navigator_*` names.

## Model constants and tool sets

Importable from `yutori.navigator`. Prefer these over hard-coded strings so upgrades land automatically.

```python
from yutori.navigator import N1_MODEL, N1_5_MODEL, TOOL_SET_CORE, TOOL_SET_EXPANDED
```

| Constant | Value | Notes |
|----------|-------|-------|
| `N1_MODEL` | `"n1-latest"` | Alias for the latest stable n1 model. |
| `N1_5_MODEL` | `"n1.5-latest"` | Alias for the latest stable n1.5 model (current default). |
| `TOOL_SET_CORE` | `"browser_tools_core-20260403"` | Default n1.5 tool set — 18 coordinate-based browser tools. |
| `TOOL_SET_EXPANDED` | `"browser_tools_expanded-20260403"` | Core tools + `extract_elements`, `find`, `set_element_value`, `execute_js`. |
| `N1_COORDINATE_SCALE` | `1000` | The normalized action space is `N1_COORDINATE_SCALE × N1_COORDINATE_SCALE`. |

For pinned versions (e.g. `n1-20260203`, `n1-experimental-20260309`) see [docs.yutori.com/reference/n1](https://docs.yutori.com/reference/n1) and [docs.yutori.com/reference/n1-5](https://docs.yutori.com/reference/n1-5).

## Namespaces

### `client.chat` — Navigator API

OpenAI-compatible pixels-to-actions chat completions. Works with both n1 and n1.5 models.

| Method | HTTP | Endpoint | Returns |
|--------|------|----------|---------|
| `client.chat.completions.create(messages, *, model="n1.5-latest", tool_set=None, disable_tools=None, json_schema=None, **kwargs)` | POST | `/v1/chat/completions` | `openai.types.chat.ChatCompletion` |

#### `chat.completions.create`

```python
from yutori.navigator import N1_5_MODEL

response = client.chat.completions.create(
    model=N1_5_MODEL,
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Search for Yutori."},
                {"type": "image_url", "image_url": {"url": "data:image/webp;base64,..."}},
            ],
        }
    ],
    tool_set=TOOL_SET_EXPANDED,           # n1.5 only
    disable_tools=["hold_key", "drag"],    # n1.5 only
    json_schema={...},                     # n1.5 only
)

message = response.choices[0].message
print(message.content)        # Model's thoughts
for tc in message.tool_calls or []:
    print(tc.function.name, tc.function.arguments)

# When json_schema is provided and the model returns valid JSON:
parsed = getattr(response, "parsed_json", None)
```

**Parameters:**
- `messages` (`Iterable[ChatCompletionMessageParam]`): OpenAI-format chat messages. Include screenshots as `image_url` content blocks.
- `model` (`str`, default `"n1.5-latest"`): Model alias or pinned ID. Pass `N1_MODEL` / `N1_5_MODEL` for clarity.
- `tool_set` (`str | None`, **n1.5 only**): Which built-in tool set to activate. Use `TOOL_SET_CORE` or `TOOL_SET_EXPANDED`. Forwarded via `extra_body`.
- `disable_tools` (`list[str] | None`, **n1.5 only**): Tool names to remove from the active tool set.
- `json_schema` (`dict | None`, **n1.5 only**): JSON Schema object. When provided, the API constrains decoding and attaches the parsed result as `response.parsed_json`.
- `**kwargs`: Any other OpenAI Chat Completions parameter (`temperature`, `tools`, `tool_choice`, `response_format`, etc.). If the caller already passes `extra_body`, the SDK merges n1.5 params into it.

**Returns:** `openai.types.chat.ChatCompletion`. When `json_schema` is set on n1.5 and parsing succeeds, the API also sets `response.parsed_json`.

**n1 vs. n1.5 summary** (reference: [docs.yutori.com](https://docs.yutori.com/reference/n1-5)):

| Feature | n1 | n1.5 |
|---------|----|----|
| Tool sets | Fixed | `tool_set` (core / expanded) |
| Disable tools | — | `disable_tools` supported |
| Structured JSON output | — | `json_schema` → `response.parsed_json` |
| Mouse move action | `hover` | `mouse_move` |
| Key press param | `key_comb` (Playwright names) | `key` (lowercase names) |
| Click modifiers | — | `ref`, `modifier` |
| Extra actions | — | `hold_key`, `middle_click`, `mouse_down`, `mouse_up`, `go_forward` |
| `type` extras | `press_enter_after`, `clear_before_typing` | — |

### `client.browsing` — Browsing API

One-time browser automation on Yutori's cloud browser or on Yutori Local.

| Method | HTTP | Endpoint | Returns |
|--------|------|----------|---------|
| `client.browsing.create(task, start_url, *, max_steps=None, agent=None, require_auth=None, browser=None, output_schema=None, webhook_url=None, webhook_format=None)` | POST | `/v1/browsing/tasks` | `dict` |
| `client.browsing.get(task_id)` | GET | `/v1/browsing/tasks/{task_id}` | `dict` |

#### `browsing.create`

```python
task = client.browsing.create(
    task="Give me a list of all employees of Yutori.",
    start_url="https://yutori.com",
    max_steps=75,
    agent=None,
    require_auth=False,
    browser=None,
    output_schema=None,
    webhook_url=None,
    webhook_format=None,
)
```

**Parameters:**
- `task` (`str`): Natural language description of the browsing task.
- `start_url` (`str`): URL to start browsing from.
- `max_steps` (`int`, optional): Maximum agent steps (1–100).
- `agent` (`str`, optional): `"navigator-n1-latest"` (default) or `"claude-sonnet-4-5-computer-use-2025-01-24"`.
- `require_auth` (`bool`, optional): Use an auth-optimized browser for login flows.
- `browser` (`str`, optional): `"cloud"` (default) or `"local"` for Yutori Local with the user's logged-in desktop sessions.
- `output_schema`: See [Structured output](#structured-output).
- `webhook_url` (`str`, optional): URL for completion notifications.
- `webhook_format` (`str`, optional): `"scout"` (default), `"slack"`, or `"zapier"`.

**Returns:** Dict containing at least `task_id`. Failed tasks may include `rejection_reason`.

#### `browsing.get`

```python
result = client.browsing.get("task_id")
```

**Returns:** Dict with `status` (`"queued"` | `"running"` | `"succeeded"` | `"failed"`) and, when complete, the task result.

### `client.research` — Research API

Deep web research using 100+ MCP tools.

| Method | HTTP | Endpoint | Returns |
|--------|------|----------|---------|
| `client.research.create(query, *, user_timezone=None, user_location=None, output_schema=None, webhook_url=None, webhook_format=None)` | POST | `/v1/research/tasks` | `dict` |
| `client.research.get(task_id)` | GET | `/v1/research/tasks/{task_id}` | `dict` |

#### `research.create`

```python
task = client.research.create(
    query="What are the latest developments in quantum computing?",
    user_timezone="America/Los_Angeles",
    user_location="San Francisco, CA, US",
    output_schema=None,
    webhook_url=None,
    webhook_format=None,
)
```

**Parameters:**
- `query` (`str`): Natural language research query.
- `user_timezone` (`str`, optional): e.g. `"America/Los_Angeles"`.
- `user_location` (`str`, optional): e.g. `"San Francisco, CA, US"`.
- `output_schema`: See [Structured output](#structured-output).
- `webhook_url` (`str`, optional): URL for completion notifications.
- `webhook_format` (`str`, optional): `"scout"` (default), `"slack"`, or `"zapier"`.

**Returns:** Dict containing `task_id`; may include `rejection_reason`.

### `client.scouts` — Scouting API

Recurring web-monitoring scouts.

| Method | HTTP | Endpoint | Returns |
|--------|------|----------|---------|
| `client.scouts.list(*, limit=None, status=None)` | GET | `/v1/scouting/tasks` | `dict` |
| `client.scouts.get(scout_id)` | GET | `/v1/scouting/tasks/{scout_id}` | `dict` |
| `client.scouts.create(query, *, output_interval=86400, start_timestamp=None, user_timezone=None, user_location=None, output_schema=None, skip_email=None, webhook_url=None, webhook_format=None, is_public=None)` | POST | `/v1/scouting/tasks` | `dict` |
| `client.scouts.update(scout_id, *, query=None, status=None, output_interval=None, user_timezone=None, user_location=None, output_schema=None, skip_email=None, webhook_url=None, webhook_format=None)` | PATCH or POST (status endpoints) | `/v1/scouting/tasks/{scout_id}` or `.../pause|resume|done` | `dict` |
| `client.scouts.delete(scout_id)` | DELETE | `/v1/scouting/tasks/{scout_id}` | `dict` |
| `client.scouts.get_updates(scout_id, *, limit=None, cursor=None)` | GET | `/v1/scouting/tasks/{scout_id}/updates` | `dict` |

#### `scouts.list`

```python
scouts = client.scouts.list(limit=20, status="active")
```

**Parameters:**
- `limit` (`int`, optional): Max scouts to return. Mapped to the API's `page_size` query param.
- `status` (`str`, optional): `"active"`, `"paused"`, or `"done"`.

**Returns:** Dict containing `scouts` list and pagination info.

#### `scouts.get`

```python
scout = client.scouts.get("scout_id")
```

**Returns:** Dict with `id`, `query`, `status`, `output_interval`, `next_run_at`, `created_at`, optional `rejection_reason`, etc.

#### `scouts.create`

```python
scout = client.scouts.create(
    query="Tell me about the latest news about Yutori",
    output_interval=86400,
    start_timestamp=None,
    user_timezone="America/Los_Angeles",
    user_location="San Francisco, CA, US",
    output_schema=None,
    skip_email=None,
    webhook_url=None,
    webhook_format=None,
    is_public=None,
)
```

**Parameters:**
- `query` (`str`): What to monitor.
- `output_interval` (`int`, default `86400`): Seconds between runs. Minimum `1800`.
- `start_timestamp` (`int`, optional): Unix timestamp. `0` means immediately.
- `user_timezone`, `user_location` (`str`, optional): Context strings.
- `output_schema`: See [Structured output](#structured-output).
- `skip_email` (`bool`, optional): Disable email notifications.
- `webhook_url`, `webhook_format` (`str`, optional): Async notification config.
- `is_public` (`bool`, optional): Public/private visibility.

**Returns:** Dict with created scout details; may include `rejection_reason`.

#### `scouts.update`

```python
# Status transitions (mapped to /pause, /resume, /done endpoints)
client.scouts.update("scout_id", status="paused")
client.scouts.update("scout_id", status="active")
client.scouts.update("scout_id", status="done")

# Field updates (PATCH)
client.scouts.update(
    "scout_id",
    query="Updated query",
    output_interval=7200,
    skip_email=True,
)
```

**Parameters:** All optional except `scout_id`. Same fields as `create`, plus `status` (`"active"` | `"paused"` | `"done"`).

**Constraints:**
- `status` and field updates **cannot be combined** in a single call — the SDK raises `ValueError`.
- When only `status` is provided, the SDK posts to the matching endpoint (`/pause`, `/resume`, `/done`).
- When only fields are provided, the SDK PATCHes `/v1/scouting/tasks/{scout_id}`.
- Calling `update` with no fields raises `ValueError`.

**Returns:** Dict with the updated scout.

#### `scouts.delete`

```python
client.scouts.delete("scout_id")
```

**Returns:** Empty dict on success.

#### `scouts.get_updates`

```python
updates = client.scouts.get_updates("scout_id", limit=20, cursor=None)
```

**Returns:** Dict with `updates` list and pagination cursor.

## Structured output

`output_schema` on `browsing.create`, `research.create`, and `scouts.create` accepts:

- A JSON Schema `dict`.
- A Pydantic **v2** `BaseModel` class or instance (converted via `model_json_schema()`).
- A Pydantic **v1** `BaseModel` class or instance (converted via `schema()`).

Pydantic is **not** a hard dependency — detection is by duck typing.

```python
from pydantic import BaseModel

class Employee(BaseModel):
    name: str
    title: str

task = client.browsing.create(
    task="List all employees of Yutori.",
    start_url="https://yutori.com",
    output_schema=Employee,
)
```

Equivalent dict form:

```python
task = client.browsing.create(
    task="...",
    start_url="...",
    output_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "title": {"type": "string"},
            },
        },
    },
)
```

Structured output for the Navigator API (n1.5 only) is a separate parameter on `client.chat.completions.create(...)`: `json_schema=...` with results on `response.parsed_json`.

## `yutori.navigator`

Opt-in helpers for custom agent loops. They do **not** change the shape of `client.chat.completions.create(...)`. Import paths:

```python
from yutori.navigator import (
    # Models / tool sets
    N1_MODEL, N1_5_MODEL, TOOL_SET_CORE, TOOL_SET_EXPANDED, N1_COORDINATE_SCALE,
    # Screenshots
    aplaywright_screenshot_to_data_url, playwright_screenshot_to_data_url, screenshot_to_data_url,
    # Coordinates
    denormalize_coordinates, normalize_coordinates,
    # Task / prompt formatting
    format_task_with_context, format_user_context, format_stop_and_summarize,
    # Key mapping (n1.5)
    map_key_to_playwright, map_keys_individual,
    # Payload trimming
    estimate_messages_size_bytes, trim_images_to_fit, trimmed_messages_to_fit,
    # Trimmed request wrappers
    create_trimmed, acreate_trimmed,
    # Misc
    extract_text_content, RunHooksBase,
)
```

### Screenshot helpers

| Helper | Signature | Description |
|--------|-----------|-------------|
| `screenshot_to_data_url` | `(image_bytes: bytes, *, resize_to=(1280, 800), source_format=None, webp_quality=None) -> str` | Convert raw screenshot bytes into `data:image/webp;base64,...`. Pillow is a required SDK dep. |
| `playwright_screenshot_to_data_url` | `(page, *, resize_to=(1280, 800), webp_quality=None) -> str` | Capture a sync Playwright page screenshot (JPEG, q=75) and re-encode as WebP. |
| `aplaywright_screenshot_to_data_url` | `(page, *, resize_to=(1280, 800), webp_quality=None) -> str` | Async version of the above. |

Default quality is WebP q=90 (or q=30 for PNG sources).

### Coordinate helpers

The Navigator emits tool-call coordinates in a normalized `N1_COORDINATE_SCALE × N1_COORDINATE_SCALE` (1000×1000) space.

| Helper | Signature | Description |
|--------|-----------|-------------|
| `denormalize_coordinates` | `(coordinates, width, height, *, scale=1000, clamp=True) -> tuple[int, int]` | Normalized → viewport pixels. |
| `normalize_coordinates` | `(coordinates, width, height, *, scale=1000, clamp=True) -> tuple[int, int]` | Viewport pixels → normalized. |

Raises `ValueError` on bad input (non-finite values, wrong length, non-positive dimensions).

### Task / prompt formatting

| Helper | Signature | Description |
|--------|-----------|-------------|
| `format_user_context` | `(*, user_timezone="America/Los_Angeles", user_location="San Francisco, CA, US") -> str` | Builds a multi-line block with location, timezone, date, time, day. Falls back to UTC if `zoneinfo` has no tzdata. |
| `format_task_with_context` | `(task: str, *, user_timezone=..., user_location=...) -> str` | `f"{task}\n\n{format_user_context(...)}"`. |
| `format_stop_and_summarize` | `(task: str) -> str` | Prompt that asks the model to stop iterating and produce a summary — for use when hitting max steps or an error. |

### Key mapping (n1.5)

n1.5 returns lowercase key names (`ctrl+c`, `enter`, `down`) which must be converted for Playwright.

| Helper | Signature | Description |
|--------|-----------|-------------|
| `map_key_to_playwright` | `(key_expr: str) -> list[str]` | Space-separated sequence → list of Playwright `keyboard.press()`-compatible strings (combos joined with `+`). E.g. `"ctrl+c"` → `["Control+c"]`, `"down down enter"` → `["ArrowDown", "ArrowDown", "Enter"]`. |
| `map_keys_individual` | `(key_expr: str) -> list[str]` | Same input, but never joins with `+`. Safe for `keyboard.down()`/`keyboard.up()` which only accept single keys. E.g. `"ctrl+c"` → `["Control", "c"]`. |

### Payload trimming

For screenshot-heavy loops where the JSON payload can blow past the API size limit.

| Helper | Signature | Description |
|--------|-----------|-------------|
| `estimate_messages_size_bytes` | `(messages) -> int` | UTF-8 byte length of `json.dumps(messages, separators=(",",":"))`. |
| `trim_images_to_fit` | `(messages, *, max_bytes=9_500_000, keep_recent=6) -> tuple[int, int]` | **Mutates** `messages` in place. Returns `(current_size, images_removed)`. Protects the `keep_recent` most recent screenshots; the latest screenshot is always preserved. Uses a two-phase strategy: first drop old screenshots outside the protected window, then dip into it (except the last) if still over limit. |
| `trimmed_messages_to_fit` | `(messages, *, max_bytes=9_500_000, keep_recent=6) -> tuple[list, int, int]` | Deep-copies `messages` first — safe default. Returns `(trimmed_messages, current_size, images_removed)`. |

When an image is stripped, a `"Screenshot omitted to stay under request size limit."` text block is inserted if the message would otherwise be content-less.

### Trimmed-request wrappers

Thin wrappers around `chat.completions.create(...)` that trim the request copy before sending.

| Helper | Signature | Description |
|--------|-----------|-------------|
| `create_trimmed` | `(completions, messages, *, model=N1_5_MODEL, max_bytes=9_500_000, keep_recent=6, **kwargs) -> ChatCompletion` | Sync. Expects `completions` to quack like `ChatCompletions`. |
| `acreate_trimmed` | `(completions, messages, *, model=N1_5_MODEL, max_bytes=9_500_000, keep_recent=6, **kwargs) -> ChatCompletion` | Async. |

Additionally, `yutori.navigator.loop.update_trimmed_history(messages, request_messages=None, *, max_bytes=..., keep_recent=...)` is available for long-lived loops that keep a complete replayable history separate from the trimmed request copy. It returns `(request_messages, size_bytes, removed)`.

### Miscellaneous helpers

| Helper | Signature | Description |
|--------|-----------|-------------|
| `extract_text_content` | `(content) -> str \| None` | Normalize OpenAI-style content (string, list of text/image blocks, or object with `.text`) into a single text string. Returns `None` for empty/missing content. |
| `RunHooksBase` | class | Async no-op lifecycle hooks with `on_agent_start`, `on_llm_start`, `on_llm_end`, `on_tool_start`, `on_tool_end`, `on_agent_end`. Intentionally not a drop-in of the OpenAI Agents SDK — mirrors phases, not exact signatures. Not wired into `client.chat` automatically. |

### `yutori.navigator.tools`

Packaged JavaScript reference implementations for n1.5's expanded browser tool set. The scripts are shipped as `.js` files inside the wheel (`yutori.navigator.tools.js`).

```python
from yutori.navigator.tools import (
    EXECUTE_JS_SCRIPT,
    EXTRACT_ELEMENTS_SCRIPT,
    FIND_SCRIPT,
    GET_ELEMENT_BY_REF_SCRIPT,
    SET_ELEMENT_VALUE_SCRIPT,
    load_tool_script,
    evaluate_tool_script,
    coerce_result,
)
```

| Symbol | Type | Description |
|--------|------|-------------|
| `EXECUTE_JS_SCRIPT` | `str` | JS source for `execute_js`. |
| `EXTRACT_ELEMENTS_SCRIPT` | `str` | JS source for `extract_elements`. |
| `FIND_SCRIPT` | `str` | JS source for `find` (text search). |
| `GET_ELEMENT_BY_REF_SCRIPT` | `str` | JS source for resolving an element ref into viewport coordinates. |
| `SET_ELEMENT_VALUE_SCRIPT` | `str` | JS source for `set_element_value` (robust form input). |
| `load_tool_script(name: str) -> str` | function | Load any packaged tool script by filename (e.g. `"extract_elements.js"`). Results are cached. |
| `coerce_result(raw) -> dict` | function | Normalize `page.evaluate(...)` output: `None` → `{"success": False, "message": "Script returned no result"}`; `dict` → passthrough; JSON string parsing to a dict → that dict; anything else → `{"value": raw}`. |
| `evaluate_tool_script(page, script, *args) -> dict` | async function | `await page.evaluate(f"({script})({json_serialized_args})")` then `coerce_result(...)`. |

## Authentication

Resolution order (first match wins):

1. Explicit `api_key` argument on `YutoriClient` / `AsyncYutoriClient`.
2. `YUTORI_API_KEY` environment variable.
3. `~/.yutori/config.json` written by `yutori auth login`.

API keys start with `yt-` and can be created at [platform.yutori.com](https://platform.yutori.com) or via `yutori auth login`.

## CLI

Installed as `yutori` (via the `yutori` script entry point). Run any command with `--help` for full option details.

### Root

| Command | Description |
|---------|-------------|
| `yutori --version` | Show CLI version and exit (eager flag). |
| `yutori version` | Show CLI version as a subcommand. |

### Auth

| Command | Description |
|---------|-------------|
| `yutori auth login` | Clerk OAuth + PKCE login via browser; saves API key to `~/.yutori/config.json`. Exits with code 1 if `YUTORI_API_KEY` is already set or if a key is already saved. |
| `yutori auth status` | Show authentication source (`config_file` or `env_var`) and a masked key. Exit code 1 if not authenticated. |
| `yutori auth logout` | Clear saved credentials. |

### Browse

| Command | Description |
|---------|-------------|
| `yutori browse run TASK START_URL [--max-steps N] [--agent NAME] [--require-auth] [--browser cloud\|local]` | Submit a browsing task. |
| `yutori browse get TASK_ID` | Get status and result (truncates output to 2000 chars). |

### Research

| Command | Description |
|---------|-------------|
| `yutori research run QUERY [--timezone/-tz TZ] [--location LOC]` | Submit a research task. |
| `yutori research get TASK_ID` | Get status and result (truncates output to 2000 chars). |

### Scouts

| Command | Description |
|---------|-------------|
| `yutori scouts list [--limit N] [--status active\|paused\|done]` | List scouts (rich table). |
| `yutori scouts get SCOUT_ID` | Show scout detail. |
| `yutori scouts create [--query/-q Q] [--interval/-i hourly\|daily\|weekly] [--timezone/-tz TZ]` | Create a scout. Prompts for `query` interactively if `-q` is omitted. Only the three named intervals are accepted; for arbitrary seconds, call `scouts.create` from Python. |
| `yutori scouts delete SCOUT_ID [--force/-f]` | Delete a scout. Prompts for confirmation unless `--force`. |

### Usage

| Command | Description |
|---------|-------------|
| `yutori usage [--period 24h\|7d\|30d\|90d]` | API usage statistics (rate limits + activity counts). Default `24h`. |

## Dependencies

Required (installed with `pip install yutori` or `uv add yutori`):

| Package | Version | Purpose |
|---------|---------|---------|
| `httpx` | `>=0.26.0,<0.28.0` | HTTP client for browsing/research/scouting. |
| `openai` | `>=1.0.0` | Backs `client.chat` (provides `ChatCompletion` types). |
| `pillow` | `>=10.0.0` | Screenshot helpers in `yutori.navigator`. |
| `typer` | `>=0.9.0` | CLI framework. |
| `rich` | `>=13.0.0` | Terminal output for the CLI. |

Optional extras:

| Extra | Packages | Purpose |
|-------|----------|---------|
| `dev` | `pytest`, `pytest-asyncio`, `ruff`, `build` | Development tooling. |
| `examples` | `loguru`, `playwright`, `pydantic`, `tenacity` | Running the `examples/` scripts. Pydantic is also the library to install if you want to pass Pydantic models to `output_schema=`. |

## Error handling example

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
