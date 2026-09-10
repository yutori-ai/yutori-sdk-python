"""Native macOS runtime for Navigator n2 computer use."""

from __future__ import annotations

from .computer import (
    MacOSActionRefusedError,
    MacOSBackgroundDeliveryError,
    MacOSComputer,
    MacOSComputerError,
    MacOSFocusChangedError,
    MacOSRecoverableActionError,
    MacOSTargetCrashedError,
    MacOSTargetWindowChangedError,
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
from .sanitize import (
    COMMAND_PREVIEW_MAX_CHARACTERS,
    OUTPUT_PREVIEW_MAX_CHARACTERS,
    OUTPUT_PREVIEW_MAX_LINE_CHARACTERS,
    OUTPUT_PREVIEW_MAX_LINES,
    sanitize_command_preview,
    sanitize_output_preview,
)
from .types import (
    CancellationLatch,
    MacOSActionOutcome,
    MacOSPresentationCapabilities,
    MacOSPresentationStatus,
    MacOSWindowTarget,
    N2Observation,
    N2Presentation,
    ShellPresentationEvent,
)
from .visibility import application_hidden, unhide_application
from .windows import select_target_window, window_records

__all__ = [
    "COMMAND_PREVIEW_MAX_CHARACTERS",
    "OUTPUT_PREVIEW_MAX_CHARACTERS",
    "OUTPUT_PREVIEW_MAX_LINE_CHARACTERS",
    "OUTPUT_PREVIEW_MAX_LINES",
    "CancellationLatch",
    "FrontmostApp",
    "MacOSActionOutcome",
    "MacOSActionRefusedError",
    "MacOSBackgroundDeliveryError",
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
    "MacOSTargetWindowChangedError",
    "MacOSUncertainActionError",
    "MacOSWindowTarget",
    "application_hidden",
    "N2Observation",
    "N2Presentation",
    "PreparedMacOSOverlay",
    "ShellPresentationEvent",
    "check_macos_overlay",
    "frontmost_app",
    "prepare_macos_overlay",
    "sanitize_command_preview",
    "sanitize_output_preview",
    "select_target_window",
    "unhide_application",
    "window_records",
]
