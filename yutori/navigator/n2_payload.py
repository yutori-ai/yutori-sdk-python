"""Screenshot encoding and request budgeting for the Navigator n2 loop.

n2 requests carry full-frame screenshots re-encoded per an :class:`N2ImageProfile`
(by default aspect-preserving WebP bounded by 1280x800; the evaluation harness
used PNG at exactly 1280x720), keep images only in the two newest image-bearing
messages, and must fit a 10 MB serialized request. Coordinates stay in the
model's 0-1000 space mapped against the ORIGINAL capture's native dimensions, so
the model decides on the downscaled image while actions land on the native one.
"""

from __future__ import annotations

import base64
import copy
import io
from dataclasses import dataclass
from typing import Any, Optional

from PIL import Image

from .payload import estimate_messages_size_bytes

N2_MODEL_IMAGE_MAX_WIDTH = 1280
N2_MODEL_IMAGE_MAX_HEIGHT = 800
N2_MODEL_IMAGE_QUALITY = 80


@dataclass(frozen=True)
class N2ImageProfile:
    """How screenshots are re-encoded before they reach the model.

    ``exact=False`` shrinks the frame to fit inside ``size`` while keeping its
    aspect ratio (never upscaling); ``exact=True`` resizes it to ``size`` exactly,
    which is what the evaluation harness does (1920x1080 captures become
    1280x720 PNGs). ``quality`` applies to lossy formats only.
    """

    format: str = "WEBP"
    size: tuple[int, int] = (N2_MODEL_IMAGE_MAX_WIDTH, N2_MODEL_IMAGE_MAX_HEIGHT)
    quality: Optional[int] = N2_MODEL_IMAGE_QUALITY
    exact: bool = False

    @property
    def media_type(self) -> str:
        return f"image/{self.format.lower()}"


DEFAULT_IMAGE_PROFILE = N2ImageProfile()
HARNESS_IMAGE_PROFILE = N2ImageProfile(format="PNG", size=(1280, 720), quality=None, exact=True)

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


def prepare_n2_image_data_url(url: str, profile: N2ImageProfile = DEFAULT_IMAGE_PROFILE) -> str:
    """Re-encode a full-frame screenshot per ``profile`` (default: WebP bounded by 1280x800)."""
    image_bytes, _ = _decode_data_url(url)
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = source.convert("RGB")
        if profile.exact:
            if image.size != tuple(profile.size):
                image = image.resize(tuple(profile.size), Image.Resampling.LANCZOS)
        else:
            image.thumbnail(tuple(profile.size), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        save_kwargs: dict[str, Any] = {}
        if profile.quality is not None and profile.format.upper() not in {"PNG", "BMP"}:
            save_kwargs["quality"] = profile.quality
        image.save(output, format=profile.format.upper(), **save_kwargs)
    return f"data:{profile.media_type};base64,{base64.b64encode(output.getvalue()).decode('ascii')}"


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
"""The marker the evaluation harness leaves where a pruned screenshot used to be."""


def _strip_images_from_message(message: dict[str, Any], omitted_text: Optional[str] = None) -> None:
    content = message.get("content")
    if not isinstance(content, list):
        return
    if omitted_text is None:
        message["content"] = [
            part for part in content if not (isinstance(part, dict) and part.get("type") == "image_url")
        ]
        return
    message["content"] = [
        {"type": "text", "text": omitted_text} if isinstance(part, dict) and part.get("type") == "image_url" else part
        for part in content
    ]


serialized_messages_bytes = estimate_messages_size_bytes


def retain_n2_image_window(
    messages: list[dict[str, Any]], *, omitted_text: Optional[str] = None
) -> list[dict[str, Any]]:
    """Copy messages and strip images outside the two newest image messages.

    With ``omitted_text`` each pruned image is replaced in place by that text
    block (the harness sends :data:`OLDER_IMAGE_OMITTED_TEXT`); otherwise the
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


def convert_request_images(messages: list[dict[str, Any]], profile: N2ImageProfile = DEFAULT_IMAGE_PROFILE) -> None:
    """Re-encode every remaining request image per ``profile``, in place."""
    for message in messages:
        for part in _message_image_parts(message):
            image_url = part.get("image_url")
            if not isinstance(image_url, dict) or not isinstance(image_url.get("url"), str):
                raise ValueError("n2 image_url content must contain a string url")
            image_url["url"] = prepare_n2_image_data_url(image_url["url"], profile)
