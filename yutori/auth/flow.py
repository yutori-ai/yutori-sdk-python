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
from datetime import datetime, timezone
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


class _BrowserAuthResult:
    """Result of the browser-based OAuth flow before API provisioning."""

    def __init__(self, jwt: str | None, auth_url: str, error: str | None = None) -> None:
        self.jwt = jwt
        self.auth_url = auth_url
        self.error = error


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code verifier and S256 challenge."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def build_auth_url(code_challenge: str, state: str, screen_hint: str | None = None) -> str:
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
    if screen_hint:
        params["screen_hint"] = screen_hint
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
                self._send_html("<h1>Login Successful</h1><p>You can close this window.</p>")
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


def _build_key_name(source: str = "yutori-cli") -> str:
    """Build a descriptive name for SDK-generated API keys."""
    date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{date_prefix}-{source}"


def generate_api_key(jwt: str, key_name: str | None = None) -> str:
    """Generate a Yutori API key using a Clerk JWT."""
    payload = {"name": key_name} if key_name else None
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            build_auth_api_url("/client/generate_key"),
            headers={"Authorization": f"Bearer {jwt}"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()["key"]


def check_registration_status(jwt: str) -> dict[str, bool]:
    """Check whether the authenticated Clerk user already has a Yutori account."""
    with httpx.Client(timeout=30.0) as client:
        response = client.get(
            build_auth_api_url("/client/registration-status"),
            headers={"Authorization": f"Bearer {jwt}"},
        )
        response.raise_for_status()
        return response.json()


def register_user(jwt: str, signup_source: str = "cli") -> None:
    """Ensure the authenticated Clerk user is registered and provisioned for API access."""
    payload = {"signup_source": signup_source}
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            build_auth_api_url("/client/register"),
            headers={"Authorization": f"Bearer {jwt}"},
            json=payload,
        )
        response.raise_for_status()


def _get_registration_state_for_ux(jwt: str) -> bool | None:
    """Best-effort registration probe for user messaging only."""
    try:
        status = check_registration_status(jwt)
        if not isinstance(status, dict):
            logger.warning("Unexpected registration status payload: %s", status)
            return None
    except Exception as exc:
        logger.warning("Could not determine registration status before login: %s", exc)
        return None

    is_registered = status.get("is_registered")
    if isinstance(is_registered, bool):
        return is_registered

    logger.warning("Unexpected registration status payload: %s", status)
    return None


def _authenticate_browser_user(screen_hint: str | None = None) -> _BrowserAuthResult:
    """Run the Clerk browser flow and exchange the callback for a JWT."""
    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(32)
    auth_url = build_auth_url(code_challenge, state, screen_hint=screen_hint)

    callback_result = _CallbackResult()
    _CallbackHandler.callback_result = callback_result

    class _ReusableServer(socketserver.TCPServer):
        allow_reuse_address = True

    try:
        server = _ReusableServer((CALLBACK_HOST, REDIRECT_PORT), _CallbackHandler)
    except OSError as e:
        if "Address already in use" in str(e):
            return _BrowserAuthResult(
                jwt=None,
                auth_url=auth_url,
                error=f"Port {REDIRECT_PORT} is already in use. Close other applications and try again.",
            )
        return _BrowserAuthResult(jwt=None, auth_url=auth_url, error=str(e))

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread_started = False
    try:
        server_thread.start()
        thread_started = True

        try:
            browser_opened = webbrowser.open(auth_url)
        except Exception as e:
            logger.warning("Could not open browser automatically (%s). Open this URL manually:\n  %s", e, auth_url)
        else:
            if not browser_opened:
                logger.warning("Could not open browser. Open this URL manually:\n  %s", auth_url)

        callback_result.received.wait(timeout=AUTH_TIMEOUT_SECONDS)
    finally:
        server.shutdown()
        if thread_started:
            server_thread.join(timeout=2)
        server.server_close()

    if not callback_result.received.is_set():
        return _BrowserAuthResult(jwt=None, auth_url=auth_url, error=ERROR_AUTH_TIMEOUT)

    if callback_result.error:
        return _BrowserAuthResult(jwt=None, auth_url=auth_url, error=callback_result.error)

    if callback_result.state != state:
        return _BrowserAuthResult(jwt=None, auth_url=auth_url, error=ERROR_STATE_MISMATCH)

    if not callback_result.code:
        return _BrowserAuthResult(jwt=None, auth_url=auth_url, error=ERROR_AUTH_FAILED)

    try:
        jwt = exchange_code_for_token(callback_result.code, code_verifier)
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = f": {e.response.text}"
        except Exception:
            pass
        return _BrowserAuthResult(
            jwt=None,
            auth_url=auth_url,
            error=f"{ERROR_AUTH_FAILED} ({e.response.status_code}){detail}",
        )
    except Exception as e:
        return _BrowserAuthResult(jwt=None, auth_url=auth_url, error=str(e))

    return _BrowserAuthResult(jwt=jwt, auth_url=auth_url)


def _complete_auth_flow(
    jwt: str,
    auth_url: str,
    *,
    key_source: str,
    signup_source: str = "cli",
) -> LoginResult:
    """Provision the Clerk identity and create a Yutori API key."""
    was_registered = _get_registration_state_for_ux(jwt)

    try:
        register_user(jwt, signup_source=signup_source)
    except httpx.HTTPStatusError as e:
        should_continue = was_registered is True and e.response.status_code >= 500
        if not should_continue:
            detail = ""
            try:
                detail = f": {e.response.text}"
            except Exception:
                pass
            return LoginResult(
                success=False,
                error=f"{ERROR_AUTH_FAILED} ({e.response.status_code}){detail}",
                auth_url=auth_url,
            )

        logger.warning(
            "Register request failed after confirming the account already exists; continuing with key generation: %s",
            e,
        )
    except httpx.RequestError as e:
        if was_registered is not True:
            return LoginResult(success=False, error=str(e), auth_url=auth_url)

        logger.warning(
            "Register request failed after confirming the account already exists; continuing with key generation: %s",
            e,
        )

    try:
        api_key = generate_api_key(jwt, key_name=_build_key_name(key_source))
        save_config(api_key)
        return LoginResult(
            success=True,
            api_key=api_key,
            auth_url=auth_url,
            account_created=None if was_registered is None else not was_registered,
        )
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = f": {e.response.text}"
        except Exception:
            pass
        return LoginResult(
            success=False,
            error=f"{ERROR_AUTH_FAILED} ({e.response.status_code}){detail}",
            auth_url=auth_url,
        )
    except Exception as e:
        return LoginResult(success=False, error=str(e), auth_url=auth_url)


def run_login_flow(key_source: str = "yutori-cli") -> LoginResult:
    """Run the full OAuth 2.0 + PKCE login flow.

    Opens browser for Clerk authentication, runs a local callback server,
    exchanges the auth code for a JWT, ensures the account is registered,
    generates an API key, and saves it.
    Key names are tagged with `key_source` for dashboard visibility.

    Returns a LoginResult — never prints directly.
    """
    browser_auth = _authenticate_browser_user()
    if browser_auth.error or not browser_auth.jwt:
        return LoginResult(success=False, error=browser_auth.error, auth_url=browser_auth.auth_url)

    return _complete_auth_flow(browser_auth.jwt, browser_auth.auth_url, key_source=key_source)


def run_register_flow(key_source: str = "yutori-cli") -> LoginResult:
    """Run the OAuth 2.0 + PKCE registration flow using Clerk's sign-up screen."""
    browser_auth = _authenticate_browser_user(screen_hint="sign_up")
    if browser_auth.error or not browser_auth.jwt:
        return LoginResult(success=False, error=browser_auth.error, auth_url=browser_auth.auth_url)

    return _complete_auth_flow(
        browser_auth.jwt,
        browser_auth.auth_url,
        key_source=key_source,
    )


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
