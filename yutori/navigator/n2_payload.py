"""Screenshot encoding and request budgeting for the Navigator n2 loop.

n2 requests carry the computer handler's screenshots as captured — the handler
defines the viewport (with any DPR scaling already removed); the SDK never
resizes, only re-encodes to ``image_format`` (WebP by default).

Requests carry the WHOLE screenshot history and must fit a 10 MB serialized
request. Sending every frame is deliberate, and matches the reference harness:
the server already keeps images only in the two newest image-bearing messages
before it serves the model, so a client-side window buys the model nothing —
while the request log the run's replay is built from records exactly the
messages the client sent, so a client that trims first is the only reason a
replay has gaps. :func:`prune_n2_screenshots_to_budget` therefore drops frames
only to fit the wire cap, oldest first.

Coordinates are the model's 0-1000 space mapped onto the capture's dimensions.
"""

from __future__ import annotations

import base64
import copy
import io
import json
from typing import Any, Optional

from PIL import Image

from .payload import estimate_messages_size_bytes

# Images are sent in this encoding unless the caller picks another; the frame's
# size is the computer handler's own capture, untouched.
DEFAULT_IMAGE_FORMAT = "webp"

MAX_REQUEST_BODY_BYTES = 10_000_000
# Slack left below the cap when deciding how much screenshot history fits: the
# budget is measured over the messages array alone, so this covers the rest of
# the serialized body (model, tool_set, sampling fields, JSON structure) and
# keeps the loop's own exact-size guard from tripping after a prune. Matches the
# reference harness's headroom; a larger allowance only throws away frames that
# would have fit.
REQUEST_ENVELOPE_ALLOWANCE_BYTES = 64 * 1024
DEFAULT_MAX_MESSAGES_BYTES = MAX_REQUEST_BODY_BYTES - REQUEST_ENVELOPE_ALLOWANCE_BYTES


def _data_url_media_type(url: str) -> str:
    """The media type of a base64 data URL, without decoding its payload."""
    if not isinstance(url, str) or not url.startswith("data:") or "," not in url:
        raise ValueError("n2 screenshots must be base64 data URLs")
    header = url.split(",", 1)[0]
    if ";base64" not in header:
        raise ValueError("n2 screenshots must use base64 data URLs")
    return header[5:].split(";", 1)[0]


def _decode_data_url(url: str) -> "tuple[bytes, str]":
    # Validation and header parsing live in _data_url_media_type; this only adds
    # the base64 decode, so the two share one definition of what a valid n2
    # screenshot data URL looks like.
    media_type = _data_url_media_type(url)
    _, encoded = url.split(",", 1)
    return base64.b64decode(encoded), media_type


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
    # Read the media type off the header rather than decoding first: a request
    # now carries the whole history, so the pass-through case is walked once per
    # frame per step, and base64-decoding megabytes only to compare a string is
    # the kind of cost that shows up as latency on a long run.
    if _data_url_media_type(url).lower() == f"image/{image_format.lower()}":
        return url
    image_bytes, _ = _decode_data_url(url)
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


def _serialized_bytes(value: Any) -> int:
    """The JSON-serialized byte size of one content part, matching the messages estimate."""
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def retain_n2_image_window(
    messages: list[dict[str, Any]], *, omitted_text: Optional[str] = OLDER_IMAGE_OMITTED_TEXT
) -> list[dict[str, Any]]:
    """Copy messages and strip images outside the two newest image messages.

    Each pruned image is replaced in place by the ``omitted_text`` block (by
    default :data:`OLDER_IMAGE_OMITTED_TEXT`); with ``None`` the
    image part is dropped.

    The loop does NOT apply this — it sends the full history and lets
    :func:`prune_n2_screenshots_to_budget` drop only what will not fit, because
    the server applies this same window itself before serving the model. Kept
    for a harness that has its own reason to send less than it has (a metered
    uplink, say), and as the executable statement of what the server's window
    does.
    """
    request_messages = copy.deepcopy(messages)
    image_indices = [index for index, message in enumerate(request_messages) if _message_image_parts(message)]
    for index in image_indices[:-2]:
        _strip_images_from_message(request_messages[index], omitted_text)
    return request_messages


def _drop_first_image(content: list[Any]) -> "dict[str, Any] | None":
    """Remove the first ``image_url`` part from a content list, returning it."""
    for position, part in enumerate(content):
        if isinstance(part, dict) and part.get("type") == "image_url":
            return content.pop(position)
    return None


def prune_n2_screenshots_to_budget(
    messages: list[dict[str, Any]],
    max_messages_bytes: int = DEFAULT_MAX_MESSAGES_BYTES,
) -> int:
    """Drop the oldest screenshots, in place, until *messages* fits the budget.

    Returns how many were dropped, so a caller can surface that the run's replay
    was truncated. Nothing is dropped when the history already fits, which is the
    common case and the whole point: every frame reaches the request log the
    replay is built from.

    A dropped frame leaves NO marker. That is what the server's own window does
    to the frames it strips, so a request pruned here and a request pruned there
    reach the model as the same conversation; injecting a marker per dropped
    frame instead hands the model text the reference harness never produces.
    The newest image is never dropped — it is the observation the model is being
    asked to act on.

    Raises:
        ValueError: if the request cannot fit even with one frame left.
    """
    size_bytes = serialized_messages_bytes(messages)
    if size_bytes <= max_messages_bytes:
        return 0

    # One entry per image part, oldest first; a message holding several images
    # appears once per image, and each visit takes that message's first
    # remaining one. The last entry is the current observation and is never
    # visited.
    image_contents: list[list[Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        image_contents.extend(content for part in _message_image_parts(message))

    dropped = 0
    # Running estimate rather than a re-serialization per drop: the payload is
    # megabytes by construction, so measuring it once per dropped frame turned a
    # trim of N frames into N passes over all of it. Dropping an array element
    # removes its serialization plus one separating comma; an image that was the
    # only part of its content list has no comma to remove, so the estimate can
    # run one byte low per such frame. The exact re-measure below settles it.
    for content in image_contents[:-1]:
        if size_bytes <= max_messages_bytes:
            break
        part = _drop_first_image(content)
        if part is None:
            continue
        size_bytes -= _serialized_bytes(part) + 1
        dropped += 1

    size_bytes = serialized_messages_bytes(messages)
    for content in image_contents[:-1]:
        if size_bytes <= max_messages_bytes:
            break
        if _drop_first_image(content) is None:
            continue
        dropped += 1
        size_bytes = serialized_messages_bytes(messages)

    if size_bytes > max_messages_bytes:
        raise ValueError(
            "The newest n2 screenshot message cannot fit within the serialized messages budget. "
            "Reduce screenshot dimensions/quality or shorten non-image request content."
        )
    return dropped


def convert_request_images(messages: list[dict[str, Any]], image_format: str = DEFAULT_IMAGE_FORMAT) -> None:
    """Re-encode every remaining request image to ``image_format``, in place."""
    for message in messages:
        for part in _message_image_parts(message):
            image_url = part.get("image_url")
            if not isinstance(image_url, dict) or not isinstance(image_url.get("url"), str):
                raise ValueError("n2 image_url content must contain a string url")
            image_url["url"] = prepare_n2_image_data_url(image_url["url"], image_format)
