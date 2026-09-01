"""Native macOS runtime for Navigator n2 computer use."""

from __future__ import annotations

from .computer import (
    MacOSActionRefusedError,
    MacOSComputer,
    MacOSComputerError,
    MacOSFocusChangedError,
    MacOSRecoverableActionError,
    MacOSTargetCrashedError,
    MacOSUncertainActionError,
)
from .frontmost import FrontmostApp, frontmost_app
from .overlay_build import (
    MacOSOverlayCheck,
    MacOSOverlayPreparationError,
    PreparedMacOSOverlay,
    check_macos_overlay,
    prepare_macos_overlay,
)
from .presentation import MacOSPresentationController, MacOSPresentationError
from .sanitize import COMMAND_PREVIEW_MAX_CHARACTERS, sanitize_command_preview
from .types import (
    CancellationLatch,
    MacOSPresentationCapabilities,
    MacOSPresentationStatus,
    N2Observation,
    N2Presentation,
    ShellPresentationEvent,
)

__all__ = [
    "COMMAND_PREVIEW_MAX_CHARACTERS",
    "CancellationLatch",
    "FrontmostApp",
    "MacOSActionRefusedError",
    "MacOSComputer",
    "MacOSComputerError",
    "MacOSFocusChangedError",
    "MacOSOverlayCheck",
    "MacOSOverlayPreparationError",
    "MacOSPresentationCapabilities",
    "MacOSPresentationController",
    "MacOSPresentationError",
    "MacOSPresentationStatus",
    "MacOSRecoverableActionError",
    "MacOSTargetCrashedError",
    "MacOSUncertainActionError",
    "N2Observation",
    "N2Presentation",
    "PreparedMacOSOverlay",
    "ShellPresentationEvent",
    "check_macos_overlay",
    "frontmost_app",
    "prepare_macos_overlay",
    "sanitize_command_preview",
]
