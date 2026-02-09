"""Tests for the yutori.auth module."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from yutori.auth.constants import (
    CALLBACK_HOST,
    CLERK_CLIENT_ID,
    CLERK_INSTANCE_URL,
    REDIRECT_PORT,
    REDIRECT_URI,
    build_auth_api_url,
)
from yutori.auth.credentials import clear_config, load_config, resolve_api_key, save_config
from yutori.auth.flow import (
    _CallbackHandler,
    _CallbackResult,
    _mask_key,
    build_auth_url,
    exchange_code_for_token,
    generate_api_key,
    generate_pkce,
    get_auth_status,
    run_login_flow,
)
from yutori.auth.types import AuthStatus, LoginResult


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

class TestPKCE:
    def test_generate_pkce_returns_pair(self):
        verifier, challenge = generate_pkce()
        assert isinstance(verifier, str)
        assert isinstance(challenge, str)
        assert len(verifier) > 40

    def test_generate_pkce_produces_valid_s256_challenge(self):
        verifier, challenge = generate_pkce()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        assert challenge == expected

    def test_generate_pkce_unique_each_call(self):
        v1, c1 = generate_pkce()
        v2, c2 = generate_pkce()
        assert v1 != v2
        assert c1 != c2


# ---------------------------------------------------------------------------
# build_auth_url
# ---------------------------------------------------------------------------

class TestBuildAuthUrl:
    def test_contains_required_params(self):
        url = build_auth_url("test_challenge", "test_state")
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        assert parsed.scheme == "https"
        assert "oauth/authorize" in parsed.path
        assert params["response_type"] == ["code"]
        assert params["client_id"] == [CLERK_CLIENT_ID]
        assert params["redirect_uri"] == [REDIRECT_URI]
        assert params["code_challenge"] == ["test_challenge"]
        assert params["code_challenge_method"] == ["S256"]
        assert params["state"] == ["test_state"]
        assert params["scope"] == ["openid profile email"]


# ---------------------------------------------------------------------------
# Credentials: save / load / clear / resolve
# ---------------------------------------------------------------------------

class TestCredentials:
    @pytest.fixture(autouse=True)
    def _use_tmp_config(self, tmp_path, monkeypatch):
        """Redirect config path to a temp directory for all credential tests."""
        config_path = tmp_path / ".yutori" / "config.json"
        monkeypatch.setattr("yutori.auth.credentials.get_config_path", lambda: config_path)
        # Also patch the import in flow.py which imports _get_config_path
        monkeypatch.setattr("yutori.auth.flow.get_config_path", lambda: config_path)
        self.config_path = config_path
        self.config_dir = config_path.parent

    def test_save_and_load_round_trip(self):
        save_config("yt-test-key-12345")
        result = load_config()
        assert result is not None
        assert result["api_key"] == "yt-test-key-12345"

    def test_save_creates_directory(self):
        assert not self.config_dir.exists()
        save_config("yt-key")
        assert self.config_dir.exists()

    def test_save_sets_file_permissions_0600(self):
        save_config("yt-key")
        file_mode = self.config_path.stat().st_mode & 0o777
        assert file_mode == 0o600

    def test_save_sets_directory_permissions_0700(self):
        save_config("yt-key")
        dir_mode = self.config_dir.stat().st_mode & 0o777
        assert dir_mode == 0o700

    def test_save_overwrites_existing(self):
        save_config("yt-old-key")
        save_config("yt-new-key")
        result = load_config()
        assert result["api_key"] == "yt-new-key"

    def test_save_atomic_no_temp_files_left(self):
        save_config("yt-key")
        files = list(self.config_dir.iterdir())
        assert len(files) == 1
        assert files[0].name == "config.json"

    def test_load_returns_none_for_missing_file(self):
        assert load_config() is None

    def test_load_returns_none_for_corrupt_json(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text("not valid json{{{")
        assert load_config() is None

    def test_load_returns_none_for_non_dict(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(["not", "a", "dict"]))
        assert load_config() is None

    def test_clear_removes_file(self):
        save_config("yt-key")
        assert self.config_path.exists()
        clear_config()
        assert not self.config_path.exists()

    def test_clear_no_error_when_missing(self):
        clear_config()  # should not raise


class TestResolveApiKey:
    @pytest.fixture(autouse=True)
    def _use_tmp_config(self, tmp_path, monkeypatch):
        config_path = tmp_path / ".yutori" / "config.json"
        monkeypatch.setattr("yutori.auth.credentials.get_config_path", lambda: config_path)
        self.config_path = config_path
        monkeypatch.delenv("YUTORI_API_KEY", raising=False)

    def test_explicit_param_wins(self, monkeypatch):
        monkeypatch.setenv("YUTORI_API_KEY", "yt-env")
        assert resolve_api_key("yt-explicit") == "yt-explicit"

    def test_env_var_used_when_no_param(self, monkeypatch):
        monkeypatch.setenv("YUTORI_API_KEY", "yt-env")
        assert resolve_api_key() == "yt-env"
        assert resolve_api_key(None) == "yt-env"

    def test_config_file_used_when_no_env(self):
        save_config("yt-stored")
        assert resolve_api_key() == "yt-stored"

    def test_returns_none_when_nothing_set(self):
        assert resolve_api_key() is None
        assert resolve_api_key(None) is None

    def test_empty_string_param_falls_through(self, monkeypatch):
        monkeypatch.setenv("YUTORI_API_KEY", "yt-env")
        assert resolve_api_key("") == "yt-env"


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

class TestCallbackHandler:
    """Tests for the OAuth callback HTTP handler."""

    def _make_handler(self, path: str, callback_result: _CallbackResult) -> _CallbackHandler:
        """Create a handler with a fake request."""
        _CallbackHandler.callback_result = callback_result

        handler = _CallbackHandler.__new__(_CallbackHandler)
        handler.path = path
        handler.requestline = f"GET {path} HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.headers = {}
        handler.wfile = io.BytesIO()
        handler.client_address = ("127.0.0.1", 12345)

        # Mock response methods
        handler._headers_buffer = []
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.send_error = MagicMock()

        return handler

    def test_success_path(self):
        result = _CallbackResult()
        handler = self._make_handler("/callback?code=test_code&state=test_state", result)
        handler.do_GET()

        assert result.code == "test_code"
        assert result.state == "test_state"
        assert result.error is None
        assert result.received.is_set()

    def test_error_path(self):
        result = _CallbackResult()
        handler = self._make_handler("/callback?error=access_denied&error_description=User+denied", result)
        handler.do_GET()

        assert result.error == "User denied"
        assert result.code is None
        assert result.received.is_set()

    def test_missing_code(self):
        result = _CallbackResult()
        handler = self._make_handler("/callback?state=test_state", result)
        handler.do_GET()

        assert result.error == "No authorization code received"
        assert result.code is None
        assert result.received.is_set()

    def test_favicon_ignored(self):
        result = _CallbackResult()
        handler = self._make_handler("/favicon.ico", result)
        handler.do_GET()

        assert not result.received.is_set()
        handler.send_response.assert_called_with(204)

    def test_unknown_path_returns_404(self):
        result = _CallbackResult()
        handler = self._make_handler("/unknown", result)
        handler.do_GET()

        assert not result.received.is_set()
        handler.send_error.assert_called_with(404)


# ---------------------------------------------------------------------------
# Token exchange and API key generation
# ---------------------------------------------------------------------------

class TestTokenExchange:
    def test_exchange_code_for_token_success(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = {"access_token": "jwt_token_123"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
            token = exchange_code_for_token("auth_code", "verifier")
            assert token == "jwt_token_123"
            call_kwargs = mock_post.call_args
            assert "oauth/token" in call_kwargs[0][0]
            assert call_kwargs[1]["data"]["code"] == "auth_code"
            assert call_kwargs[1]["data"]["code_verifier"] == "verifier"

    def test_exchange_code_raises_on_error(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock(status_code=401)
        )

        with patch.object(httpx.Client, "post", return_value=mock_response):
            with pytest.raises(httpx.HTTPStatusError):
                exchange_code_for_token("bad_code", "verifier")


class TestGenerateApiKey:
    def test_generate_api_key_success(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = {"key": "yt-generated-key"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
            key = generate_api_key("jwt_token")
            assert key == "yt-generated-key"
            call_kwargs = mock_post.call_args
            assert "Bearer jwt_token" in call_kwargs[1]["headers"]["Authorization"]

    def test_generate_api_key_uses_build_auth_api_url(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = {"key": "yt-key"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
            generate_api_key("jwt")
            url = mock_post.call_args[0][0]
            assert url == build_auth_api_url("/client/generate_key")


# ---------------------------------------------------------------------------
# run_login_flow (mocked — no real browser or server)
# ---------------------------------------------------------------------------

class TestRunLoginFlow:
    @patch("yutori.auth.flow.webbrowser.open")
    @patch("yutori.auth.flow.generate_api_key", return_value="yt-new-key")
    @patch("yutori.auth.flow.exchange_code_for_token", return_value="jwt123")
    @patch("yutori.auth.flow.save_config")
    def test_successful_flow(self, mock_save, mock_exchange, mock_gen_key, mock_browser):
        """Mock the callback result to simulate a successful flow."""

        def fake_server_init(self, addr, handler):
            pass

        def fake_serve_forever(self):
            # Simulate the callback happening
            _CallbackHandler.callback_result.code = "auth_code"
            _CallbackHandler.callback_result.state = None  # Will be set by run_login_flow
            _CallbackHandler.callback_result.received.set()

        # We need a more targeted approach: patch the server and thread
        with patch("yutori.auth.flow.socketserver.TCPServer.__init__", fake_server_init), \
             patch("yutori.auth.flow.socketserver.TCPServer.serve_forever", fake_serve_forever), \
             patch("yutori.auth.flow.socketserver.TCPServer.shutdown"), \
             patch("yutori.auth.flow.socketserver.TCPServer.server_close"), \
             patch("yutori.auth.flow.threading.Thread") as mock_thread_cls:

            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread

            # Patch the _CallbackResult so we can control the state
            original_result = _CallbackResult()
            with patch("yutori.auth.flow._CallbackResult", return_value=original_result):
                # We need to make the state match — intercept secrets.token_urlsafe
                with patch("yutori.auth.flow.secrets.token_urlsafe", return_value="fixed_state"):
                    # Simulate callback setting the right state before received.wait returns
                    def side_effect(*args, **kwargs):
                        original_result.code = "auth_code"
                        original_result.state = "fixed_state"
                        original_result.received.set()

                    mock_thread.start.side_effect = side_effect

                    result = run_login_flow()
                    assert result.success is True
                    assert result.api_key == "yt-new-key"
                    assert result.auth_url is not None
                    mock_save.assert_called_once_with("yt-new-key")

    def test_port_in_use(self):
        with patch("yutori.auth.flow.socketserver.TCPServer.__init__", side_effect=OSError("Address already in use")):
            result = run_login_flow()
            assert result.success is False
            assert "already in use" in result.error

    def test_timeout(self):
        def fake_server_init(self, addr, handler):
            pass

        with patch("yutori.auth.flow.socketserver.TCPServer.__init__", fake_server_init), \
             patch("yutori.auth.flow.socketserver.TCPServer.serve_forever"), \
             patch("yutori.auth.flow.socketserver.TCPServer.shutdown"), \
             patch("yutori.auth.flow.socketserver.TCPServer.server_close"), \
             patch("yutori.auth.flow.webbrowser.open"), \
             patch("yutori.auth.flow.threading.Thread") as mock_thread_cls:

            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread

            original_result = _CallbackResult()
            with patch("yutori.auth.flow._CallbackResult", return_value=original_result):
                # Don't set received, so wait() returns False after timeout=0
                with patch.object(original_result.received, "wait", return_value=False):
                    result = run_login_flow()
                    assert result.success is False
                    assert "timed out" in result.error.lower()
                    assert result.auth_url is not None

    def test_state_mismatch(self):
        def fake_server_init(self, addr, handler):
            pass

        with patch("yutori.auth.flow.socketserver.TCPServer.__init__", fake_server_init), \
             patch("yutori.auth.flow.socketserver.TCPServer.serve_forever"), \
             patch("yutori.auth.flow.socketserver.TCPServer.shutdown"), \
             patch("yutori.auth.flow.socketserver.TCPServer.server_close"), \
             patch("yutori.auth.flow.webbrowser.open"), \
             patch("yutori.auth.flow.threading.Thread") as mock_thread_cls:

            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread

            original_result = _CallbackResult()
            with patch("yutori.auth.flow._CallbackResult", return_value=original_result):
                with patch("yutori.auth.flow.secrets.token_urlsafe", return_value="expected_state"):
                    def side_effect(*args, **kwargs):
                        original_result.code = "auth_code"
                        original_result.state = "wrong_state"
                        original_result.received.set()

                    mock_thread.start.side_effect = side_effect

                    result = run_login_flow()
                    assert result.success is False
                    assert "state mismatch" in result.error.lower()
                    assert result.auth_url is not None


# ---------------------------------------------------------------------------
# get_auth_status
# ---------------------------------------------------------------------------

class TestGetAuthStatus:
    @pytest.fixture(autouse=True)
    def _use_tmp_config(self, tmp_path, monkeypatch):
        config_path = tmp_path / ".yutori" / "config.json"
        monkeypatch.setattr("yutori.auth.credentials.get_config_path", lambda: config_path)
        monkeypatch.setattr("yutori.auth.flow.get_config_path", lambda: config_path)
        monkeypatch.delenv("YUTORI_API_KEY", raising=False)
        self.config_path = config_path

    def test_authenticated_from_config_file(self):
        save_config("yt-test-key-12345")
        status = get_auth_status()
        assert status.authenticated is True
        assert status.source == "config_file"
        assert "yt-tes" in status.masked_key
        assert "2345" in status.masked_key

    def test_authenticated_from_env_var(self, monkeypatch):
        monkeypatch.setenv("YUTORI_API_KEY", "yt-env-key-67890")
        status = get_auth_status()
        assert status.authenticated is True
        assert status.source == "env_var"
        assert "yt-env" in status.masked_key

    def test_not_authenticated(self):
        status = get_auth_status()
        assert status.authenticated is False
        assert status.masked_key is None

    def test_env_var_takes_precedence_over_config_file(self, monkeypatch):
        save_config("yt-config-key-abc")
        monkeypatch.setenv("YUTORI_API_KEY", "yt-env-key-xyz")
        status = get_auth_status()
        assert status.source == "env_var"

    def test_non_string_api_key_in_config_treated_as_unauthenticated(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps({"api_key": 12345}))
        status = get_auth_status()
        assert status.authenticated is False


# ---------------------------------------------------------------------------
# _mask_key
# ---------------------------------------------------------------------------

class TestMaskKey:
    def test_long_key_masked(self):
        result = _mask_key("yt-abcdefghijk")
        assert result == "yt-abc...hijk"

    def test_short_key_fully_masked(self):
        result = _mask_key("short")
        assert result == "***"

    def test_exactly_11_chars(self):
        result = _mask_key("12345678901")
        assert result == "123456...8901"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_callback_host_is_ipv4(self):
        assert CALLBACK_HOST == "127.0.0.1"

    def test_redirect_uri_uses_localhost(self):
        assert "localhost" in REDIRECT_URI
        assert str(REDIRECT_PORT) in REDIRECT_URI
        assert "/callback" in REDIRECT_URI

    def test_build_auth_api_url(self):
        url = build_auth_api_url("/client/generate_key")
        assert url.endswith("/v1/client/generate_key")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class TestTypes:
    def test_login_result_success(self):
        r = LoginResult(success=True, api_key="yt-key")
        assert r.success is True
        assert r.api_key == "yt-key"
        assert r.error is None

    def test_login_result_failure(self):
        r = LoginResult(success=False, error="something broke")
        assert r.success is False
        assert r.api_key is None
        assert r.error == "something broke"

    def test_login_result_auth_url_default_none(self):
        r = LoginResult(success=True, api_key="yt-key")
        assert r.auth_url is None

    def test_login_result_auth_url_preserved(self):
        r = LoginResult(success=False, error="timeout", auth_url="https://clerk.yutori.com/oauth/authorize?x=1")
        assert r.auth_url == "https://clerk.yutori.com/oauth/authorize?x=1"

    def test_auth_status_authenticated(self):
        s = AuthStatus(authenticated=True, masked_key="yt-...abc", source="config_file", config_path="/home/.yutori/config.json")
        assert s.authenticated is True
        assert s.source == "config_file"

    def test_auth_status_not_authenticated(self):
        s = AuthStatus(authenticated=False)
        assert s.authenticated is False
        assert s.masked_key is None
        assert s.source is None
