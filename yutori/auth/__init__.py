"""Authentication utilities for the Yutori SDK.

Lightweight imports (credentials, types) are eager. Heavyweight imports
(flow — pulls in http.server, threading, webbrowser) are lazy to avoid
penalizing SDK users who never use the OAuth login flow.
"""

from .credentials import clear_config, load_config, resolve_api_key, save_config
from .types import AuthStatus, LoginResult


def __getattr__(name: str):
    if name == "run_login_flow":
        from .flow import run_login_flow

        return run_login_flow
    if name == "run_register_flow":
        from .flow import run_register_flow

        return run_register_flow
    if name == "get_auth_status":
        from .flow import get_auth_status

        return get_auth_status
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "clear_config",
    "get_auth_status",
    "load_config",
    "resolve_api_key",
    "run_login_flow",
    "run_register_flow",
    "save_config",
    "AuthStatus",
    "LoginResult",
]
