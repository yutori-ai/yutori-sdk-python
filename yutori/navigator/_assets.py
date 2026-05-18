"""Helpers for loading bundled static assets used by Navigator utilities."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=None)
def _load_js_resource(package: str, name: str) -> str:
    """Read a bundled ``<package>/js/<name>`` file, stripped of surrounding whitespace."""
    return files(package).joinpath("js", name).read_text(encoding="utf-8").strip()


def load_js_asset(name: str) -> str:
    return _load_js_resource("yutori.navigator", name)
