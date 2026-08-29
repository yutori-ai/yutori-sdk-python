"""Screenshot encoding and request budgeting for the Navigator n2 loop.

n2 requests carry full-frame screenshots re-encoded as aspect-preserving WebP
bounded by 1280x800, keep images only in the two newest image-bearing messages,
and must fit a 10 MB serialized request. Coordinates stay in the model's 0-1000
space mapped against the ORIGINAL capture's native dimensions, so the model
decides on the downscaled image while actions land on the native one.
"""

from __future__ import annotations

import base64
import copy
import io
from typing import Any

from PIL import Image

from .payload import estimate_messages_size_bytes

N2_MODEL_IMAGE_MAX_WIDTH = 1280
N2_MODEL_IMAGE_MAX_HEIGHT = 800
N2_MODEL_IMAGE_QUALITY = 80

MAX_REQUEST_BODY_BYTES = 10_000_000
REQUEST_ENVELOPE_ALLOWANCE_BYTES = 500_000
DEFAULT_MAX_MESSAGES_BYTES = MAX_REQUEST_BODY_BYTES - REQUEST_ENVELOPE_ALLOWANCE_BYTES


def _decode_data_url(url: str) -> "tuple[bytes, str]":
    if not isinstance(url, str) or not url.startswith("data:") or "," not in url:
        raise ValueError("n2 screenshots must be base64 data URLs")
    header, encoded = url.split(",", 1)
    if ";base64" not in header:
        raise ValueError("n2 screenshots must use base64 data URLs")
    return base64.b64decode(encoded), header[5:].split(";", 1)[0]


def image_dimensions(url: str) -> "tuple[int, int]":
    """The pixel dimensions of a data-URL image, without re-encoding it."""
    image_bytes, _ = _decode_data_url(url)
    with Image.open(io.BytesIO(image_bytes)) as image:
        return image.size


def prepare_n2_image_data_url(url: str) -> str:
    """Return a full-frame, aspect-preserving WebP bounded by 1280x800."""
    image_bytes, _ = _decode_data_url(url)
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = source.convert("RGB")
        image.thumbnail(
            (N2_MODEL_IMAGE_MAX_WIDTH, N2_MODEL_IMAGE_MAX_HEIGHT),
            Image.Resampling.LANCZOS,
        )
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=N2_MODEL_IMAGE_QUALITY)
    return f"data:image/webp;base64,{base64.b64encode(output.getvalue()).decode('ascii')}"


def _message_image_parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [part for part in content if isinstance(part, dict) and part.get("type") == "image_url"]


def latest_image_url(messages: list[dict[str, Any]]) -> "str | None":
    for message in reversed(messages):
        for part in reversed(_message_image_parts(message)):
            image_url = part.get("image_url")
            if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                return image_url["url"]
    return None


def _strip_images_from_message(message: dict[str, Any]) -> None:
    content = message.get("content")
    if not isinstance(content, list):
        return
    message["content"] = [part for part in content if not (isinstance(part, dict) and part.get("type") == "image_url")]


serialized_messages_bytes = estimate_messages_size_bytes


def retain_n2_image_window(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy messages and strip images outside the two newest image messages."""
    request_messages = copy.deepcopy(messages)
    image_indices = [index for index, message in enumerate(request_messages) if _message_image_parts(message)]
    for index in image_indices[:-2]:
        _strip_images_from_message(request_messages[index])
    return request_messages


def fit_n2_request_images_to_budget(
    messages: list[dict[str, Any]],
    max_messages_bytes: int = DEFAULT_MAX_MESSAGES_BYTES,
) -> list[dict[str, Any]]:
    """Copy an already-windowed request and drop its older image message if needed."""
    request_messages = copy.deepcopy(messages)
    image_indices = [index for index, message in enumerate(request_messages) if _message_image_parts(message)]

    if serialized_messages_bytes(request_messages) <= max_messages_bytes:
        return request_messages

    retained_indices = image_indices[-2:]
    if len(retained_indices) == 2:
        _strip_images_from_message(request_messages[retained_indices[0]])
    if serialized_messages_bytes(request_messages) <= max_messages_bytes:
        return request_messages

    raise ValueError(
        "The newest n2 screenshot message cannot fit within the serialized messages budget. "
        "Reduce screenshot dimensions/quality or shorten non-image request content."
    )


def convert_request_images(messages: list[dict[str, Any]]) -> None:
    """Re-encode every remaining request image to the model's WebP contract, in place."""
    for message in messages:
        for part in _message_image_parts(message):
            image_url = part.get("image_url")
            if not isinstance(image_url, dict) or not isinstance(image_url.get("url"), str):
                raise ValueError("n2 image_url content must contain a string url")
            image_url["url"] = prepare_n2_image_data_url(image_url["url"])
