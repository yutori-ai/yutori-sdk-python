"""Authentication utilities for the Yutori SDK.

Lightweight imports (credentials, types) are eager. Heavyweight imports
(flow — pulls in http.server, threading, webbrowser) are lazy to avoid
penalizing SDK users who never use the OAuth login flow.
"""

from .credentials import clear_config, load_config, require_api_key, resolve_api_key, save_config
from .types import AuthStatus, LoginResult

# Names lazily re-exported from .flow (see module docstring for why).
_LAZY_FLOW_ATTRS = frozenset({"run_login_flow", "get_auth_status"})


def __getattr__(name: str):
    if name in _LAZY_FLOW_ATTRS:
        from . import flow

        return getattr(flow, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "clear_config",
    "get_auth_status",
    "load_config",
    "require_api_key",
    "resolve_api_key",
    "run_login_flow",
    "save_config",
    "AuthStatus",
    "LoginResult",
]
