"""Helpers for loading bundled static assets used by n1 utilities."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=None)
def load_js_asset(name: str) -> str:
    return files("yutori.n1").joinpath("js", name).read_text(encoding="utf-8").strip()
