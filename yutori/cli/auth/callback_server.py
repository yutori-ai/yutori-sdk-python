"""Local HTTP callback server for browser-based authentication."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from ..constants import CALLBACK_TIMEOUT_SECONDS, DEFAULT_CALLBACK_PORT


@dataclass
class AuthResult:
    """Result from the authentication callback."""

    success: bool
    api_key: str | None = None
    key_name: str | None = None
    error: str | None = None


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the auth callback."""

    result: AuthResult | None = None
    expected_state: str | None = None
    on_result: Callable[[AuthResult], None] | None = None

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path != "/callback":
            self._send_response(404, "Not Found")
            return

        params = parse_qs(parsed.query)
        state = params.get("state", [None])[0]
        key = params.get("key", [None])[0]
        name = params.get("name", [None])[0]
        error = params.get("error", [None])[0]

        if error:
            result = AuthResult(success=False, error=error)
            self._send_success_page("Authentication failed. You can close this window.")
            self._set_result(result)
            return

        if not state or state != CallbackHandler.expected_state:
            result = AuthResult(success=False, error="Invalid state parameter (possible CSRF)")
            self._send_success_page("Authentication failed: Invalid state. You can close this window.")
            self._set_result(result)
            return

        if not key:
            result = AuthResult(success=False, error="No API key received")
            self._send_success_page("Authentication failed: No key received. You can close this window.")
            self._set_result(result)
            return

        result = AuthResult(success=True, api_key=key, key_name=name)
        self._send_success_page("Authentication successful! You can close this window and return to your terminal.")
        self._set_result(result)

    def _send_response(self, code: int, message: str) -> None:
        self.send_response(code)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(message.encode())

    def _send_success_page(self, message: str) -> None:
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Yutori CLI Authentication</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background-color: #f8fafc;
        }}
        .container {{
            text-align: center;
            padding: 2rem;
            background: white;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            max-width: 400px;
        }}
        h1 {{ color: #1e293b; margin-bottom: 1rem; }}
        p {{ color: #64748b; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Yutori CLI</h1>
        <p>{message}</p>
    </div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def _set_result(self, result: AuthResult) -> None:
        CallbackHandler.result = result
        if CallbackHandler.on_result:
            CallbackHandler.on_result(result)


def generate_state() -> str:
    """Generate a random state string for CSRF protection."""
    return secrets.token_urlsafe(32)


def start_callback_server(
    state: str,
    port: int = DEFAULT_CALLBACK_PORT,
    timeout: int = CALLBACK_TIMEOUT_SECONDS,
) -> AuthResult:
    """Start the callback server and wait for authentication.

    Args:
        state: The state string for CSRF protection.
        port: The port to listen on.
        timeout: Maximum seconds to wait for callback.

    Returns:
        AuthResult with the authentication outcome.
    """
    CallbackHandler.result = None
    CallbackHandler.expected_state = state

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    server.timeout = 1

    result_event = threading.Event()

    def on_result(result: AuthResult) -> None:
        result_event.set()

    CallbackHandler.on_result = on_result

    start_time = time.time()
    while not result_event.is_set():
        if time.time() - start_time > timeout:
            server.server_close()
            return AuthResult(success=False, error=f"Timed out waiting for authentication ({timeout}s)")

        server.handle_request()

    server.server_close()
    return CallbackHandler.result or AuthResult(success=False, error="Unknown error")
