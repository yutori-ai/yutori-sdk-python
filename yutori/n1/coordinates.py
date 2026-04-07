"""Coordinate helpers for n1's 1000x1000 action space."""

from __future__ import annotations

import math
from typing import Sequence

N1_COORDINATE_SCALE = 1000


def denormalize_coordinates(
    coordinates: Sequence[int | float],
    width: int,
    height: int,
    *,
    scale: int = N1_COORDINATE_SCALE,
    clamp: bool = True,
) -> tuple[int, int]:
    """Convert normalized n1 coordinates into viewport pixels.

    When *clamp* is ``True`` (the default), the result is clamped to
    ``[0, width - 1]`` and ``[0, height - 1]`` so the returned pixel
    indices are always valid for a viewport of the given size.
    """

    x, y = _coerce_coordinates(coordinates)
    _validate_dimension("width", width)
    _validate_dimension("height", height)
    _validate_scale(scale)

    raw_x = round(x / scale * width)
    raw_y = round(y / scale * height)

    if not clamp:
        return raw_x, raw_y

    return _clamp_to_dimension(raw_x, width), _clamp_to_dimension(raw_y, height)


def normalize_coordinates(
    coordinates: Sequence[int | float],
    width: int,
    height: int,
    *,
    scale: int = N1_COORDINATE_SCALE,
    clamp: bool = True,
) -> tuple[int, int]:
    """Convert viewport pixels into normalized n1 coordinates.

    When *clamp* is ``True`` (the default), the result is clamped to
    ``[0, scale]`` so the returned coordinates stay within n1's
    normalized action space.
    """

    x, y = _coerce_coordinates(coordinates)
    _validate_dimension("width", width)
    _validate_dimension("height", height)
    _validate_scale(scale)

    raw_x = round(x / width * scale)
    raw_y = round(y / height * scale)

    if not clamp:
        return raw_x, raw_y

    return _clamp(raw_x, 0, scale), _clamp(raw_y, 0, scale)


def _coerce_coordinates(coordinates: Sequence[int | float]) -> tuple[float, float]:
    if coordinates is None:
        raise ValueError("coordinates must not be None")
    if len(coordinates) != 2:
        raise ValueError(f"coordinates must contain exactly 2 items, got {coordinates!r}")
    x, y = float(coordinates[0]), float(coordinates[1])
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError(f"coordinates must be finite numbers, got {coordinates!r}")
    return x, y


def _validate_dimension(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")


def _validate_scale(scale: int) -> None:
    if scale <= 0:
        raise ValueError(f"scale must be positive, got {scale}")


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def _clamp_to_dimension(value: int, dimension: int) -> int:
    return _clamp(value, 0, dimension - 1)
