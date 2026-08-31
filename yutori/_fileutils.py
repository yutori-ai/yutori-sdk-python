"""Shared atomic local-file-write helper.

Used for small state/secret files (the CLI's saved API key, the macOS overlay
build cache's pointer files) where a reader must never observe a partial
write, even if the process is interrupted mid-write.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, contents: str, *, mode: int = 0o600) -> None:
    """Atomically write ``contents`` to ``path``.

    Writes to a temp file in ``path``'s own directory (so the final
    ``os.replace`` is a same-filesystem rename), chmods it to ``mode``, then
    renames it into place. A concurrent reader of ``path`` therefore always
    sees either the previous complete contents or the new complete contents,
    never a partial write. The temp file is removed if anything raises before
    the rename lands; ``os.replace`` itself leaves nothing behind to clean up
    on the success path.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(contents)
        tmp_path.chmod(mode)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)
