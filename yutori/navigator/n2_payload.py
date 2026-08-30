"""Screenshot encoding and request budgeting for the Navigator n2 loop.

n2 requests carry the computer handler's screenshots as captured — the handler
defines the viewport (with any DPR scaling already removed); the SDK never
resizes, only re-encodes to ``image_format`` (WebP by default). Requests keep
images only in the two newest image-bearing messages (older ones leave an
``[older image omitted]`` marker) and must fit a 10 MB serialized request.
Coordinates are the model's 0-1000 space mapped onto the capture's dimensions.
"""

from __future__ import annotations

import base64
import copy
import io
from typing import Any, Optional

from PIL import Image

from .payload import estimate_messages_size_bytes

# Images are sent in this encoding unless the caller picks another; the frame's
# size is the computer handler's own capture, untouched.
DEFAULT_IMAGE_FORMAT = "webp"

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


def prepare_n2_image_data_url(url: str, image_format: str = DEFAULT_IMAGE_FORMAT) -> str:
    """Re-encode an image data URL to ``image_format``; returned unchanged when it already is.

    Never resizes: the frame stays at whatever size the computer handler
    captured (its viewport, with any DPR scaling already removed).
    """
    image_bytes, media_type = _decode_data_url(url)
    if media_type.lower() == f"image/{image_format.lower()}":
        return url
    with Image.open(io.BytesIO(image_bytes)) as source:
        output = io.BytesIO()
        source.convert("RGB").save(output, format=image_format.upper())
    return f"data:image/{image_format.lower()};base64,{base64.b64encode(output.getvalue()).decode('ascii')}"


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


OLDER_IMAGE_OMITTED_TEXT = "[older image omitted]"
"""The text left where a pruned screenshot used to be."""


def _strip_images_from_message(message: dict[str, Any], omitted_text: Optional[str] = None) -> None:
    content = message.get("content")
    if not isinstance(content, list):
        return
    if omitted_text is None:
        message["content"] = [
            part for part in content if not (isinstance(part, dict) and part.get("type") == "image_url")
        ]
        return
    replaced = [
        {"type": "text", "text": omitted_text} if isinstance(part, dict) and part.get("type") == "image_url" else part
        for part in content
    ]
    # Adjacent text parts merge into one, the marker directly concatenated —
    # the reference builder's rendering of a pruned frame.
    merged: list[Any] = []
    for part in replaced:
        if (
            merged
            and isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(merged[-1], dict)
            and merged[-1].get("type") == "text"
        ):
            merged[-1] = {"type": "text", "text": merged[-1].get("text", "") + part.get("text", "")}
        else:
            merged.append(part)
    message["content"] = merged


serialized_messages_bytes = estimate_messages_size_bytes


def retain_n2_image_window(
    messages: list[dict[str, Any]], *, omitted_text: Optional[str] = OLDER_IMAGE_OMITTED_TEXT
) -> list[dict[str, Any]]:
    """Copy messages and strip images outside the two newest image messages.

    Each pruned image is replaced in place by the ``omitted_text`` block (by
    default :data:`OLDER_IMAGE_OMITTED_TEXT`); with ``None`` the
    image part is dropped.
    """
    request_messages = copy.deepcopy(messages)
    image_indices = [index for index, message in enumerate(request_messages) if _message_image_parts(message)]
    for index in image_indices[:-2]:
        _strip_images_from_message(request_messages[index], omitted_text)
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


def convert_request_images(messages: list[dict[str, Any]], image_format: str = DEFAULT_IMAGE_FORMAT) -> None:
    """Re-encode every remaining request image to ``image_format``, in place."""
    for message in messages:
        for part in _message_image_parts(message):
            image_url = part.get("image_url")
            if not isinstance(image_url, dict) or not isinstance(image_url.get("url"), str):
                raise ValueError("n2 image_url content must contain a string url")
            image_url["url"] = prepare_n2_image_data_url(image_url["url"], image_format)
