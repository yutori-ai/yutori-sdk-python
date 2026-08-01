"""Tests for the async AsyncYutoriClient."""

import httpx
import pytest

from yutori import APIError, AsyncYutoriClient, AuthenticationError
from yutori.navigator import NAVIGATOR_N1_5_MODEL, NAVIGATOR_N1_MODEL

from ._client_fixtures import (
    make_json_response,
    make_mock_chat_completion,
    make_mock_usage_response,
    make_status_response,
    make_trimmable_messages,
    mocked_async_openai_client,
    patch_async_http,
)


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
    async def test_get_usage_success(self, async_client):
        with patch_async_http("get", make_mock_usage_response()):
            result = await async_client.get_usage()
            assert result["num_active_scouts"] == 2
            assert result["activity"]["period"] == "24h"

    async def test_get_usage_with_period(self, async_client):
        with patch_async_http("get", make_mock_usage_response("30d")) as mock_get:
            result = await async_client.get_usage(period="30d")
            assert result["activity"]["period"] == "30d"
            call_kwargs = mock_get.call_args[1]
            assert call_kwargs["params"] == {"period": "30d"}


@pytest.mark.asyncio
class TestAsyncScoutsNamespace:
    async def test_scouts_list(self, async_client):
        mock_response = make_json_response({"scouts": []})

        with patch_async_http("get", mock_response) as mock_get:
            result = await async_client.scouts.list(limit=10, status="active")
            assert result == {"scouts": []}
            params = mock_get.call_args[1]["params"]
            assert params["page_size"] == 10
            assert "limit" not in params
            assert params["status"] == "active"

    async def test_scouts_list_forwards_cursor(self, async_client):
        mock_response = make_json_response({"scouts": []})

        with patch_async_http("get", mock_response) as mock_get:
            await async_client.scouts.list(cursor="next-page")
            params = mock_get.call_args[1]["params"]
            assert params["cursor"] == "next-page"
            assert "page_size" not in params

    async def test_scouts_get(self, async_client):
        mock_response = make_json_response({"id": "scout-123"})

        with patch_async_http("get", mock_response):
            result = await async_client.scouts.get("scout-123")
            assert result["id"] == "scout-123"

    async def test_scouts_create(self, async_client):
        mock_response = make_json_response({"id": "new-scout"})

        with patch_async_http("post", mock_response):
            result = await async_client.scouts.create(query="Monitor site")
            assert result["id"] == "new-scout"

    async def test_scouts_update_status(self, async_client):
        mock_response = make_json_response({"id": "scout-123", "status": "paused"})

        with patch_async_http("post", mock_response) as mock_post:
            result = await async_client.scouts.update("scout-123", status="paused")
            assert result["status"] == "paused"
            assert "/pause" in mock_post.call_args[0][0]

    async def test_scouts_update_fields(self, async_client):
        mock_response = make_json_response({"id": "scout-123", "query": "new query"})

        with patch_async_http("patch", mock_response):
            result = await async_client.scouts.update("scout-123", query="new query")
            assert result["query"] == "new query"

    async def test_scouts_update_is_public(self, async_client):
        mock_response = make_json_response({"id": "scout-123", "is_public": False})

        with patch_async_http("patch", mock_response) as mock_patch:
            await async_client.scouts.update("scout-123", is_public=False)
            payload = mock_patch.call_args[1]["json"]
            assert payload["is_public"] is False

    async def test_scouts_update_status_and_fields_raises(self, async_client):
        with pytest.raises(ValueError, match="Cannot update status and other fields simultaneously"):
            await async_client.scouts.update("scout-123", status="paused", query="new query")

    async def test_scouts_delete(self, async_client):
        mock_response = make_status_response(200)

        with patch_async_http("delete", mock_response):
            result = await async_client.scouts.delete("scout-123")
            assert result == {}

    async def test_scouts_get_updates(self, async_client):
        mock_response = make_json_response({"updates": []})

        with patch_async_http("get", mock_response):
            result = await async_client.scouts.get_updates("scout-123")
            assert "updates" in result


@pytest.mark.asyncio
class TestAsyncBrowsingNamespace:
    async def test_browsing_list(self, async_client):
        mock_response = make_json_response({"tasks": [], "total": 0})

        with patch_async_http("get", mock_response) as mock_get:
            result = await async_client.browsing.list(limit=20, status="succeeded", cursor="cur-1")
            assert result == {"tasks": [], "total": 0}
            assert mock_get.call_args[0][0].endswith("/browsing/tasks")
            params = mock_get.call_args[1]["params"]
            assert params["page_size"] == 20
            assert "limit" not in params
            assert params["status"] == "succeeded"
            assert params["cursor"] == "cur-1"

    async def test_browsing_create(self, async_client):
        mock_response = make_json_response({"task_id": "task-123"})

        with patch_async_http("post", mock_response):
            result = await async_client.browsing.create(
                task="Click login",
                start_url="https://example.com",
            )
            assert result["task_id"] == "task-123"

    async def test_browsing_create_with_local_browser_and_auth(self, async_client):
        mock_response = make_json_response({"task_id": "task-456"})

        with patch_async_http("post", mock_response) as mock_post:
            result = await async_client.browsing.create(
                task="Log in and export data",
                start_url="https://example.com/login",
                require_auth=True,
                browser="local",
                webhook_format="zapier",
            )
            assert result["task_id"] == "task-456"
            payload = mock_post.call_args[1]["json"]
            assert payload["require_auth"] is True
            assert payload["browser"] == "local"
            assert payload["webhook_format"] == "zapier"

    async def test_browsing_get(self, async_client):
        mock_response = make_json_response({"task_id": "task-123", "status": "succeeded"})

        with patch_async_http("get", mock_response):
            result = await async_client.browsing.get("task-123")
            assert result["status"] == "succeeded"

    async def test_browsing_get_with_rejection_reason(self, async_client):
        mock_response = make_json_response(
            {
                "task_id": "task-123",
                "status": "failed",
                "rejection_reason": "billing_limit_reached",
            }
        )

        with patch_async_http("get", mock_response):
            result = await async_client.browsing.get("task-123")
            assert result["status"] == "failed"
            assert result["rejection_reason"] == "billing_limit_reached"


@pytest.mark.asyncio
class TestAsyncResearchNamespace:
    async def test_research_list(self, async_client):
        mock_response = make_json_response({"tasks": [], "total": 0})

        with patch_async_http("get", mock_response) as mock_get:
            result = await async_client.research.list(limit=20, status="succeeded", cursor="cur-1")
            assert result == {"tasks": [], "total": 0}
            assert mock_get.call_args[0][0].endswith("/research/tasks")
            params = mock_get.call_args[1]["params"]
            assert params["page_size"] == 20
            assert "limit" not in params
            assert params["status"] == "succeeded"
            assert params["cursor"] == "cur-1"

    async def test_research_create(self, async_client):
        mock_response = make_json_response({"task_id": "research-123"})

        with patch_async_http("post", mock_response):
            result = await async_client.research.create(query="Find AI funding")
            assert result["task_id"] == "research-123"

    async def test_research_get(self, async_client):
        mock_response = make_json_response({"task_id": "research-123", "status": "succeeded"})

        with patch_async_http("get", mock_response):
            result = await async_client.research.get("research-123")
            assert result["status"] == "succeeded"

    async def test_research_get_with_rejection_reason(self, async_client):
        mock_response = make_json_response(
            {
                "task_id": "research-123",
                "status": "failed",
                "rejection_reason": "rate_limit_exceeded",
            }
        )

        with patch_async_http("get", mock_response):
            result = await async_client.research.get("research-123")
            assert result["status"] == "failed"
            assert result["rejection_reason"] == "rate_limit_exceeded"


@pytest.mark.asyncio
class TestAsyncPydanticSchemaIntegration:
    """Test that Pydantic models are resolved to JSON schema dicts in async payloads."""

    @staticmethod
    def _make_mock_response():
        return make_json_response({"task_id": "t-1"})

    class _FakeModel:
        @classmethod
        def model_json_schema(cls):
            return {"type": "object", "properties": {"name": {"type": "string"}}}

    async def test_browsing_create_with_model_class(self, async_client):
        with patch_async_http("post", self._make_mock_response()) as mock_post:
            await async_client.browsing.create(task="t", start_url="https://x.com", output_schema=self._FakeModel)
            payload = mock_post.call_args[1]["json"]
            assert payload["output_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}

    async def test_browsing_create_with_model_instance(self, async_client):
        with patch_async_http("post", self._make_mock_response()) as mock_post:
            await async_client.browsing.create(task="t", start_url="https://x.com", output_schema=self._FakeModel())
            payload = mock_post.call_args[1]["json"]
            assert payload["output_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}

    async def test_research_create_with_model_class(self, async_client):
        with patch_async_http("post", self._make_mock_response()) as mock_post:
            await async_client.research.create(query="q", output_schema=self._FakeModel)
            payload = mock_post.call_args[1]["json"]
            assert payload["output_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}

    async def test_scouts_create_with_model_class(self, async_client):
        mock = make_json_response({"id": "s-1"})
        with patch_async_http("post", mock) as mock_post:
            await async_client.scouts.create(query="q", output_schema=self._FakeModel)
            payload = mock_post.call_args[1]["json"]
            assert payload["output_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}

    async def test_scouts_update_with_model_class(self, async_client):
        mock = make_json_response({"id": "s-1"})
        with patch_async_http("patch", mock) as mock_patch:
            await async_client.scouts.update("s-1", output_schema=self._FakeModel)
            payload = mock_patch.call_args[1]["json"]
            assert payload["output_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}

    async def test_scouts_update_with_model_instance(self, async_client):
        mock = make_json_response({"id": "s-1"})
        with patch_async_http("patch", mock) as mock_patch:
            await async_client.scouts.update("s-1", output_schema=self._FakeModel())
            payload = mock_patch.call_args[1]["json"]
            assert payload["output_schema"] == {"type": "object", "properties": {"name": {"type": "string"}}}


@pytest.mark.asyncio
class TestAsyncChatNamespace:
    async def test_chat_completions_default_model_is_canonical_n1_5_constant(self, async_client):
        mock_completion = make_mock_chat_completion(content="click", model=NAVIGATOR_N1_5_MODEL)

        with mocked_async_openai_client(mock_completion) as mock_openai_client:
            await async_client.chat.completions.create(messages=[{"role": "user", "content": "Click login"}])
            call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
            assert call_kwargs["model"] == NAVIGATOR_N1_5_MODEL

    async def test_chat_completions(self, async_client):
        mock_completion = make_mock_chat_completion(content="click", model=NAVIGATOR_N1_MODEL)

        with mocked_async_openai_client(mock_completion):
            result = await async_client.chat.completions.create(
                messages=[{"role": "user", "content": "Click login"}],
            )
            assert result.choices[0].message.content == "click"

    async def test_chat_completions_n1_5_forwards_extra_body_options(self, async_client):
        from yutori.navigator import TOOL_SET_CORE

        json_schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
        mock_completion = make_mock_chat_completion(content='{"status":"ok"}', model=NAVIGATOR_N1_5_MODEL)

        with mocked_async_openai_client(mock_completion) as mock_openai_client:
            result = await async_client.chat.completions.create(
                messages=[{"role": "user", "content": "Reply with JSON."}],
                model=NAVIGATOR_N1_5_MODEL,
                tool_set=TOOL_SET_CORE,
                disable_tools=["hold_key"],
                json_schema=json_schema,
                extra_body={"trace_id": "trace-123"},
            )
            assert result.choices[0].message.content == '{"status":"ok"}'

        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == NAVIGATOR_N1_5_MODEL
        assert call_kwargs["extra_body"] == {
            "trace_id": "trace-123",
            "tool_set": TOOL_SET_CORE,
            "disable_tools": ["hold_key"],
            "json_schema": json_schema,
        }

    async def test_n1_helper_acreate_trimmed_public_helper_uses_trimmed_copy(self, async_client):
        from copy import deepcopy

        from yutori.navigator import acreate_trimmed
        from yutori.navigator.payload import trimmed_messages_to_fit

        mock_completion = make_mock_chat_completion(content="click", model=NAVIGATOR_N1_MODEL)
        original_messages = make_trimmable_messages()
        original_snapshot = deepcopy(original_messages)

        with mocked_async_openai_client(mock_completion) as mock_openai_client:
            result = await acreate_trimmed(
                async_client.chat.completions,
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

    async def test_n1_payload_helper_supports_standard_async_create_pattern(self, async_client):
        from copy import deepcopy

        from yutori.navigator import trimmed_messages_to_fit

        mock_completion = make_mock_chat_completion(content="click", model=NAVIGATOR_N1_MODEL)
        original_messages = make_trimmable_messages()
        original_snapshot = deepcopy(original_messages)
        trimmed_messages, _, _ = trimmed_messages_to_fit(original_messages, max_bytes=100, keep_recent=1)

        with mocked_async_openai_client(mock_completion) as mock_openai_client:
            result = await async_client.chat.completions.create(
                model=NAVIGATOR_N1_MODEL,
                messages=trimmed_messages,
            )
            assert result.choices[0].message.content == "click"

        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        assert call_kwargs["messages"] == trimmed_messages
        assert original_messages == original_snapshot


@pytest.mark.asyncio
class TestAsyncErrorHandling:
    @pytest.mark.parametrize(
        ("status_code", "reason"),
        [(401, "Unauthorized"), (403, "Forbidden")],
    )
    async def test_auth_error(self, status_code, reason):
        mock_response = make_status_response(status_code, reason)

        with patch_async_http("get", mock_response):
            async with AsyncYutoriClient(api_key="yt-invalid") as client:
                with pytest.raises(AuthenticationError):
                    await client.get_usage()

    @pytest.mark.parametrize(
        ("status_code", "reason"),
        [(400, "Bad request"), (500, "Internal server error")],
    )
    async def test_api_error(self, async_client, status_code, reason):
        mock_response = make_status_response(status_code, reason)

        with patch_async_http("get", mock_response):
            with pytest.raises(APIError) as exc_info:
                await async_client.get_usage()
            assert exc_info.value.status_code == status_code


class TestAsyncTransportErrorWrapping:
    async def test_connect_error_wrapped(self, async_client):
        from yutori.exceptions import APIConnectionError

        with patch_async_http("get", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(APIConnectionError, match="ConnectError.*refused"):
                await async_client.scouts.list()


class TestAsyncLazyChatNamespace:
    async def test_chat_not_built_at_init_and_close_safe(self):
        async with AsyncYutoriClient(api_key="yt-test") as client:
            assert client._chat is None
        assert client._chat is None
