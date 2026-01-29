"""Tests for the sync YutoriClient."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from yutori import APIError, AuthenticationError, YutoriClient


class TestYutoriClientInit:
    def test_init_with_api_key(self):
        client = YutoriClient(api_key="yt-test-key")
        assert client._api_key == "yt-test-key"
        assert client._base_url == "https://api.yutori.com/v1"
        client.close()

    def test_init_without_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("YUTORI_API_KEY", raising=False)
        with pytest.raises(AuthenticationError):
            YutoriClient(api_key="")

    def test_init_with_none_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("YUTORI_API_KEY", raising=False)
        with pytest.raises(AuthenticationError):
            YutoriClient(api_key=None)

    def test_init_from_env_var(self, monkeypatch):
        monkeypatch.setenv("YUTORI_API_KEY", "yt-env-key")
        client = YutoriClient()
        assert client._api_key == "yt-env-key"
        client.close()

    def test_init_with_custom_base_url(self):
        client = YutoriClient(api_key="yt-test", base_url="https://custom.api.com/v1/")
        assert client._base_url == "https://custom.api.com/v1"
        client.close()

    def test_context_manager(self):
        with YutoriClient(api_key="yt-test") as client:
            assert client._api_key == "yt-test"


class TestYutoriClientGetUsage:
    def test_get_usage_success(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"api_key_id": "key123", "user_id": "user456"}'
        mock_response.json.return_value = {"api_key_id": "key123", "user_id": "user456"}

        with patch.object(httpx.Client, "get", return_value=mock_response):
            client = YutoriClient(api_key="yt-test")
            result = client.get_usage()
            assert result == {"api_key_id": "key123", "user_id": "user456"}
            client.close()

    def test_get_usage_auth_error(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch.object(httpx.Client, "get", return_value=mock_response):
            client = YutoriClient(api_key="yt-invalid")
            with pytest.raises(AuthenticationError):
                client.get_usage()
            client.close()


class TestScoutsNamespace:
    def test_scouts_list(self, client):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"scouts": []}'
        mock_response.json.return_value = {"scouts": []}

        with patch.object(httpx.Client, "get", return_value=mock_response) as mock_get:
            result = client.scouts.list(limit=10, status="active")
            assert result == {"scouts": []}
            mock_get.assert_called_once()
            call_kwargs = mock_get.call_args
            assert "limit" in str(call_kwargs) or call_kwargs[1].get("params", {}).get("limit") == 10

    def test_scouts_get(self, client):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"id": "scout-123", "query": "test"}'
        mock_response.json.return_value = {"id": "scout-123", "query": "test"}

        with patch.object(httpx.Client, "get", return_value=mock_response) as mock_get:
            result = client.scouts.get("scout-123")
            assert result["id"] == "scout-123"
            mock_get.assert_called_once()

    def test_scouts_create(self, client):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"id": "new-scout", "query": "Monitor site"}'
        mock_response.json.return_value = {"id": "new-scout", "query": "Monitor site"}

        with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
            result = client.scouts.create(
                query="Monitor site",
                output_interval=3600,
                webhook_url="https://webhook.test",
            )
            assert result["id"] == "new-scout"
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            payload = call_kwargs[1]["json"]
            assert payload["query"] == "Monitor site"
            assert payload["output_interval"] == 3600
            assert payload["webhook_url"] == "https://webhook.test"

    def test_scouts_update_status(self, client):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"id": "scout-123", "status": "paused"}'
        mock_response.json.return_value = {"id": "scout-123", "status": "paused"}

        with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
            result = client.scouts.update("scout-123", status="paused")
            assert result["status"] == "paused"
            mock_post.assert_called_once()
            assert "/pause" in mock_post.call_args[0][0]

    def test_scouts_update_fields(self, client):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"id": "scout-123", "query": "new query"}'
        mock_response.json.return_value = {"id": "scout-123", "query": "new query"}

        with patch.object(httpx.Client, "patch", return_value=mock_response) as mock_patch:
            result = client.scouts.update("scout-123", query="new query")
            assert result["query"] == "new query"
            mock_patch.assert_called_once()

    def test_scouts_update_status_and_fields_raises(self, client):
        with pytest.raises(ValueError, match="Cannot update status and other fields simultaneously"):
            client.scouts.update("scout-123", status="paused", query="new query")

    def test_scouts_delete(self, client):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b""

        with patch.object(httpx.Client, "delete", return_value=mock_response) as mock_delete:
            result = client.scouts.delete("scout-123")
            assert result == {}
            mock_delete.assert_called_once()

    def test_scouts_get_updates(self, client):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"updates": [], "cursor": null}'
        mock_response.json.return_value = {"updates": [], "cursor": None}

        with patch.object(httpx.Client, "get", return_value=mock_response):
            result = client.scouts.get_updates("scout-123", limit=5)
            assert "updates" in result


class TestBrowsingNamespace:
    def test_browsing_create(self, client):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"task_id": "task-123", "status": "queued"}'
        mock_response.json.return_value = {"task_id": "task-123", "status": "queued"}

        with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
            result = client.browsing.create(
                task="Click the login button",
                start_url="https://example.com",
                max_steps=10,
            )
            assert result["task_id"] == "task-123"
            payload = mock_post.call_args[1]["json"]
            assert payload["task"] == "Click the login button"
            assert payload["start_url"] == "https://example.com"
            assert payload["max_steps"] == 10

    def test_browsing_get(self, client):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"task_id": "task-123", "status": "succeeded"}'
        mock_response.json.return_value = {"task_id": "task-123", "status": "succeeded"}

        with patch.object(httpx.Client, "get", return_value=mock_response):
            result = client.browsing.get("task-123")
            assert result["status"] == "succeeded"


class TestResearchNamespace:
    def test_research_create(self, client):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"task_id": "research-123", "status": "queued"}'
        mock_response.json.return_value = {"task_id": "research-123", "status": "queued"}

        with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
            result = client.research.create(
                query="Find AI startup funding",
                user_timezone="America/Los_Angeles",
            )
            assert result["task_id"] == "research-123"
            payload = mock_post.call_args[1]["json"]
            assert payload["query"] == "Find AI startup funding"

    def test_research_get(self, client):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"task_id": "research-123", "status": "succeeded"}'
        mock_response.json.return_value = {"task_id": "research-123", "status": "succeeded"}

        with patch.object(httpx.Client, "get", return_value=mock_response):
            result = client.research.get("research-123")
            assert result["status"] == "succeeded"


class TestChatNamespace:
    def test_chat_completions(self, client):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.content = b'{"choices": [{"message": {"content": "click"}}]}'
        mock_response.json.return_value = {"choices": [{"message": {"content": "click"}}]}

        with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
            result = client.chat.completions(
                messages=[{"role": "user", "content": "Click login"}],
                model="n1-preview-2025-11",
            )
            assert "choices" in result
            payload = mock_post.call_args[1]["json"]
            assert payload["model"] == "n1-preview-2025-11"
            headers = mock_post.call_args[1]["headers"]
            assert "Authorization" in headers


class TestErrorHandling:
    def test_api_error_on_400(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.text = "Bad request"

        with patch.object(httpx.Client, "get", return_value=mock_response):
            client = YutoriClient(api_key="yt-test")
            with pytest.raises(APIError) as exc_info:
                client.get_usage()
            assert exc_info.value.status_code == 400
            client.close()

    def test_api_error_on_500(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500
        mock_response.text = "Internal server error"

        with patch.object(httpx.Client, "get", return_value=mock_response):
            client = YutoriClient(api_key="yt-test")
            with pytest.raises(APIError) as exc_info:
                client.get_usage()
            assert exc_info.value.status_code == 500
            client.close()
