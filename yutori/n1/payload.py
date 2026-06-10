"""Compatibility shim for yutori.navigator.payload."""

from __future__ import annotations

from yutori.navigator import payload as _target

from ._compat import alias_module_contents, warn_renamed

warn_renamed(__name__)
alias_module_contents(globals(), _target)
