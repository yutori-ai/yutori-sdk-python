"""Tests for the async AsyncYutoriClient."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from yutori import APIError, AsyncYutoriClient, AuthenticationError


class TestAsyncYutoriClientInit:
    @pytest.mark.asyncio
    async def test_init_with_api_key(self):
        client = AsyncYutoriClient(api_key="yt-test-key")
        assert client._api_key == "yt-test-key"
        assert client._base_url == "https://api.yutori.com/v1"
        await client.close()

    def test_init_without_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("YUTORI_API_KEY", raising=False)
        monkeypatch.setattr("yutori.auth.credentials.load_config", lambda: None)
        with pytest.raises(AuthenticationError):
            AsyncYutoriClient(api_key="")

    def test_init_with_none_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("YUTORI_API_KEY", raising=False)
        monkeypatch.setattr("yutori.auth.credentials.load_config", lambda: None)
        with pytest.raises(AuthenticationError):
            AsyncYutoriClient(api_key=None)

    @pytest.mark.asyncio
    async def test_init_from_env_var(self, monkeypatch):
        monkeypatch.setenv("YUTORI_API_KEY", "yt-env-key")
        client = AsyncYutoriClient()
        assert client._api_key == "yt-env-key"
        await client.close()


@pytest.mark.asyncio
class TestAsyncYutoriClientGetUsage:
    USAGE_RESPONSE = {
        "num_active_scouts": 2,
        "active_scout_ids": ["id-1", "id-2"],
        "rate_limits": {
            "requests_today": 100,
            "daily_limit": 10000,
            "remaining_requests": 9900,
            "reset_at": "2026-03-04T00:00:00+00:00",
            "status": "available",
        },
        "n1_rate_limits": {
            "requests_today": 50,
            "daily_limit": 50000,
            "remaining_requests": 49950,
            "reset_at": "2026-03-04T00:00:00+00:00",
            "per_second_limit": 20,
        },
        "activity": {
            "period": "24h",
            "scout_runs": 10,
            "browsing_tasks": 3,
            "research_tasks": 2,
            "n1_calls": 50,
        },
    }

    def _mock_usage_response(self, period: str = "24h"):
        import json

        data = {**self.USAGE_RESPONSE, "activity": {**self.USAGE_RESPONSE["activity"], "period": period}}
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = json.dumps(data).encode()
        mock_response.json.return_value = data
        return mock_response

    async def test_get_usage_success(self):
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=self._mock_usage_response()):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.get_usage()
                assert result["num_active_scouts"] == 2
                assert result["activity"]["period"] == "24h"

    async def test_get_usage_with_period(self):
        with patch.object(
            httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=self._mock_usage_response("30d")
        ) as mock_get:
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.get_usage(period="30d")
                assert result["activity"]["period"] == "30d"
                call_kwargs = mock_get.call_args[1]
                assert call_kwargs["params"] == {"period": "30d"}


@pytest.mark.asyncio
class TestAsyncScoutsNamespace:
    async def test_scouts_list(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"scouts": []}'
        mock_response.json.return_value = {"scouts": []}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response) as mock_get:
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.scouts.list(limit=10, status="active")
                assert result == {"scouts": []}
                params = mock_get.call_args[1]["params"]
                assert params["page_size"] == 10
                assert params["limit"] == 10
                assert params["status"] == "active"

    async def test_scouts_get(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"id": "scout-123"}'
        mock_response.json.return_value = {"id": "scout-123"}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.scouts.get("scout-123")
                assert result["id"] == "scout-123"

    async def test_scouts_create(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"id": "new-scout"}'
        mock_response.json.return_value = {"id": "new-scout"}

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.scouts.create(query="Monitor site")
                assert result["id"] == "new-scout"

    async def test_scouts_update_status(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"id": "scout-123", "status": "paused"}'
        mock_response.json.return_value = {"id": "scout-123", "status": "paused"}

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.scouts.update("scout-123", status="paused")
                assert result["status"] == "paused"
                assert "/pause" in mock_post.call_args[0][0]

    async def test_scouts_update_fields(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"id": "scout-123", "query": "new query"}'
        mock_response.json.return_value = {"id": "scout-123", "query": "new query"}

        with patch.object(httpx.AsyncClient, "patch", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.scouts.update("scout-123", query="new query")
                assert result["query"] == "new query"

    async def test_scouts_update_status_and_fields_raises(self):
        async with AsyncYutoriClient(api_key="yt-test") as client:
            with pytest.raises(ValueError, match="Cannot update status and other fields simultaneously"):
                await client.scouts.update("scout-123", status="paused", query="new query")

    async def test_scouts_delete(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b""

        with patch.object(httpx.AsyncClient, "delete", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.scouts.delete("scout-123")
                assert result == {}

    async def test_scouts_get_updates(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"updates": []}'
        mock_response.json.return_value = {"updates": []}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.scouts.get_updates("scout-123")
                assert "updates" in result


@pytest.mark.asyncio
class TestAsyncBrowsingNamespace:
    async def test_browsing_create(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"task_id": "task-123"}'
        mock_response.json.return_value = {"task_id": "task-123"}

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.browsing.create(
                    task="Click login",
                    start_url="https://example.com",
                )
                assert result["task_id"] == "task-123"

    async def test_browsing_get(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"task_id": "task-123", "status": "succeeded"}'
        mock_response.json.return_value = {"task_id": "task-123", "status": "succeeded"}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.browsing.get("task-123")
                assert result["status"] == "succeeded"


@pytest.mark.asyncio
class TestAsyncResearchNamespace:
    async def test_research_create(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"task_id": "research-123"}'
        mock_response.json.return_value = {"task_id": "research-123"}

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.research.create(query="Find AI funding")
                assert result["task_id"] == "research-123"

    async def test_research_get(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"task_id": "research-123", "status": "succeeded"}'
        mock_response.json.return_value = {"task_id": "research-123", "status": "succeeded"}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.research.get("research-123")
                assert result["status"] == "succeeded"


@pytest.mark.asyncio
class TestAsyncPydanticSchemaIntegration:
    """Test that Pydantic models are resolved to JSON schema dicts in async payloads."""

    @staticmethod
    def _make_mock_response():
        mock = MagicMock(spec=httpx.Response)
        mock.status_code = 200
        mock.content = b'{"task_id": "t-1"}'
        mock.json.return_value = {"task_id": "t-1"}
        return mock

    class _FakeModel:
        @classmethod
        def model_json_schema(cls):
            return {"type": "object", "properties": {"name": {"type": "string"}}}

    async def test_browsing_create_with_model_class(self):
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=self._make_mock_response()
        ) as mock_post:
            async with AsyncYutoriClient(api_key="yt-test") as client:
                await client.browsing.create(task="t", start_url="https://x.com", output_schema=self._FakeModel)
                payload = mock_post.call_args[1]["json"]
                assert payload["output_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}

    async def test_browsing_create_with_model_instance(self):
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=self._make_mock_response()
        ) as mock_post:
            async with AsyncYutoriClient(api_key="yt-test") as client:
                await client.browsing.create(task="t", start_url="https://x.com", output_schema=self._FakeModel())
                payload = mock_post.call_args[1]["json"]
                assert payload["output_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}

    async def test_research_create_with_model_class(self):
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=self._make_mock_response()
        ) as mock_post:
            async with AsyncYutoriClient(api_key="yt-test") as client:
                await client.research.create(query="q", output_schema=self._FakeModel)
                payload = mock_post.call_args[1]["json"]
                assert payload["output_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}

    async def test_scouts_create_with_model_class(self):
        mock = self._make_mock_response()
        mock.content = b'{"id": "s-1"}'
        mock.json.return_value = {"id": "s-1"}
        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock) as mock_post:
            async with AsyncYutoriClient(api_key="yt-test") as client:
                await client.scouts.create(query="q", output_schema=self._FakeModel)
                payload = mock_post.call_args[1]["json"]
                assert payload["output_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}

    async def test_scouts_update_with_model_class(self):
        mock = self._make_mock_response()
        mock.content = b'{"id": "s-1"}'
        mock.json.return_value = {"id": "s-1"}
        with patch.object(httpx.AsyncClient, "patch", new_callable=AsyncMock, return_value=mock) as mock_patch:
            async with AsyncYutoriClient(api_key="yt-test") as client:
                await client.scouts.update("s-1", output_schema=self._FakeModel)
                payload = mock_patch.call_args[1]["json"]
                assert payload["output_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}

    async def test_scouts_update_with_model_instance(self):
        mock = self._make_mock_response()
        mock.content = b'{"id": "s-1"}'
        mock.json.return_value = {"id": "s-1"}
        with patch.object(httpx.AsyncClient, "patch", new_callable=AsyncMock, return_value=mock) as mock_patch:
            async with AsyncYutoriClient(api_key="yt-test") as client:
                await client.scouts.update("s-1", output_schema=self._FakeModel())
                payload = mock_patch.call_args[1]["json"]
                assert payload["output_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}


@pytest.mark.asyncio
class TestAsyncChatNamespace:
    async def test_chat_completions(self):
        from openai.types.chat import ChatCompletion, ChatCompletionMessage
        from openai.types.chat.chat_completion import Choice

        mock_completion = ChatCompletion(
            id="chatcmpl-123",
            choices=[
                Choice(
                    finish_reason="stop",
                    index=0,
                    message=ChatCompletionMessage(role="assistant", content="click"),
                )
            ],
            created=1234567890,
            model="n1-latest",
            object="chat.completion",
        )

        with patch("yutori._async.chat.AsyncOpenAI") as MockAsyncOpenAI:
            mock_openai_client = MagicMock()
            mock_openai_client.chat.completions.create = AsyncMock(return_value=mock_completion)
            mock_openai_client.close = AsyncMock()
            MockAsyncOpenAI.return_value = mock_openai_client

            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.chat.completions.create(
                    messages=[{"role": "user", "content": "Click login"}],
                )
                assert result.choices[0].message.content == "click"

    async def test_n1_helper_acreate_trimmed_public_helper_uses_trimmed_copy(self):
        from copy import deepcopy

        from openai.types.chat import ChatCompletion, ChatCompletionMessage
        from openai.types.chat.chat_completion import Choice

        from yutori.n1 import acreate_trimmed
        from yutori.n1.payload import trimmed_messages_to_fit

        mock_completion = ChatCompletion(
            id="chatcmpl-123",
            choices=[
                Choice(
                    finish_reason="stop",
                    index=0,
                    message=ChatCompletionMessage(role="assistant", content="click"),
                )
            ],
            created=1234567890,
            model="n1-latest",
            object="chat.completion",
        )
        original_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Check the page"},
                    {"type": "image_url", "image_url": {"url": "A" * 5000}},
                ],
            },
            {
                "role": "tool",
                "content": [
                    {"type": "text", "text": "Tool output"},
                    {"type": "image_url", "image_url": {"url": "A" * 5000}},
                ],
            },
        ]
        original_snapshot = deepcopy(original_messages)

        with patch("yutori._async.chat.AsyncOpenAI") as MockAsyncOpenAI:
            mock_openai_client = MagicMock()
            mock_openai_client.chat.completions.create = AsyncMock(return_value=mock_completion)
            mock_openai_client.close = AsyncMock()
            MockAsyncOpenAI.return_value = mock_openai_client

            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await acreate_trimmed(
                    client.chat.completions,
                    original_messages,
                    max_bytes=100,
                    keep_recent=1,
                )
                assert result.choices[0].message.content == "click"

        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        sent_messages = call_kwargs["messages"]
        assert sent_messages is not original_messages
        assert sent_messages == trimmed_messages_to_fit(original_messages, max_bytes=100, keep_recent=1)[0]
        assert original_messages == original_snapshot

    async def test_n1_payload_helper_supports_standard_async_create_pattern(self):
        from copy import deepcopy

        from openai.types.chat import ChatCompletion, ChatCompletionMessage
        from openai.types.chat.chat_completion import Choice

        from yutori.n1 import trimmed_messages_to_fit

        mock_completion = ChatCompletion(
            id="chatcmpl-123",
            choices=[
                Choice(
                    finish_reason="stop",
                    index=0,
                    message=ChatCompletionMessage(role="assistant", content="click"),
                )
            ],
            created=1234567890,
            model="n1-latest",
            object="chat.completion",
        )
        original_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Check the page"},
                    {"type": "image_url", "image_url": {"url": "A" * 5000}},
                ],
            },
            {
                "role": "tool",
                "content": [
                    {"type": "text", "text": "Tool output"},
                    {"type": "image_url", "image_url": {"url": "A" * 5000}},
                ],
            },
        ]
        original_snapshot = deepcopy(original_messages)
        trimmed_messages, _, _ = trimmed_messages_to_fit(original_messages, max_bytes=100, keep_recent=1)

        with patch("yutori._async.chat.AsyncOpenAI") as MockAsyncOpenAI:
            mock_openai_client = MagicMock()
            mock_openai_client.chat.completions.create = AsyncMock(return_value=mock_completion)
            mock_openai_client.close = AsyncMock()
            MockAsyncOpenAI.return_value = mock_openai_client

            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.chat.completions.create(
                    model="n1-latest",
                    messages=trimmed_messages,
                )
                assert result.choices[0].message.content == "click"

        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        assert call_kwargs["messages"] == trimmed_messages
        assert original_messages == original_snapshot


@pytest.mark.asyncio
class TestAsyncErrorHandling:
    async def test_auth_error(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-invalid") as client:
                with pytest.raises(AuthenticationError):
                    await client.get_usage()

    async def test_auth_error_forbidden(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-invalid") as client:
                with pytest.raises(AuthenticationError):
                    await client.get_usage()

    async def test_api_error(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500
        mock_response.text = "Server error"

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                with pytest.raises(APIError) as exc_info:
                    await client.get_usage()
                assert exc_info.value.status_code == 500
