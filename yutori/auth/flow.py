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
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .constants import (
    AUTH_SIGN_IN_URL,
    AUTH_TIMEOUT_SECONDS,
    CALLBACK_HOST,
    CLERK_CLIENT_ID,
    CLERK_CONSENT_URL,
    CLERK_INSTANCE_URL,
    DEFAULT_AUTH_SIGN_IN_URL,
    DEFAULT_CLERK_CONSENT_URL,
    DEFAULT_CLERK_INSTANCE_URL,
    ERROR_AUTH_FAILED,
    ERROR_AUTH_TIMEOUT,
    ERROR_STATE_MISMATCH,
    REDIRECT_PORT,
    REDIRECT_URI,
    build_auth_api_url,
)
from .credentials import _is_real_key, get_config_path, load_config, save_config
from .types import AuthStatus, LoginResult

logger = logging.getLogger(__name__)

REGISTER_INCOMPATIBLE_STATUS_CODES = {404, 405, 501}
REGISTER_INCOMPATIBLE_ERROR = (
    "Yutori servers are out of sync with this CLI version. Please retry shortly, "
    "or contact support@yutori.ai if this persists."
)


class RegisterEndpointUnavailableError(RuntimeError):
    """Raised when the backend does not yet support the registration endpoint."""


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code verifier and S256 challenge."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def build_auth_url(code_challenge: str, state: str) -> str:
    """Build the browser entrypoint for the Clerk OAuth consent flow."""
    params = {
        "response_type": "code",
        "client_id": CLERK_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": "openid profile email",
    }
    if AUTH_SIGN_IN_URL or CLERK_CONSENT_URL:
        sign_in_url = AUTH_SIGN_IN_URL or DEFAULT_AUTH_SIGN_IN_URL
        consent_base_url = CLERK_CONSENT_URL or DEFAULT_CLERK_CONSENT_URL
        consent_url = f"{consent_base_url}?{urlencode(params)}"
        return f"{sign_in_url}?{urlencode({'redirect_url': consent_url})}"

    if CLERK_INSTANCE_URL != DEFAULT_CLERK_INSTANCE_URL:
        return f"{CLERK_INSTANCE_URL}/oauth/authorize?{urlencode(params)}"

    consent_url = f"{DEFAULT_CLERK_CONSENT_URL}?{urlencode(params)}"
    return f"{DEFAULT_AUTH_SIGN_IN_URL}?{urlencode({'redirect_url': consent_url})}"


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


def check_registration_status(jwt: str) -> bool | None:
    """Best-effort registration check.

    Returns:
        ``True`` when the backend confirms the user is already registered,
        ``False`` when it confirms the user is not registered, and ``None``
        when the status probe fails.

    Callers must distinguish ``None`` from ``False``. Treating unknown as
    "new user" would incorrectly trigger registration for existing users when
    the probe endpoint is transiently unreachable.
    """
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                build_auth_api_url("/client/registration-status"),
                headers={"Authorization": f"Bearer {jwt}"},
            )
            if response.status_code == 200:
                body = response.json()
                # Defensive: only trust the response if it's the expected
                # `{"is_registered": bool}` shape. A list, string, or missing
                # key is a backend contract mismatch — treat as unknown and
                # fall through to returning None.
                if isinstance(body, dict) and "is_registered" in body:
                    return bool(body["is_registered"])
                logger.warning("Registration status probe returned unexpected body shape: %r", body)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Registration status probe failed: %s", exc)
    return None


def register_user(jwt: str, signup_source: str = "cli") -> None:
    """Ensure the current Clerk user is registered before key generation."""
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            build_auth_api_url("/client/register-api"),
            headers={"Authorization": f"Bearer {jwt}"},
            json={"signup_source": signup_source},
        )
        if response.status_code in REGISTER_INCOMPATIBLE_STATUS_CODES:
            raise RegisterEndpointUnavailableError(f"{REGISTER_INCOMPATIBLE_ERROR} (received {response.status_code})")
        response.raise_for_status()


def run_login_flow(
    key_source: str = "yutori-cli",
    on_registration_state: Callable[[str], None] | None = None,
) -> LoginResult:
    """Run the full OAuth 2.0 + PKCE login flow.

    Opens browser for Clerk authentication, runs a local callback server,
    exchanges the auth code for a JWT, generates an API key, and saves it.
    Key names are tagged with `key_source` for dashboard visibility.

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

    # webbrowser.open returns False when no browser could be launched (headless
    # remote shell, locked-down container). Print the URL to stderr so the user
    # can copy it manually instead of waiting out AUTH_TIMEOUT_SECONDS in silence.
    if not webbrowser.open(auth_url):
        print(
            f"Could not launch a browser. Open this URL manually to finish login:\n  {auth_url}",
            file=sys.stderr,
        )

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
        registration_status = check_registration_status(jwt)
        is_new_user = registration_status is False
        if on_registration_state:
            try:
                on_registration_state("creating_account" if is_new_user else "logging_in")
            except Exception:
                logger.warning("Registration-state callback failed", exc_info=True)
        try:
            register_user(jwt)
        except RegisterEndpointUnavailableError:
            if registration_status is not True:
                raise
            logger.warning("Registration endpoint unavailable; continuing for existing user", exc_info=True)
        api_key = generate_api_key(jwt, key_name=_build_key_name(key_source))
        save_config(api_key)
        return LoginResult(success=True, api_key=api_key, auth_url=auth_url)
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
    if _is_real_key(env_key):
        return AuthStatus(
            authenticated=True,
            masked_key=_mask_key(env_key),
            source="env_var",
            config_path=config_path,
        )

    config = load_config()
    config_key = config.get("api_key") if config else None
    if isinstance(config_key, str) and _is_real_key(config_key):
        return AuthStatus(
            authenticated=True,
            masked_key=_mask_key(config_key),
            source="config_file",
            config_path=config_path,
        )

    return AuthStatus(authenticated=False, config_path=config_path)
