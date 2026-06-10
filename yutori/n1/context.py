"""Compatibility shim for yutori.navigator.context."""

from __future__ import annotations

from yutori.navigator import context as _target

from ._compat import alias_module_contents, warn_renamed

warn_renamed(__name__)
alias_module_contents(globals(), _target)
