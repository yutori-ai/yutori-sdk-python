"""Constants for Yutori authentication and configuration."""

from __future__ import annotations

import os

from yutori.config import DEFAULT_BASE_URL

# Clerk OAuth configuration
CLERK_INSTANCE_URL = os.environ.get("CLERK_INSTANCE_URL", "https://clerk.yutori.com")
CLERK_CLIENT_ID = os.environ.get("CLERK_CLIENT_ID", "TGiyfoPbG01Sakpe")

# Callback server — 127.0.0.1 for both bind and redirect URI (no IPv4/IPv6 mismatch)
CALLBACK_HOST = "127.0.0.1"
REDIRECT_PORT = 54320
REDIRECT_URI = f"http://{CALLBACK_HOST}:{REDIRECT_PORT}/callback"
AUTH_TIMEOUT_SECONDS = 300

# Credential storage
CONFIG_DIR = ".yutori"
CONFIG_FILE = "config.json"

# Error messages
ERROR_AUTH_TIMEOUT = "Login timed out. Please try again."
ERROR_STATE_MISMATCH = "Security validation failed (state mismatch). Please try again."
ERROR_AUTH_FAILED = "Authentication failed"


def build_auth_api_url(path: str) -> str:
    """Build API URL for auth endpoints using the SDK's canonical base URL."""
    return f"{DEFAULT_BASE_URL}{path}"
