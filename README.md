# Yutori Python SDK

[![PyPI version](https://img.shields.io/pypi/v/yutori.svg)](https://pypi.org/project/yutori/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

The official Python library for the Yutori API.

Yutori provides APIs for building web agents that autonomously execute tasks on the web. The SDK offers both synchronous and asynchronous clients with full type annotations.

## Documentation

- [API Reference](https://docs.yutori.com)
- [Platform Dashboard](https://platform.yutori.com)

## Installation

```bash
pip install yutori
```

## Usage

### Getting an API Key

1. Sign up at [platform.yutori.com](https://platform.yutori.com)
2. Navigate to Settings to create an API key
3. API keys start with `yt-`

### Basic Example

```python
from yutori import YutoriClient

client = YutoriClient(api_key="yt-...")

# Check your API key usage
print(client.get_usage())
```

The SDK reads the `YUTORI_API_KEY` environment variable if no key is provided:

```python
client = YutoriClient()  # Uses YUTORI_API_KEY env var
```

## API Overview

The Yutori API provides four main capabilities:

| API          | Description                               | SDK Namespace     |
| ------------ | ----------------------------------------- | ----------------- |
| **n1**       | Pixels-to-actions LLM for browser control | `client.chat`     |
| **Browsing** | One-time browser automation tasks         | `client.browsing` |
| **Research** | Deep web research using 100+ tools        | `client.research` |
| **Scouting** | Continuous web monitoring on a schedule   | `client.scouts`   |

## n1 API

The n1 API is a pixels-to-actions LLM that processes screenshots and predicts browser actions (click, type, scroll, etc.). It follows the OpenAI Chat Completions interface:

```python
response = client.chat.completions.create(
    model="n1-latest",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Describe the screenshot and search for Yutori."
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://upload.wikimedia.org/wikipedia/commons/5/53/Google_homepage_%28as_of_January_2024%29.jpg"
                    }
                }
            ]
        }
    ]
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

If you don't want to manage your own browser infrastructure, use the Browsing API which calls n1 on a cloud browser.

## Browsing API

Run one-time browser automation tasks. An AI agent operates a cloud browser to complete your task.

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

### Structured Output with Webhooks

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

### Structured Output

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

## Scouting API

Scouts run on a configurable schedule to monitor the web and send notifications when relevant updates occur.

```python
from yutori import YutoriClient

client = YutoriClient(api_key="yt-...")

# Create a scout that monitors for updates
scout = client.scouts.create(
    query="Tell me about the latest news, product updates, and announcements about Yutori",
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
scout = client.scouts.create(
    query="Tell me about the latest news and announcements about Yutori",
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
| `AuthenticationError` | 401         | Invalid or missing API key         |
| `APIError`            | 4xx, 5xx    | General API error with status code |

## Configuration

```python
from yutori import YutoriClient

client = YutoriClient(
    api_key="yt-...",                          # Required (or set YUTORI_API_KEY)
    base_url="https://api.yutori.com/v1",      # Default
    timeout=30.0,                               # Request timeout in seconds
)
```

## Requirements

- Python 3.9+
- [httpx](https://www.python-httpx.org/) for HTTP requests
- [openai](https://github.com/openai/openai-python) for the n1 chat API

## Examples

See [examples/](examples/) for complete working examples, including a browser automation agent using the n1 API.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
