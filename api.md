# Yutori Python SDK & CLI API Reference

A dense reference to everything the Yutori Python SDK and CLI expose. The [README](README.md) has the human-facing quickstart; this file is for agents and consumers that need exact signatures, parameters, and import paths.

## Package layout

| Import | Purpose |
|--------|---------|
| `yutori` | Clients (`YutoriClient`, `AsyncYutoriClient`) and exceptions (`APIError`, `APIConnectionError`, `AuthenticationError`, `YutoriSDKError`) |
| `yutori.navigator` | Agent-loop helpers for the Navigator API (Navigator n1.5 / Navigator n2 chat completions) |
| `yutori.navigator.macos` | Native macOS driver (`MacOSComputer`) for Navigator n2 loops — what Yutori MCP runs |
| `yutori.navigator.tools` | Packaged JavaScript reference implementations for the Navigator n1.5 expanded browser tools |

All SDK calls go through `YutoriClient` / `AsyncYutoriClient`. The Navigator helpers are optional and do not change the shape of `client.chat.completions.create(...)`.

## Exceptions

```python
from yutori import YutoriSDKError, AuthenticationError, APIConnectionError, APIError
```

| Exception | Description |
|-----------|-------------|
| `YutoriSDKError` | Base class for all Yutori SDK errors. |
| `AuthenticationError` | Raised on HTTP 401/403 and when no API key can be resolved. |
| `APIConnectionError` | Raised when the API cannot be reached (connection refused, DNS failure, timeout). |
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
| `client.chat` | `ChatNamespace` | Navigator API (Navigator n1.5 / Navigator n2) chat completions |
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

## Model constants and tool sets

Importable from `yutori.navigator`. Prefer these over hard-coded strings so upgrades land automatically.

```python
from yutori.navigator import (
    NAVIGATOR_N1_5_MODEL,
    NAVIGATOR_COORDINATE_SCALE,
    TOOL_SET_CORE,
    TOOL_SET_EXPANDED,
    TOOL_SET_COMPUTER_USE_LATEST,
    TOOL_SET_COMPUTER_USE_20260825,
    TOOL_SET_COMPUTER_USE_20260830,
)
```

| Constant | Value | Notes |
|----------|-------|-------|
| `NAVIGATOR_N1_5_MODEL` | `"n1.5-latest"` | Alias for the latest stable Navigator n1.5 model (current default). |
| `TOOL_SET_CORE` | `"browser_tools_core-20260403"` | Default Navigator n1.5 tool set — 18 coordinate-based browser tools. |
| `TOOL_SET_EXPANDED` | `"browser_tools_expanded-20260403"` | Core tools + `extract_elements`, `find`, `set_element_value`, `execute_js`. |
| `NAVIGATOR_N2_MODEL` | `"n2"` | Stable Navigator n2 model identifier and `N2ComputerAgent` default. |
| `TOOL_SET_COMPUTER_USE_LATEST` | `"computer_use_tools-20260830"` | Current n2 tool set (alias of `TOOL_SET_COMPUTER_USE_20260830`): `computer_batch`, `edit`, `read`, `write`, and `bash`. A batch contains up to 20 actions drawn from 15 action types, including held mouse/key actions and screenshot. |
| `TOOL_SET_COMPUTER_USE_20260830` | `"computer_use_tools-20260830"` | The dated id `LATEST` currently resolves to. |
| `TOOL_SET_COMPUTER_USE_20260825` | `"computer_use_tools-20260825"` | The previous set: the same five tools and batch actions; differs only in tool descriptions and in `computer_batch` marking every argument required, where 20260830 leaves optional arguments optional. |
| `NAVIGATOR_COORDINATE_SCALE` | `1000` | The normalized action space is `NAVIGATOR_COORDINATE_SCALE × NAVIGATOR_COORDINATE_SCALE`. |

**Replay note.** The SDK also accepts the immutable `computer_use_tools-20260818` browser set for replaying recorded trajectories. It is not a desktop set: its extra `goto_url` call requires a computer handler that implements `async goto_url(url: str)`. The bundled desktop and public Cua sandbox adapters deliberately return a recoverable unsupported-environment result for that browser-only call.

For pinned versions (e.g. `n1.5-20260428`) see [docs.yutori.com/reference/n1-5](https://docs.yutori.com/reference/n1-5) and [docs.yutori.com/reference/n2](https://docs.yutori.com/reference/n2).

## Namespaces

### `client.chat` — Navigator API

OpenAI-compatible pixels-to-actions chat completions. Works with Navigator n1.5 and Navigator n2.

| Method | HTTP | Endpoint | Returns |
|--------|------|----------|---------|
| `client.chat.completions.create(messages, *, model="n1.5-latest", tool_set=None, disable_tools=None, json_schema=None, prev_request_id=None, **kwargs)` | POST | `/v1/chat/completions` | `openai.types.chat.ChatCompletion` — echo a response's `request_id` back as `prev_request_id` to link calls into one conversation |

#### `chat.completions.create`

```python
from yutori.navigator import NAVIGATOR_N1_5_MODEL, TOOL_SET_EXPANDED

response = client.chat.completions.create(
    model=NAVIGATOR_N1_5_MODEL,
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Search for Yutori."},
                {"type": "image_url", "image_url": {"url": "data:image/webp;base64,..."}},
            ],
        }
    ],
    tool_set=TOOL_SET_EXPANDED,           # n1.5 tool set; n2 uses TOOL_SET_COMPUTER_USE_LATEST
    disable_tools=["hold_key", "drag"],    # n1.5 names; n2 takes bash/read/write/edit
    json_schema={...},                     # Navigator n1.5 only
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
- `model` (`str`, default `"n1.5-latest"`): Model alias or pinned ID. Pass `NAVIGATOR_N1_5_MODEL` or `"n2"` for clarity.
- `tool_set` (`str | None`): Which server-side tool set to activate. Navigator n1.5: `TOOL_SET_CORE` or `TOOL_SET_EXPANDED`. Navigator n2: `TOOL_SET_COMPUTER_USE_LATEST` (pin it explicitly). Forwarded via `extra_body`.
- `disable_tools` (`list[str] | None`): Tool names to remove from the active tool set. Navigator n2 accepts only `bash`, `read`, `write`, `edit` — `computer_batch` is its GUI surface and cannot be disabled, and unknown names are rejected rather than ignored.
- `json_schema` (`dict | None`, **Navigator n1.5 only**): JSON Schema object. When provided, the API constrains decoding and attaches the parsed result as `response.parsed_json`.
- `**kwargs`: Any other OpenAI Chat Completions parameter (`temperature`, `tools`, `tool_choice`, `response_format`, etc.). If the caller already passes `extra_body`, the SDK merges Navigator n1.5 params into it.

**Returns:** `openai.types.chat.ChatCompletion`. When `json_schema` is set on Navigator n1.5 and parsing succeeds, the API also sets `response.parsed_json`.

#### Navigator n2

Navigator n2 operates a full desktop. Use `model="n2"` and pin the tool set — `TOOL_SET_COMPUTER_USE_LATEST` is the set the SDK's loop implements. It answers with `computer_batch` calls (an ordered sequence of GUI actions, answered with one screenshot taken after the last one), `bash` calls (answered with the command's output), and `read`/`write`/`edit` file-tool calls (answered with the tool's text; a `read` of an image returns the image); a turn with text and no `tool_calls` is the final answer. No frame rides with a `bash` or file-tool result — when the model needs a fresh look after them (or at the start of a run), it requests one itself with a `screenshot` batch member. n2 is non-streaming and rejects caller-provided `json_schema`, `response_format`, and non-auto `tool_choice`. It does accept `disable_tools` and `tools` — see [Changing the n2 tool set](#changing-the-n2-tool-set). Send the full conversation: the server keeps every screenshot in the two newest image-bearing messages and strips older image parts while preserving the rest of the history.

**System prompt.** The server owns the n2 system prompt: every request is served with the model's tool definitions and coordinate conventions. A caller-supplied system message never replaces the served prompt — it is appended at the end under a `# User Instructions` header.

```python
response = client.chat.completions.create(
    model="n2",
    tool_set=TOOL_SET_COMPUTER_USE_LATEST,
    messages=[...],  # task + full-screen screenshot, then alternating assistant tool_calls / tool results
)
```

#### Changing the n2 tool set

A tool set is fixed, but you can drop tools from it and add your own.

`disable_tools` removes tools your harness cannot back — a machine with no shell or no
filesystem should say so rather than let the model call a tool that always fails:

```python
response = client.chat.completions.create(
    model="n2",
    messages=[...],
    tool_set=TOOL_SET_COMPUTER_USE_LATEST,
    disable_tools=["bash", "write", "edit"],
)
```

Only `bash`, `read`, `write` and `edit` can be disabled. `computer_batch` cannot — it is the
GUI surface, and without it there is no agent. Unknown names are rejected rather than ignored,
so a typo fails the call instead of quietly serving the full set. Serve as much of the set as
your harness can back: see [Tool ownership](#tool-ownership) on keeping at least
`computer_batch` and `bash`.

`tools` serves your own definitions alongside the set, in the standard OpenAI tool shape, and
composes with `disable_tools` — your tools are appended after the set's, and disabling one
frees its name:

```python
response = client.chat.completions.create(
    model="n2",
    messages=[...],
    tools=[{
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Fetch an order by id.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    }],
)
```

A definition whose name the set already serves is **refused**, not silently applied, so a
custom `read` or `bash` cannot shadow the tool the model expects — disable the served tool
first. `computer_batch` cannot be redefined at all.

`N2ComputerAgent` implements only the tools in the set (see [Tool ownership](#tool-ownership)),
so it has nowhere to dispatch a custom call — on `TOOL_SET_COMPUTER_USE_LATEST` the model gets
back a recoverable `[ERROR] Invalid <name> call: ... does not expose <name>` tool result and
carries on. To actually run custom tools, drive `chat.completions.create` in your own loop.

`N2ComputerAgent` runs this loop against any computer adapter — see [Navigator n2 loop](#navigator-n2-loop). The [Cua cookbook](examples/navigator_n2/README.md) runs the full current tool set in local Docker. [`examples/navigator_n2_daytona.py`](examples/navigator_n2_daytona.py) is a compact agent using third-party Daytona infrastructure; [Yutori MCP](https://github.com/yutori-ai/yutori-mcp) drives a local Mac (`uvx yutori-mcp computer-use setup`). Model reference: [docs.yutori.com/reference/n2](https://docs.yutori.com/reference/n2).

### `client.browsing` — Browsing API

One-time browser automation on Yutori's cloud browser or on Yutori Local.

| Method | HTTP | Endpoint | Returns |
|--------|------|----------|---------|
| `client.browsing.list(*, limit=None, status=None, cursor=None)` | GET | `/v1/browsing/tasks` | `dict` |
| `client.browsing.create(task, start_url, *, max_steps=None, agent=None, require_auth=None, browser=None, output_schema=None, webhook_url=None, webhook_format=None)` | POST | `/v1/browsing/tasks` | `dict` |
| `client.browsing.get(task_id)` | GET | `/v1/browsing/tasks/{task_id}` | `dict` |

#### `browsing.list`

```python
tasks = client.browsing.list(limit=20, status="succeeded")
```

**Parameters:**
- `limit` (`int`, optional): Max tasks to return. Mapped to the API's `page_size` query param. If omitted, returns all browsing tasks.
- `status` (`str`, optional): Filter by `"running"`, `"succeeded"`, or `"failed"`.
- `cursor` (`str`, optional): Pagination cursor from a previous response's `next_cursor`/`prev_cursor`.

**Returns:** Dict with a `tasks` list plus `total`, `filtered_total`, `summary` counts, `has_more`, and `next_cursor`/`prev_cursor`. The list `status` is a lightweight value derived without a live workflow lookup, so `"running"` also covers queued and not-yet-reconciled tasks — call `browsing.get(task_id)` for the authoritative per-task status.

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
- `max_steps` (`int`, optional): Maximum agent steps.
- `agent` (`str`, optional): Optional agent/model override for the browsing task.
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
| `client.research.list(*, limit=None, status=None, cursor=None)` | GET | `/v1/research/tasks` | `dict` |
| `client.research.create(query, *, user_timezone=None, user_location=None, output_schema=None, webhook_url=None, webhook_format=None)` | POST | `/v1/research/tasks` | `dict` |
| `client.research.get(task_id)` | GET | `/v1/research/tasks/{task_id}` | `dict` |

#### `research.list`

```python
tasks = client.research.list(limit=20, status="succeeded")
```

**Parameters:**
- `limit` (`int`, optional): Max tasks to return. Mapped to the API's `page_size` query param. If omitted, returns all research tasks.
- `status` (`str`, optional): Filter by `"running"`, `"succeeded"`, or `"failed"`.
- `cursor` (`str`, optional): Pagination cursor from a previous response's `next_cursor`/`prev_cursor`.

**Returns:** Dict with a `tasks` list plus `total`, `filtered_total`, `summary` counts, `has_more`, and `next_cursor`/`prev_cursor`. The list `status` is a lightweight value derived without a live workflow lookup, so `"running"` also covers queued and not-yet-reconciled tasks — call `research.get(task_id)` for the authoritative per-task status.

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
| `client.scouts.list(*, limit=None, status=None, cursor=None)` | GET | `/v1/scouting/tasks` | `dict` |
| `client.scouts.get(scout_id)` | GET | `/v1/scouting/tasks/{scout_id}` | `dict` |
| `client.scouts.create(query, *, output_interval=86400, start_timestamp=None, user_timezone=None, user_location=None, output_schema=None, skip_email=None, webhook_url=None, webhook_format=None, is_public=None)` | POST | `/v1/scouting/tasks` | `dict` |
| `client.scouts.update(scout_id, *, query=None, status=None, output_interval=None, user_timezone=None, user_location=None, output_schema=None, skip_email=None, webhook_url=None, webhook_format=None, is_public=None)` | PATCH or POST (status endpoints) | `/v1/scouting/tasks/{scout_id}` or `.../pause|resume|done` | `dict` |
| `client.scouts.delete(scout_id)` | DELETE | `/v1/scouting/tasks/{scout_id}` | `dict` |
| `client.scouts.get_updates(scout_id, *, limit=None, cursor=None)` | GET | `/v1/scouting/tasks/{scout_id}/updates` | `dict` |

#### `scouts.list`

```python
scouts = client.scouts.list(limit=20, status="active")
```

**Parameters:**
- `limit` (`int`, optional): Max scouts to return. Mapped to the API's `page_size` query param.
- `status` (`str`, optional): `"active"`, `"paused"`, or `"done"`.
- `cursor` (`str`, optional): Pagination cursor from a previous response's `next_cursor`/`prev_cursor`.

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

Structured output for the Navigator API (Navigator n1.5 only) is a separate parameter on `client.chat.completions.create(...)`: `json_schema=...` with results on `response.parsed_json`.

## `yutori.navigator`

Opt-in helpers for custom agent loops. They do **not** change the shape of `client.chat.completions.create(...)`. Import paths:

```python
from yutori.navigator import (
    # Models / tool sets
    NAVIGATOR_N1_5_MODEL,
    TOOL_SET_CORE, TOOL_SET_EXPANDED, TOOL_SET_COMPUTER_USE_LATEST, NAVIGATOR_COORDINATE_SCALE,
    TOOL_SET_COMPUTER_USE_20260825, TOOL_SET_COMPUTER_USE_20260830,
    # Navigator n2 loop helpers
    N2Computer, N2ComputerAgent, N2Compactor, N2InlineCompactor,
    parse_n2_tool_calls, execute_n2_computer_call, retain_n2_image_window,
    # Navigator n2 bash/file-tool reference implementations
    ShellFileToolsMixin, format_shell_output, render_image_result, FILE_TOOL_SCRIPT,
    # Screenshots
    aplaywright_screenshot_to_data_url, playwright_screenshot_to_data_url, screenshot_to_data_url,
    # Coordinates
    denormalize_coordinates, normalize_coordinates,
    # Task / prompt formatting
    format_task_with_context, format_user_context, format_stop_and_summarize,
    # Key mapping (Navigator n1.5)
    map_key_to_playwright, map_keys_individual,
    # Payload trimming
    estimate_messages_size_bytes, trim_images_to_fit, trimmed_messages_to_fit,
    # Trimmed request wrappers
    create_trimmed, acreate_trimmed,
    # Misc
    extract_text_content, RunHooksBase,
)
```

### Navigator n2 loop

Imports:

```python
from yutori.navigator import N2Computer, N2ComputerAgent, N2InlineCompactor, TOOL_SET_COMPUTER_USE_LATEST
from yutori.navigator.macos import MacOSComputer  # macOS only; needs the `macos` extra
```

`N2ComputerAgent(*, computer, tool_set=TOOL_SET_COMPUTER_USE_LATEST, completions=None, api_key=None, base_url=None, model="n2", instructions=None, callbacks=None, action_confirmation_callback=None, presentation=None, screenshot_delay=0.5, execution_deadline=None, temperature=None, supports_click_modifiers=False, supports_scroll_modifiers=None, **loop_policies)` drives one n2 conversation against any computer adapter. The loop is async: pass `completions=AsyncYutoriClient().chat.completions` (the sync client's completions will not work), or an `api_key` — the agent then owns its own `AsyncYutoriClient`; close it with `aclose()` or the async context manager. The defaults are already pinned: stable model `n2` and the current dated tool set.

Constructor parameters (the `**loop_policies` keywords have [their own table](#loop-policies)):

| Parameter | What it does |
|---|---|
| `computer` | Your adapter — see [the adapter contract](#the-adapter-contract-n2computer). |
| `tool_set` | A dated set from `SUPPORTED_N2_TOOL_SETS`. The loop serves exactly the set's tools — it has no `disable_tools`/`tools` passthrough, a custom tool call gets a recoverable does-not-expose error, and the adapter must serve every tool in the set (see [Changing the n2 tool set](#changing-the-n2-tool-set)). |
| `instructions` | Optional text inserted as the first user message of the run's history. |
| `callbacks` | List of duck-typed observer objects — see [Callbacks and confirmation](#callbacks-and-confirmation). |
| `action_confirmation_callback` | Opt-in approval gate for model actions — same section. |
| `presentation` | Optional `N2Presentation` event sink for a live UI (what Yutori MCP's overlay uses); its failures never break the run. |
| `screenshot_delay` | Seconds to let the desktop settle before the post-action frame (default `0.5`; skipped when `screenshot` returns native `N2Observation` frames). |
| `execution_deadline` | Optional `time.monotonic()` timestamp; batch members stop executing once it passes. |
| `temperature` | Sampling passthrough; `None` uses the server's pinned defaults. |
| `supports_click_modifiers` / `supports_scroll_modifiers` | Declare only when the adapter can hold a modifier for that whole gesture (the scroll setting defaults to the click one). An undeclared modified action is rejected with a model-visible error, never silently executed unmodified. |

`MacOSComputer` is the native macOS adapter — CuaDriver session, capture/input, shell lifecycle, cancellation, recovery, and the optional presentation overlay. It is what Yutori MCP runs; local shell execution stays off unless the caller enables it.

Keyboard actions carry a focus guard (`verify_focus=True`): the adapter records the frontmost application at every screenshot and re-checks it right before `type_text`, `press_key`, and `hotkey`. If focus moved since the frame the model reasoned over — a dialog, a notification, a slow launch — the keys are not sent and the model receives `MacOSFocusChangedError` with a fresh frame as the tool result. The probe uses LaunchServices (`lsappinfo`) and needs no permission grant; when it cannot answer, the guard fails open. Pass `verify_focus=False` to disable it, or `frontmost_probe=` to substitute the probe in tests.

#### Running and resuming

`run(task)` (a task string or a message list) is an async generator yielding `{"output": [items], "usage": {...}, "message": {...}}` per model turn (`message` is the raw assistant message) and `{"output": [result items], "usage": {}}` per executed tool call. The items are responses-style dicts, the same shapes kept in `agent.trajectory`:

| Item `type` | Shape |
|---|---|
| `message` | Assistant text: `content` is `[{"type": "output_text", "text": ...}]`; the model's thinking rides on `"reasoning"`. |
| `function_call` | One tool call: `call_id`, `name`, `arguments` (a JSON string). |
| `function_call_output` | Its result: `output` is a string, or `{"type": "input_image", "image_url": ..., "result": <text>}` when a frame or image rides along. |

The run ends when the model answers with text and no tool calls, a callback's `on_run_continue` returns `False`, a `max_steps`/`agent_timeout_seconds` budget is spent, or the next request would exceed `context_window_tokens`; `agent.stopped_by` records which (`"final_answer"`, `"callback"`, `"max_steps"`, `"timeout"`, `"context_limit"`). Final text passes through untouched. `resume(message)` appends a user message to `agent.trajectory` and continues the same conversation, so the caller decides what a text-only turn means — answer a question, steer, or stop; a caller who wants an explicit completion convention (say, a `[DONE]` marker) asks for it in `system_prompt` and resumes until it appears. Each request echoes the previous response's `request_id` as `prev_request_id` (`run()` starts a new chain, `resume()` continues it), so the platform reports the whole conversation as one session.

Request rendering: the run starts without a screenshot — the model asks for one with a `screenshot` batch member. Frames are sent at the handler's own capture size, re-encoded to `image_format` (never resized); older frames are replaced by `[older image omitted]`; prior-turn reasoning is re-sent as the assistant message's `reasoning`/`reasoning_content` fields. Two responses are re-requested once instead of kept: a turn whose text carries literal `<tool_call>` markup but parsed no tool calls (retried with a format reminder, `TOOL_CALL_FORMAT_NUDGE`; the check is `needs_tool_call_format_nudge`), and a turn cut off at the output cap (`finish_reason == "length"`) with no tool calls. Neither attempt enters the kept trajectory.

#### The adapter contract (`N2Computer`)

`computer` is any object with the async handler surface below. The always-required core for the current tool set is exported as the `N2Computer` protocol — structural, so annotate your adapter (`computer: N2Computer = MyComputer(...)`) to have a type checker verify it. The loop itself stays duck-typed; an adapter for an older tool set may implement less. GUI coordinates arrive as **native pixels** — the loop maps the model's normalized 0–1000 space onto the dimensions of the frame it sent.

Required (all `async def`):

| Method | Contract |
|---|---|
| `screenshot() -> str` | The current frame as a data URL or bare base64 (any Pillow-decodable format). Capture at the size the model should see: the handler defines the viewport, with DPR scaling removed — the SDK re-encodes but never resizes. 1280×720 is a good starting point when you control the desktop size; a native-size desktop (a fixed-size sandbox, someone's own monitor) also works and just spends more tokens per screenshot. A handler that downscales captures instead must scale incoming coordinates back up: the loop maps the model's normalized coordinates onto the capture's dimensions, so they arrive in capture space, not desktop space. May instead return a native `N2Observation`, as `MacOSComputer` does, which unlocks the optional `wait_for_change`/`poll_after_action` hooks and skips the settle delay. |
| `click(x, y, button="left")` | `button` is `"left"`, `"right"`, or `"middle"`. |
| `double_click(x, y)` | — |
| `move(x, y)` | Move the pointer without clicking. |
| `drag(path)` | `path` is exactly two `{"x", "y"}` dicts: start and end. |
| `scroll(x, y, scroll_x, scroll_y)` | Pixel deltas at (x, y); positive `scroll_y` is down, positive `scroll_x` is right (exactly one is nonzero per call). The loop converts the model's notches at one notch = 10% of the screen dimension; a backend that wants the model's own units should take `model_action=` (below) and read its `direction`/`amount`. A handler without horizontal scrolling should raise on a nonzero `scroll_x`, failing that one action rather than the run. |
| `type(text)` | Type into the focused element. |
| `keypress(keys)` | One chord per call, as a list: `"ctrl+c"` arrives as `["ctrl", "c"]`, and a space-separated sequence (`"down down enter"`) becomes one call per combo. Names are pre-normalized to the SDK vocabulary (`Return` → `enter`, `ArrowUp` → `up`, `meta`/`super` → `cmd`, …); names outside it pass through lowercased for the handler to accept or reject. |
| `wait(ms)` | Idle for `ms` milliseconds. |
| `run_bash_command(command, timeout=120.0, run_in_background=False) -> str` | Run in a persistent bash session — the working directory persists across calls — and return the rendered output (see [Tool ownership](#tool-ownership)). |
| `read_file(file_path, offset=1, limit=2000) -> str \| dict` | `cat -n`-numbered text; return `{"text", "image_url"}` for an image file so the model sees the image itself. |
| `write_file(file_path, content) -> str` | Create or overwrite; return the confirmation text. |
| `edit_file(file_path, old_string, new_string, replace_all=False) -> str` | Exact-string replacement; return the result text. |

Optional extensions the loop probes for:

| Extension | Serves | Without it |
|---|---|---|
| `triple_click(x, y)` | Native multi-click timing | Falls back to `double_click` then `click`. |
| `hold_key(key, ms)` | `hold_key` with a duration | The action fails with a model-visible error; the run continues. |
| `key_down(key)` / `key_up(key)` | Durationless `hold_key` (held until the next action) | Same. |
| `left_mouse_down(x=None, y=None)` / `left_mouse_up(x=None, y=None)` | `mouse_down`/`mouse_up` batch members | Same. |
| `release_held_mouse_button()` | Cleanup after a batch or a cancellation | No cleanup call is made. |
| `get_dimensions() -> (width, height)` | The coordinate space for a turn with no frame in history | The loop measures one unsent screenshot. |
| `modifier=` keyword on click/scroll handlers | Modified gestures, with `supports_click_modifiers`/`supports_scroll_modifiers` declared | Modified actions are rejected. |
| `model_action=` keyword on any GUI handler | Receives the model's untranslated call (`{"action": name, **arguments}` — a click's `coordinates` still normalized 0–1000, a scroll's `direction`/`amount` in notches) alongside the translated arguments | The handler sees only the loop's pixel translation. |

Failure conventions: a GUI handler reports failure by returning `{"success": False, "error": ...}` or by raising — either halts the batch at that member, and the model sees the completed `[i:name]` lines, the halt line, and a fresh frame; the run continues. An exception from a `bash`/file handler becomes a recoverable `[ERROR] <tool> failed: ...` result. *Expected* tool outcomes — file not found, `old_string` not unique, a command timeout — must be **returned** as result text (`ERROR: ...`, `Command timed out after Ns`), never raised: those exact strings are the contract the model relies on.

#### Tool ownership

The split follows one principle: the adapter is responsible for tool implementations (they are system-dependent); the loop is responsible for tool transformation and batching.

| Tool | Implemented by | Notes |
|---|---|---|
| `computer_batch` (and the older sets' standalone GUI actions and `screenshot`) | The loop | Validates the batch, maps coordinates, normalizes key names, runs members in order, stops at the first error, captures the one post-batch frame, and formats the `[i:name]` result. The adapter only executes the GUI primitives above. |
| `bash` | Adapter: `run_bash_command` | The loop validates arguments and passes the handler's text through (a whitespace trim and a 256K runaway backstop aside). `format_shell_output(output, exit_code)` renders the expected shape: an `Exit code N` header on nonzero exit, `(Bash completed with no output)`, the 30K truncation cap. A `run_in_background=True` call returns immediately with a confirmation naming the log file the output streams to (for the model to `read`) and the process id — `examples/navigator_n2/direct_x11_adapter.py` and `examples/navigator_n2/cua_adapter.py` have the exact wording. |
| `read` / `write` / `edit` | Adapter: `read_file` / `write_file` / `edit_file` | Same pass-through; `ShellFileToolsMixin` (next section) is the reference implementation. |
| Older sets: `shell_command`, `grep`/`glob`, and the browser set's `goto_url` | Adapter: `run_shell_command`, `grep_files`/`glob_files`, `goto_url` | Duck-typed; a missing handler fails that call recoverably. |

Conventions the model relies on, beyond the exact result strings: n2 performs best when the full five-tool set is served — if you must run a reduced dated set, keep at least `computer_batch` and `bash`, because the model does shell work through the `bash` tool and driving a terminal through the GUI instead is far less efficient; and a `read` of an image file shows the model the image itself. Integrating your own infrastructure is therefore three pieces — the GUI primitives over your capture/input APIs, `run_bash_command` over its shell, and the file tools. Choose a starting point based on where the adapter runs relative to the desktop:

- **Direct X11 Linux** — if the adapter runs on the desktop host and can access its display, shell, and filesystem directly, start from [`examples/navigator_n2/direct_x11_adapter.py`](examples/navigator_n2/direct_x11_adapter.py). The host may be a local machine or a VM; the distinguishing feature is that the adapter calls X11 and OS primitives directly.
- **API-backed desktop** — if screenshot, input, shell, and file operations cross an API boundary, whether to local sandbox software or a remote service, start from [`examples/navigator_n2/cua_adapter.py`](examples/navigator_n2/cua_adapter.py) as a full-surface structural example. Adapt its provider calls, input units, result types, and process/file handling to your API while preserving the SDK-facing behavior: unit conversion at the boundary, `bash` with a persistent working directory (including timeout and background-run forms), and the file tools via the mixin. [`examples/navigator_n2_daytona.py`](examples/navigator_n2_daytona.py) is a second, more compact API-backed example over REST primitives.
- **macOS** — `yutori.navigator.macos.MacOSComputer` is a shipped runtime, not an example; use it directly.

All three expose the same SDK-facing computer-handler contract; their infrastructure-facing interfaces differ.

#### File tools over a shell (`ShellFileToolsMixin`)

For any sandbox whose shell has `python3` (stdlib only) on PATH, mix `yutori.navigator.ShellFileToolsMixin` into the adapter and implement two hooks (both `async def`); the mixin then provides all five file-tool handlers with the exact expected result strings — `cat -n` numbering, the sha256 read-before-edit gate, `[... output truncated, N more chars ...]` caps, image reads rendered visible via `render_image_result`:

| Hook | Contract |
|---|---|
| `run_sandbox_shell(command, *, timeout_seconds)` | Run one shell command in the sandbox; return any object with `stdout`, `stderr`, and `returncode` attributes. |
| `file_tool_cwd() -> str` | The directory relative paths resolve against (usually the bash tool's tracked cwd). |

The shell is one transport, not the contract: to implement the file tools without one — as `MacOSComputer` does natively — reproduce the contracts of the sandbox-side `FILE_TOOL_SCRIPT` (`yutori/navigator/sandbox_tools.py`).

#### Callbacks and confirmation

`callbacks` is a list of duck-typed objects; every hook is optional and `async`:

| Hook | Fires |
|---|---|
| `on_run_start(kwargs, items)` / `on_run_end(kwargs, old_items, new_items)` | Around each `run()`/`resume()`; `on_run_end` always fires. |
| `on_run_continue(kwargs, old_items, new_items) -> bool` | Before each model turn; return `False` to stop the run (`stopped_by == "callback"`). |
| `on_api_start(api_kwargs)` / `on_api_end(api_kwargs, response)` | Around each chat-completions request. |
| `on_usage(usage)` | After each response, with its raw usage dict. |
| `on_screenshot(raw_base64, "screenshot_after")` | For each captured post-action frame. |
| `on_computer_call_start(item)` / `on_computer_call_end(item, result_items)` | Around each executed tool call. |
| `on_text(item)` | For each assistant `message` item. |
| `on_compaction({"items_before": int, "items_after": int})` | After each applied compaction. |

`action_confirmation_callback` (sync or async) is an opt-in approval gate. When set, every action except `screenshot`, `wait`, `mouse_move`, and `scroll` requires confirmation; the callback receives `{"call_id", "tool_name", "arguments", "actions"}` — `actions` is one `{"action": name, **arguments}` entry per batch member (or the single call) — and a falsy return refuses the call with a model-visible `[ERROR] Action was not confirmed by the user.` result; the run continues.

#### Loop policies

The `**loop_policies` keywords:

| Keyword | Default | What it controls |
|---|---|---|
| `system_prompt` | `None` | Sent as a system message ahead of the conversation; the server appends it under its own prompt's `# User Instructions` header (see **System prompt** in the Navigator n2 section). |
| `image_format` | `"webp"` | The encoding request images are converted to (pass-through when the source already matches). The SDK never resizes. |
| `max_completion_tokens` | `20480` | Output budget per model call. |
| `reasoning_effort` | `None` | Passed through when set: `none`/`low`/`medium`/`xhigh`. The server also accepts OpenAI's `high` (→ `xhigh`) and `minimal` (→ `low`); unknown values fall back to the server default, `medium`. |
| `api_timeout_seconds` | `600` | Per-request timeout sent with each model call. `None` uses the client's default. |
| `context_window_tokens` | `128000` | The run ends with `stopped_by == "context_limit"` once the last `prompt_tokens` + `max_completion_tokens` + a 4096-token margin would exceed it. `None` disables the check. |
| `tool_call_timeout_seconds` | `900` | Budget for executing one tool call (a whole batch); on expiry the model sees `ERROR_TIMEOUT: <call_id> timed out after N seconds`. `None` disables it. |
| `completion_kwargs` | `None` | Extra fields merged into every chat-completions request (e.g. `top_p`), for callers who want explicit sampling settings. |
| `max_steps` / `agent_timeout_seconds` | `None` | Turn and wall-clock budgets per `run()`/`resume()` call (`stopped_by == "max_steps"` / `"timeout"`). |
| `compactor` | `"auto"` | `"auto"` attaches a fresh `N2InlineCompactor`, so long runs are checkpointed instead of ending at the context limit. Pass `None` to disable, or an `N2Compactor` for a custom policy; it is called before each model request and the context-limit guard. |

### N2 context compaction

`N2InlineCompactor` — the loop's default (`compactor="auto"`) — implements a usage-triggered, tail-retaining policy: it preserves the initial user request, replaces older turns with a model-written working checkpoint, and keeps recent complete turns verbatim. The defaults reproduce the regime n2 performs best in: a 64K-token working context with this same compaction prompt, triggered at 53,760 tokens (64,000 minus 10,240 of headroom). Keep the default compactor for best performance — `compactor=None` runs the model past its 64K working context toward the 128k serving limit, and a custom policy departs from the compaction prompt the model expects. To customize the trigger or retention, construct it explicitly:

```python
from yutori.navigator import N2ComputerAgent, N2InlineCompactor

agent = N2ComputerAgent(
    computer=computer,
    completions=client.chat.completions,
    model="n2",
    compactor=N2InlineCompactor(
        trigger_input_tokens=53_760,
        keep_last_n_turns=5,
        tail_token_budget=16_384,
    ),
)
```

The defaults trigger only when the previous actor request used more than 53,760 prompt tokens, retain at most
five actor turns within an estimated 16,384-token tail, target a 9,000-character checkpoint, and try at most
three compaction responses. The compaction request uses the actor's same completion surface, system prompt,
tool set, image policy, sampling fields, output budget, and timeout. A successful request remains in the
`prev_request_id` chain; tool-calling, empty, or malformed responses are retried. History is replaced only
after a valid tagged checkpoint. If no complete turn fits in the retained tail, the latest screenshot is
restored after the checkpoint so the next actor call is not image-blind. The first actor call after replacement
is exempt from another compaction.

For another policy, implement `N2Compactor.compact(...)`. Existing compactors may continue returning
`list[dict] | None`; implementations that accept the optional `context` keyword receive an
`N2CompactionContext` with the exact actor request and cancellation policy and may return
`N2CompactionResult` to advance the request chain. A new `run()` resets usage and optional compactor state;
`resume()` continues both.

### Harness-owned completion requests

`agent.completion_request(extra_messages=None, *, items=None)` returns the actor's exact next Chat Completions request as a `dict` — system prompt, windowed messages, sampling fields, tool set, and request chaining — without advancing the loop or mutating the trajectory. Pass `extra_messages` (a list of chat-format dicts) to append harness-owned messages after the trajectory, for example a step-cap "stop and summarize" probe: `await client.chat.completions.create(**agent.completion_request([nudge]))`. The call and its response stay the caller's own; the trajectory is not changed. `items` overrides the rendered trajectory (the loop's own steps pass their in-flight working set).

### Screenshot helpers

| Helper | Signature | Description |
|--------|-----------|-------------|
| `screenshot_to_data_url` | `(image_bytes: bytes, *, resize_to=(1280, 800), source_format=None, webp_quality=None) -> str` | Convert raw screenshot bytes into `data:image/webp;base64,...`. Pillow is a required SDK dep. |
| `playwright_screenshot_to_data_url` | `(page, *, resize_to=(1280, 800), webp_quality=None) -> str` | Capture a sync Playwright page screenshot (JPEG, q=75) and re-encode as WebP. |
| `aplaywright_screenshot_to_data_url` | `(page, *, resize_to=(1280, 800), webp_quality=None) -> str` | Async version of the above. |

Default quality is WebP q=90 (or q=30 for PNG sources).

### Coordinate helpers

The Navigator emits tool-call coordinates in a normalized `NAVIGATOR_COORDINATE_SCALE × NAVIGATOR_COORDINATE_SCALE` (1000×1000) space.

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

### Key mapping (Navigator n1.5)

Navigator n1.5 returns lowercase key names (`ctrl+c`, `enter`, `down`) which must be converted for Playwright.

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
| `create_trimmed` | `(completions, messages, *, model=NAVIGATOR_N1_5_MODEL, max_bytes=9_500_000, keep_recent=6, **kwargs) -> ChatCompletion` | Sync. Expects `completions` to quack like `ChatCompletions`. |
| `acreate_trimmed` | `(completions, messages, *, model=NAVIGATOR_N1_5_MODEL, max_bytes=9_500_000, keep_recent=6, **kwargs) -> ChatCompletion` | Async. |

Additionally, `yutori.navigator.loop.update_trimmed_history(messages, request_messages=None, *, max_bytes=..., keep_recent=...)` is available for long-lived loops that keep a complete replayable history separate from the trimmed request copy. It returns `(request_messages, size_bytes, removed)`.

### Miscellaneous helpers

| Helper | Signature | Description |
|--------|-----------|-------------|
| `extract_text_content` | `(content) -> str \| None` | Normalize OpenAI-style content (string, list of text/image blocks, or object with `.text`) into a single text string. Returns `None` for empty/missing content. |
| `RunHooksBase` | class | Async no-op lifecycle hooks with `on_agent_start`, `on_llm_start`, `on_llm_end`, `on_tool_start`, `on_tool_end`, `on_agent_end`. Intentionally not a drop-in of the OpenAI Agents SDK — mirrors phases, not exact signatures. Not wired into `client.chat` automatically. |

### `yutori.navigator.tools`

Packaged JavaScript reference implementations for the Navigator n1.5 expanded browser tool set. The scripts are shipped as `.js` files inside the wheel (`yutori.navigator.tools.js`).

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
| `yutori browse list [--limit N] [--status running\|succeeded\|failed] [--cursor C]` | List browsing tasks (rich table). If `--limit` is omitted, returns all tasks. |
| `yutori browse run TASK START_URL [--max-steps N] [--agent NAME] [--require-auth] [--browser cloud\|local]` | Submit a browsing task. Exit code 1 if the API rejects the task (`status: failed`). |
| `yutori browse get TASK_ID` | Get status and result (truncates output to 2000 chars). |

### Research

| Command | Description |
|---------|-------------|
| `yutori research list [--limit N] [--status running\|succeeded\|failed] [--cursor C]` | List research tasks (rich table). If `--limit` is omitted, returns all tasks. |
| `yutori research run QUERY [--timezone/-tz TZ] [--location LOC]` | Submit a research task. Exit code 1 if the API rejects the task (`status: failed`). |
| `yutori research get TASK_ID` | Get status and result (truncates output to 2000 chars). |

### Scouts

| Command | Description |
|---------|-------------|
| `yutori scouts list [--limit N] [--status active\|paused\|done]` | List scouts (rich table). |
| `yutori scouts get SCOUT_ID` | Show scout detail. |
| `yutori scouts create [--query/-q Q] [--interval/-i hourly\|daily\|weekly] [--timezone/-tz TZ]` | Create a scout. Prompts for `query` interactively if `-q` is omitted. Only the three named intervals are accepted; for arbitrary seconds, call `scouts.create` from Python. Exit code 1 if the API rejects the scout (`status: failed`). |
| `yutori scouts delete SCOUT_ID [--force/-f]` | Delete a scout. Prompts for confirmation unless `--force`. |

### Usage

| Command | Description |
|---------|-------------|
| `yutori usage [--period 24h\|7d\|30d\|90d]` | API usage statistics (rate limits + activity counts). Default `24h`. |

## Dependencies

Required (installed with `pip install yutori` or `uv add yutori`):

| Package | Version | Purpose |
|---------|---------|---------|
| `httpx` | `>=0.26.0,<1.0.0` | HTTP client for browsing/research/scouting. |
| `openai` | `>=1.55.3` | Backs `client.chat` (provides `ChatCompletion` types). |
| `pillow` | `>=10.0.0` | Screenshot helpers in `yutori.navigator`. |
| `typer` | `>=0.9.0` | CLI framework. |
| `rich` | `>=13.0.0` | Terminal output for the CLI. |

Optional extras:

| Extra | Packages | Purpose |
|-------|----------|---------|
| `dev` | `pytest`, `pytest-asyncio`, `ruff`, `build` | Development tooling. |
| `examples` | `loguru`, `playwright`, `pydantic`, `tenacity` | Running the `examples/` scripts. Pydantic is also the library to install if you want to pass Pydantic models to `output_schema=`. |
| `macos` | `cua-driver==0.19.3` | Native macOS CUA driver for Navigator n2 loops. |

## Error handling example

```python
from yutori import YutoriClient, APIError, APIConnectionError, AuthenticationError

try:
    client = YutoriClient(api_key="invalid-key")
    client.get_usage()
except AuthenticationError as e:
    print(f"Invalid API key: {e}")
except APIConnectionError as e:
    print(f"Connection failed: {e}")
except APIError as e:
    print(f"API error (status {e.status_code}): {e.message}")
```
