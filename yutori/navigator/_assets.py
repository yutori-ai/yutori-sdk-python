"""Helpers for loading bundled static assets used by Navigator utilities."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=None)
def load_js_asset(name: str) -> str:
    return files("yutori.navigator").joinpath("js", name).read_text(encoding="utf-8").strip()
