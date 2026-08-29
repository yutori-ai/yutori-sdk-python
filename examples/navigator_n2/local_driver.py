"""Compatibility wrapper for the public SDK macOS N2 computer handler."""

from yutori.navigator.macos import MacOSComputer, MacOSComputerError

CuaDriverDesktop = MacOSComputer
CuaDriverError = MacOSComputerError

__all__ = ["CuaDriverDesktop", "CuaDriverError"]
