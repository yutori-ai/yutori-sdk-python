"""Typed return values for authentication operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RegistrationState = Literal["creating_account", "logging_in"]

# Shared by yutori/cli/commands/auth.py and yutori/cli/commands/install_flow.py,
# the two ``on_registration_state`` callbacks passed to ``run_login_flow``, so
# the wording can't drift out of sync between the two entrypoints.
REGISTRATION_STATE_MESSAGES: dict[RegistrationState, str] = {
    "creating_account": "Creating account...",
    "logging_in": "Logging in...",
}


@dataclass
class LoginResult:
    """Result of a login attempt."""

    success: bool
    api_key: str | None = None
    error: str | None = None
    auth_url: str | None = None


@dataclass
class AuthStatus:
    """Current authentication status."""

    authenticated: bool
    masked_key: str | None = None
    source: str | None = None  # "config_file", "env_var", or None
    config_path: str | None = None
