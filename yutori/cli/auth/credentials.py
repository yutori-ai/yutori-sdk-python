"""Credential storage for the Yutori CLI."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from ..constants import (
    CREDENTIALS_DIR_NAME,
    CREDENTIALS_FILE_NAME,
    KEYRING_SERVICE_NAME,
    KEYRING_USERNAME,
)


class StoredCredentials(TypedDict):
    api_key: str
    created_at: str
    key_name: str


def _get_credentials_file_path() -> Path:
    """Get the path to the credentials file."""
    config_dir = Path.home() / ".config" / CREDENTIALS_DIR_NAME
    return config_dir / CREDENTIALS_FILE_NAME


def _try_keyring_get() -> str | None:
    """Try to get credentials from system keyring."""
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME)
    except Exception:
        return None


def _try_keyring_set(api_key: str) -> bool:
    """Try to store credentials in system keyring."""
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME, api_key)
        return True
    except Exception:
        return False


def _try_keyring_delete() -> bool:
    """Try to delete credentials from system keyring."""
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME)
        return True
    except Exception:
        return False


def get_credentials() -> StoredCredentials | None:
    """Get stored credentials.

    Tries keyring first, then falls back to file storage.

    Returns:
        StoredCredentials if found, None otherwise.
    """
    api_key = _try_keyring_get()
    if api_key:
        return StoredCredentials(
            api_key=api_key,
            created_at="",
            key_name="",
        )

    credentials_file = _get_credentials_file_path()
    if credentials_file.exists():
        try:
            with open(credentials_file) as f:
                data = json.load(f)
                return StoredCredentials(
                    api_key=data.get("api_key", ""),
                    created_at=data.get("created_at", ""),
                    key_name=data.get("key_name", ""),
                )
        except (json.JSONDecodeError, OSError):
            return None

    return None


def save_credentials(api_key: str, key_name: str) -> None:
    """Save credentials.

    Tries keyring first, then falls back to file storage.

    Args:
        api_key: The API key to store.
        key_name: The name of the API key.
    """
    if _try_keyring_set(api_key):
        return

    credentials_file = _get_credentials_file_path()
    credentials_file.parent.mkdir(parents=True, exist_ok=True)

    credentials = StoredCredentials(
        api_key=api_key,
        created_at=datetime.now(timezone.utc).isoformat(),
        key_name=key_name,
    )

    with open(credentials_file, "w") as f:
        json.dump(credentials, f, indent=2)

    os.chmod(credentials_file, stat.S_IRUSR | stat.S_IWUSR)


def clear_credentials() -> bool:
    """Clear stored credentials.

    Removes from both keyring and file storage.

    Returns:
        True if any credentials were cleared, False otherwise.
    """
    cleared = False

    if _try_keyring_delete():
        cleared = True

    credentials_file = _get_credentials_file_path()
    if credentials_file.exists():
        try:
            credentials_file.unlink()
            cleared = True
        except OSError:
            pass

    return cleared
