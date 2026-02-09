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

    # Atomic write: temp file in same directory, then rename
    fd, tmp_path = tempfile.mkstemp(dir=config_dir, prefix=".config_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, config_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def clear_config() -> None:
    """Delete the config file if it exists."""
    config_path = get_config_path()
    if config_path.exists():
        config_path.unlink()


def resolve_api_key(api_key: str | None = None) -> str | None:
    """Resolve an API key using the standard precedence chain.

    Order: explicit parameter > YUTORI_API_KEY env var > config file.
    Returns None if no key is found (caller decides error behavior).
    """
    if api_key:
        return api_key

    env_key = os.environ.get("YUTORI_API_KEY")
    if env_key:
        return env_key

    config = load_config()
    if config:
        stored_key = config.get("api_key")
        if stored_key and isinstance(stored_key, str):
            return stored_key

    return None
