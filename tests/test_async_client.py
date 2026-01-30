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
        with pytest.raises(AuthenticationError):
            AsyncYutoriClient(api_key="")

    def test_init_with_none_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("YUTORI_API_KEY", raising=False)
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
    async def test_get_usage_success(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"api_key_id": "key123"}'
        mock_response.json.return_value = {"api_key_id": "key123"}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.get_usage()
                assert result == {"api_key_id": "key123"}


@pytest.mark.asyncio
class TestAsyncScoutsNamespace:
    async def test_scouts_list(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"scouts": []}'
        mock_response.json.return_value = {"scouts": []}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                result = await client.scouts.list()
                assert result == {"scouts": []}

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

    async def test_api_error(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500
        mock_response.text = "Server error"

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            async with AsyncYutoriClient(api_key="yt-test") as client:
                with pytest.raises(APIError) as exc_info:
                    await client.get_usage()
                assert exc_info.value.status_code == 500
