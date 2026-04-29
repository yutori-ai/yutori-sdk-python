"""Credential storage for the Yutori SDK.

Stores API keys in ~/.yutori/config.json with restrictive permissions.
This matches the pattern used by ~/.aws/credentials, ~/.npmrc, etc.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..exceptions import AuthenticationError
from .constants import CONFIG_DIR, CONFIG_FILE


def get_config_path() -> Path:
    return Path.home() / CONFIG_DIR / CONFIG_FILE


def load_config() -> dict[str, Any] | None:
    """Load config from ~/.yutori/config.json.

    Returns None if file doesn't exist, is corrupt, or is not a dict.
    """
    config_path = get_config_path()
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text())
        if not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def save_config(api_key: str) -> None:
    """Save API key to ~/.yutori/config.json with atomic write and restrictive permissions.

    - Directory: 0700 (owner read/write/execute only)
    - File: 0600 (owner read/write only)
    - Atomic: writes to temp file in same dir, then os.replace()
    """
    config_path = get_config_path()
    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(config_dir, 0o700)

    content = json.dumps({"api_key": api_key}, indent=2)

    # Atomic write: temp file in same directory, then rename.
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=config_dir,
        prefix=".config_",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(tmp.name)
    try:
        with tmp as f:
            f.write(content)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, config_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def clear_config() -> None:
    """Delete the config file if it exists."""
    config_path = get_config_path()
    if config_path.exists():
        config_path.unlink()


_PLACEHOLDER_KEYS = frozenset({"YOUR_API_KEY"})


def _is_real_key(key: str | None) -> bool:
    return bool(key and key.strip() and key.strip() not in _PLACEHOLDER_KEYS)


def get_stored_api_key() -> str | None:
    """Return the stored API key from the config file, or ``None``.

    Returns ``None`` when the config file is absent, unreadable, malformed,
    or holds a placeholder value such as ``"YOUR_API_KEY"``.
    """
    config = load_config()
    if not config:
        return None
    stored = config.get("api_key")
    if isinstance(stored, str) and _is_real_key(stored):
        return stored
    return None


def _resolve_api_key_with_source(api_key: str | None = None) -> tuple[str, str] | None:
    """Resolve an API key using the standard precedence chain and report its source.

    Order: explicit parameter > ``YUTORI_API_KEY`` env var > config file.
    Placeholder values like ``"YOUR_API_KEY"`` are treated as missing.

    Returns a ``(key, source)`` tuple where ``source`` is one of
    ``"param"``, ``"env_var"``, or ``"config_file"``, or ``None`` if no key
    is found. Callers that only need the key itself should use
    :func:`resolve_api_key`.
    """
    if _is_real_key(api_key):
        return api_key, "param"

    env_key = os.environ.get("YUTORI_API_KEY")
    if _is_real_key(env_key):
        return env_key, "env_var"

    stored_key = get_stored_api_key()
    if stored_key is not None:
        return stored_key, "config_file"

    return None


def resolve_api_key(api_key: str | None = None) -> str | None:
    """Resolve an API key using the standard precedence chain.

    Order: explicit parameter > YUTORI_API_KEY env var > config file.
    Placeholder values like ``"YOUR_API_KEY"`` are treated as missing.
    Returns None if no key is found (caller decides error behavior).
    """
    resolved = _resolve_api_key_with_source(api_key)
    return resolved[0] if resolved else None


def require_api_key(api_key: str | None = None) -> str:
    """Resolve an API key and raise if none can be found.

    Same precedence chain as :func:`resolve_api_key`, but raises
    :class:`AuthenticationError` with a uniform user-facing message when
    no real key is available.
    """
    resolved = resolve_api_key(api_key)
    if not resolved:
        raise AuthenticationError(
            "No API key provided. Run 'yutori auth login', set YUTORI_API_KEY, or pass api_key."
        )
    return resolved
