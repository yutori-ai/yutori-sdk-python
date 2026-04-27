"""Load bundled JS tool scripts for Navigator helpers."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=None)
def load_tool_script(name: str) -> str:
    return files(__package__).joinpath("js", name).read_text(encoding="utf-8").strip()
