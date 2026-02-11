"""Constants for Yutori authentication and configuration."""

from __future__ import annotations

import os

from yutori.config import DEFAULT_BASE_URL

# Clerk OAuth configuration
CLERK_INSTANCE_URL = os.environ.get("CLERK_INSTANCE_URL", "https://clerk.yutori.com")
CLERK_CLIENT_ID = os.environ.get("CLERK_CLIENT_ID", "TGiyfoPbG01Sakpe")

# Callback server — bind to 127.0.0.1 (avoids IPv4/IPv6 mismatch),
# but use localhost in redirect URI (must match Clerk's registered URL).
CALLBACK_HOST = "127.0.0.1"
REDIRECT_PORT = 54320
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
AUTH_TIMEOUT_SECONDS = 300

# Credential storage
CONFIG_DIR = ".yutori"
CONFIG_FILE = "config.json"

# API endpoints
GENERATE_KEY_ENDPOINT = "/client/generate_key"
SIGN_UP_VALIDATE_ENDPOINT = "/client/sign-up/validate-api"

# Clerk sign-up hint
SCREEN_HINT_SIGN_UP = "sign_up"

# Error messages
ERROR_AUTH_TIMEOUT = "Login timed out. Please try again."
ERROR_STATE_MISMATCH = "Security validation failed (state mismatch). Please try again."
ERROR_AUTH_FAILED = "Authentication failed"
ERROR_SIGN_UP_FAILED = "Account creation failed"
ERROR_MAX_KEYS_REACHED = "Maximum number of API keys reached (50). Delete unused keys and try again."


def build_auth_api_url(path: str) -> str:
    """Build API URL for auth endpoints (key generation after OAuth)."""
    return f"{DEFAULT_BASE_URL}{path}"
