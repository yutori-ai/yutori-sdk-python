"""Load bundled JS tool scripts for Navigator helpers."""

from __future__ import annotations

from .._assets import _load_js_resource


def load_tool_script(name: str) -> str:
    return _load_js_resource(__package__, name)
