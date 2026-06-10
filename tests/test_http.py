"""Tests for shared HTTP helpers in yutori._http."""

from unittest.mock import MagicMock

import httpx
import pytest

from yutori._http import apply_chat_extra_body, handle_response, resolve_scout_status_endpoint
from yutori.exceptions import APIError, AuthenticationError


def make_response(status_code: int, text: str = "", content: bytes = b"") -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = text
    response.content = content
    return response


class TestHandleResponse:
    def test_redirect_raises_api_error_instead_of_silent_success(self):
        # Redirects are never followed, so a 3xx means a misconfigured base
        # URL; returning {} here would look like a successful empty response.
        with pytest.raises(APIError) as exc_info:
            handle_response(make_response(307))
        assert exc_info.value.status_code == 307

    def test_auth_error_includes_status_and_server_detail(self):
        with pytest.raises(AuthenticationError, match=r"403.*scout belongs to another user"):
            handle_response(make_response(403, text="scout belongs to another user"))

    def test_auth_error_without_body_still_readable(self):
        with pytest.raises(AuthenticationError, match=r"401"):
            handle_response(make_response(401))


class TestApplyChatExtraBody:
    def test_does_not_mutate_caller_extra_body(self):
        user_extra = {"trace_id": "trace-123"}
        kwargs = {"extra_body": user_extra}
        apply_chat_extra_body(kwargs, tool_set="browser_tools_core", disable_tools=None, json_schema=None)
        assert user_extra == {"trace_id": "trace-123"}
        assert kwargs["extra_body"] == {"trace_id": "trace-123", "tool_set": "browser_tools_core"}


class TestResolveScoutStatusEndpoint:
    def test_paused_maps_to_pause(self):
        assert resolve_scout_status_endpoint("paused") == "pause"

    def test_active_maps_to_resume(self):
        assert resolve_scout_status_endpoint("active") == "resume"

    def test_done_maps_to_done(self):
        assert resolve_scout_status_endpoint("done") == "done"

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="Invalid status"):
            resolve_scout_status_endpoint("deleted")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Invalid status"):
            resolve_scout_status_endpoint("")
