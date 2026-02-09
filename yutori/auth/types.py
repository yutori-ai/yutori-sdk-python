"""Typed return values for authentication operations."""

from __future__ import annotations

from dataclasses import dataclass


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
