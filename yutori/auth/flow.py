"""OAuth 2.0 + PKCE authentication flow for Yutori.

Implements the Clerk OAuth flow: browser auth -> callback -> code exchange -> API key generation.
All functions return typed results and never print directly (callers handle presentation).
"""

from __future__ import annotations

import base64
import hashlib
import html
import http.server
import logging
import os
import secrets
import socketserver
import threading
import webbrowser
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .constants import (
    AUTH_TIMEOUT_SECONDS,
    CALLBACK_HOST,
    CLERK_CLIENT_ID,
    CLERK_INSTANCE_URL,
    ERROR_AUTH_FAILED,
    ERROR_AUTH_TIMEOUT,
    ERROR_STATE_MISMATCH,
    REDIRECT_PORT,
    REDIRECT_URI,
    build_auth_api_url,
)
from .credentials import get_config_path, load_config, save_config
from .types import AuthStatus, LoginResult

logger = logging.getLogger(__name__)


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code verifier and S256 challenge."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def build_auth_url(code_challenge: str, state: str) -> str:
    """Build Clerk OAuth authorization URL."""
    params = {
        "response_type": "code",
        "client_id": CLERK_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": "openid profile email",
    }
    return f"{CLERK_INSTANCE_URL}/oauth/authorize?{urlencode(params)}"


class _CallbackResult:
    """Thread-safe container for OAuth callback data."""

    def __init__(self) -> None:
        self.code: str | None = None
        self.state: str | None = None
        self.error: str | None = None
        self.received = threading.Event()


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for the OAuth redirect callback."""

    callback_result: _CallbackResult

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress HTTP server logs

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path != "/callback":
            self.send_error(404)
            return

        params = parse_qs(parsed.query)

        try:
            if "error" in params:
                error_text = params.get("error_description", params["error"])[0]
                self.callback_result.error = error_text
                self._send_html(f"<h1>Login Failed</h1><p>{html.escape(error_text)}</p>")
            elif params.get("code"):
                self.callback_result.code = params["code"][0]
                self.callback_result.state = params.get("state", [None])[0]
                self._send_html(
                    "<h1>Login Successful</h1>"
                    "<p>Redirecting to your developer dashboard...</p>"
                    '<script>setTimeout(function(){window.location.href='
                    '"https://platform.yutori.com/settings"},5000)</script>'
                )
            else:
                self.callback_result.error = "No authorization code received"
                self._send_html("<h1>Login Failed</h1><p>No authorization code received.</p>")
        finally:
            self.callback_result.received.set()

    def _send_html(self, body: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(f"<html><body>{body}</body></html>".encode())


def exchange_code_for_token(code: str, code_verifier: str) -> str:
    """Exchange authorization code for JWT via Clerk token endpoint."""
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{CLERK_INSTANCE_URL}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLERK_CLIENT_ID,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": code_verifier,
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]


def generate_api_key(jwt: str) -> str:
    """Generate a Yutori API key using a Clerk JWT."""
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            build_auth_api_url("/client/generate_key"),
            headers={"Authorization": f"Bearer {jwt}"},
        )
        response.raise_for_status()
        return response.json()["key"]


def run_login_flow() -> LoginResult:
    """Run the full OAuth 2.0 + PKCE login flow.

    Opens browser for Clerk authentication, runs a local callback server,
    exchanges the auth code for a JWT, generates an API key, and saves it.

    Returns a LoginResult — never prints directly.
    """
    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(32)
    auth_url = build_auth_url(code_challenge, state)

    callback_result = _CallbackResult()
    _CallbackHandler.callback_result = callback_result

    class _ReusableServer(socketserver.TCPServer):
        allow_reuse_address = True

    try:
        server = _ReusableServer((CALLBACK_HOST, REDIRECT_PORT), _CallbackHandler)
    except OSError as e:
        if "Address already in use" in str(e):
            return LoginResult(
                success=False,
                error=f"Port {REDIRECT_PORT} is already in use. Close other applications and try again.",
            )
        return LoginResult(success=False, error=str(e))

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    if not webbrowser.open(auth_url):
        logger.warning("Could not open browser. Open this URL manually:\n  %s", auth_url)

    try:
        callback_result.received.wait(timeout=AUTH_TIMEOUT_SECONDS)
    finally:
        server.shutdown()
        server_thread.join(timeout=2)
        server.server_close()

    if not callback_result.received.is_set():
        return LoginResult(success=False, error=ERROR_AUTH_TIMEOUT, auth_url=auth_url)

    if callback_result.error:
        return LoginResult(success=False, error=callback_result.error, auth_url=auth_url)

    if callback_result.state != state:
        return LoginResult(success=False, error=ERROR_STATE_MISMATCH, auth_url=auth_url)

    if not callback_result.code:
        return LoginResult(success=False, error=ERROR_AUTH_FAILED, auth_url=auth_url)

    try:
        jwt = exchange_code_for_token(callback_result.code, code_verifier)
        api_key = generate_api_key(jwt)
        save_config(api_key)
        return LoginResult(success=True, api_key=api_key, auth_url=auth_url)
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = f": {e.response.text}"
        except Exception:
            pass
        return LoginResult(success=False, error=f"{ERROR_AUTH_FAILED} ({e.response.status_code}){detail}", auth_url=auth_url)
    except Exception as e:
        return LoginResult(success=False, error=str(e), auth_url=auth_url)


def _mask_key(key: str) -> str:
    if len(key) >= 16:
        return key[:4] + "..." + key[-4:]
    if len(key) >= 8:
        return key[:4] + "..."
    return "***"


def get_auth_status() -> AuthStatus:
    """Check current authentication status.

    Precedence matches resolve_api_key(): env var > config file.
    Returns an AuthStatus — never prints directly.
    """
    config_path = str(get_config_path())

    env_key = os.environ.get("YUTORI_API_KEY")
    if env_key:
        return AuthStatus(
            authenticated=True,
            masked_key=_mask_key(env_key),
            source="env_var",
            config_path=config_path,
        )

    config = load_config()
    config_key = config.get("api_key") if config else None
    if config_key and isinstance(config_key, str):
        return AuthStatus(
            authenticated=True,
            masked_key=_mask_key(config_key),
            source="config_file",
            config_path=config_path,
        )

    return AuthStatus(authenticated=False, config_path=config_path)
