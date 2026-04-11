"""Optional trajectory logging and replay helpers for navigator browser loops.

These helpers are only for local debugging and inspection. Agent runs do not
depend on them, and callers can ignore this module entirely if they do not want
to persist replay artifacts.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .content import extract_text_content
from .coordinates import N1_COORDINATE_SCALE

_ACTION_COLOR_CLASS = {
    "left_click": "click",
    "middle_click": "click",
    "double_click": "click",
    "triple_click": "click",
    "right_click": "click",
    "click": "click",
    "scroll": "scroll",
    "type": "type",
    "hover": "hover",
    "mouse_move": "hover",
}
_PREFERRED_ACTION_KEYS = (
    "ref",
    "coordinates",
    "center_coordinates",
    "start_coordinates",
    "end_coordinates",
    "text",
    "value",
    "direction",
    "amount",
    "key",
    "key_comb",
    "url",
    "duration",
    "press_enter_after",
    "clear_before_typing",
)


def log_formatter(record: dict, *, colorize: bool = True) -> str:
    """Format log messages for optional loguru-based task/replay logs."""

    extra = record["extra"]
    if colorize:
        result = (
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{file}</cyan>:<cyan>{line}</cyan> | "
        )
        if "task_id" in extra:
            result += "<magenta>{extra[task_id]}</magenta> | "
        if "attempt" in extra:
            result += "<blue>{extra[attempt]}</blue> | "
        result += "<level>{message}</level>\n{exception}"
    else:
        result = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {file}:{line} | "
        if "task_id" in extra:
            result += "{extra[task_id]} | "
        if "attempt" in extra:
            result += "{extra[attempt]} | "
        result += "{message}\n{exception}"
    return result


def make_run_id(*, prefix: str = "run", label: str | None = None) -> str:
    """Create a filesystem-friendly replay id for optional local artifacts."""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_prefix = _slugify(prefix) or "run"
    clean_label = _slugify(label or "")
    if clean_label:
        return f"{clean_prefix}_{clean_label}_{timestamp}"
    return f"{clean_prefix}_{timestamp}"


class TrajectoryRecorder:
    """Persist optional local replay artifacts for a navigator run.

    This recorder is intentionally separate from the agent loop itself. If you
    do not want replay files, skip constructing it and the rest of your loop can
    stay unchanged.
    """

    def __init__(self, save_dir: str | Path, run_id: str) -> None:
        self.save_dir = Path(save_dir)
        self.run_id = run_id
        self.item_dir = self.save_dir / run_id
        self.item_dir.mkdir(parents=True, exist_ok=True)

    def artifact_path(self, name: str) -> Path:
        """Return the output path for one optional replay artifact."""

        return self.item_dir / name

    async def save_json(
        self,
        filename: str,
        data: Any,
        *,
        indent: int | None = 2,
        default: Any | None = None,
    ) -> None:
        """Write one optional JSON artifact into the replay directory."""

        path = self.artifact_path(filename)
        text = json.dumps(data, indent=indent, default=default or _json_default)
        await asyncio.to_thread(path.write_text, text, encoding="utf-8")

    async def load_json(self, filename: str) -> Any | None:
        """Read one optional JSON artifact if it exists."""

        path = self.artifact_path(filename)
        if not path.exists():
            return None
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return json.loads(text)

    async def save_jsonl(self, filename: str, records: list[Any]) -> None:
        """Write one optional JSONL artifact into the replay directory."""

        path = self.artifact_path(filename)
        lines = [json.dumps(record, default=_json_default) for record in records]
        await asyncio.to_thread(path.write_text, "\n".join(lines), encoding="utf-8")

    async def load_jsonl(self, filename: str) -> list[Any]:
        """Read one optional JSONL artifact if it exists."""

        path = self.artifact_path(filename)
        if not path.exists():
            return []
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        if not text.strip():
            return []
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    async def save_messages(self, messages: list[dict]) -> None:
        """Persist the full message history for optional replay/debugging."""

        await self.save_jsonl("messages.jsonl", messages)

    async def load_messages(self) -> list[dict]:
        """Load the optional replay message history if it was written."""

        return await self.load_jsonl("messages.jsonl")

    async def save_step_payloads(self, step_payloads: list[dict[str, Any]]) -> None:
        """Persist sanitized request/response payloads for optional replay."""

        sanitized = [sanitize_step_payload(payload) for payload in step_payloads]
        await self.save_jsonl("step_payloads.jsonl", sanitized)

    async def load_step_payloads(self) -> list[dict[str, Any]]:
        """Load optional per-step request/response payloads if present."""

        return await self.load_jsonl("step_payloads.jsonl")

    async def save_html(
        self,
        messages: list[dict],
        result: object | None = None,
        step_payloads: list[dict[str, Any]] | None = None,
        *,
        coord_space_width: int = N1_COORDINATE_SCALE,
        coord_space_height: int = N1_COORDINATE_SCALE,
    ) -> None:
        """Render the optional static HTML replay viewer for one run."""

        path = self.artifact_path("visualization.html")
        html = generate_visualization_html(
            task_id=self.run_id,
            messages=messages,
            result=result,
            step_payloads=step_payloads,
            coord_space_width=coord_space_width,
            coord_space_height=coord_space_height,
        )
        await asyncio.to_thread(path.write_text, html, encoding="utf-8")


def generate_visualization_html(
    task_id: str,
    messages: list[dict],
    result: object | None = None,
    step_payloads: list[dict[str, Any]] | None = None,
    coord_space_width: int = N1_COORDINATE_SCALE,
    coord_space_height: int = N1_COORDINATE_SCALE,
) -> str:
    """Generate a static HTML viewer for an optional message replay."""

    steps, system_prompt, user_query = _build_steps(
        messages,
        coord_space_width,
        coord_space_height,
        step_payloads=step_payloads,
    )
    result_score = getattr(result, "score", None) if result is not None else None
    result_json = _dump_result_json(result)

    html: list[str] = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        "<meta charset=\"UTF-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">",
        f"<title>Trajectory Replay: {_escape_html(task_id)}</title>",
        "<style>",
        _STYLES,
        "</style>",
        "</head>",
        "<body>",
        "<div class=\"page\">",
        "<header class=\"hero\">",
        "<div>",
        "<div class=\"eyebrow\">Trajectory Replay</div>",
        f"<h1>{_escape_html(task_id)}</h1>",
        "</div>",
    ]

    if result_score is not None:
        badge_class = "partial"
        if result_score == 1:
            badge_class = "success"
        elif result_score == 0:
            badge_class = "failure"
        html.append(f"<div class=\"score {badge_class}\">score {result_score}</div>")
    html.extend(["</header>", "<main>"])

    if system_prompt:
        html.extend(
            [
                "<details class=\"panel\">",
                "<summary>System Prompt</summary>",
                f"<pre>{_escape_html(system_prompt)}</pre>",
                "</details>",
            ]
        )

    if user_query:
        html.extend(
            [
                "<details class=\"panel\" open>",
                "<summary>User Prompt</summary>",
                f"<pre>{_escape_html(user_query)}</pre>",
                "</details>",
            ]
        )

    if not steps:
        html.append("<section class=\"panel empty\">No assistant steps were recorded.</section>")

    for step in steps:
        html.append(_render_step(step))

    if result_json:
        html.extend(
            [
                "<details class=\"panel\">",
                "<summary>Result Artifact</summary>",
                f"<pre>{_escape_html(result_json)}</pre>",
                "</details>",
            ]
        )

    html.extend(
        [
            "</main>",
            "<div class=\"modal\" id=\"modal\" onclick=\"closeReplayModal(event)\">",
            "<button class=\"modal-close\" onclick=\"closeReplayModal(event)\">x</button>",
            "<div class=\"modal-inner\" id=\"modal-inner\"></div>",
            "</div>",
            "<script>",
            _SCRIPT,
            "</script>",
            "</div>",
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(html)


def _build_steps(
    messages: list[dict],
    coord_space_width: int,
    coord_space_height: int,
    *,
    step_payloads: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    steps: list[dict[str, Any]] = []
    current_observation: list[dict] | None = None
    system_prompt: str | None = None
    user_query: str | None = None

    for message_index, message in enumerate(messages):
        role = message.get("role")
        content = message.get("content")

        if role == "system":
            system_prompt = _stringify_content(content)
            continue

        if role in {"user", "tool", "observation"}:
            if role == "user" and user_query is None:
                user_query = extract_text_content(content) or _stringify_content(content)
            observation = _normalize_observation(content)
            if observation:
                current_observation = observation
            continue

        if role != "assistant":
            continue

        actions = _parse_tool_calls(message)
        action_markers = [_get_action_marker_style(action, coord_space_width, coord_space_height) for action in actions]
        screenshot_url, _ = _extract_observation_parts(current_observation)
        assistant_text = extract_text_content(content) or _stringify_content(content)
        step_num = len(steps) + 1
        request_payload, response_payload = _resolve_step_payloads(
            messages,
            message_index,
            message,
            step_num=step_num,
            step_payloads=step_payloads,
        )

        steps.append(
            {
                "step_num": step_num,
                "screenshot_url": screenshot_url,
                "assistant_response": assistant_text or "",
                "raw_request": request_payload,
                "raw_response": response_payload,
                "actions": actions,
                "action_markers": action_markers,
                "is_final_answer": len(actions) == 0 and bool((assistant_text or "").strip()),
            }
        )
        current_observation = None

    return steps, system_prompt, user_query


def _normalize_observation(content: Any) -> list[dict] | None:
    if content is None:
        return None
    if isinstance(content, list):
        normalized: list[dict] = []
        for block in content:
            if isinstance(block, dict):
                normalized.append(block)
        return normalized or None
    if isinstance(content, dict):
        return [content]
    if isinstance(content, str):
        stripped = content.strip()
        if stripped:
            return [{"type": "text", "text": stripped}]
    return None


def _extract_observation_parts(observation: list[dict] | None) -> tuple[str | None, list[str]]:
    screenshot_url: str | None = None
    text_observations: list[str] = []
    if not observation:
        return screenshot_url, text_observations

    for block in observation:
        block_type = block.get("type")
        if block_type == "image_url":
            screenshot_url = block.get("image_url", {}).get("url") or screenshot_url
        elif block_type == "text":
            text = block.get("text", "").strip()
            if text:
                text_observations.append(text)
        elif block_type == "tool_result":
            nested = block.get("content")
            if isinstance(nested, list):
                nested_url, nested_texts = _extract_tool_result_parts(nested)
                screenshot_url = nested_url or screenshot_url
                text_observations.extend(nested_texts)
    return screenshot_url, text_observations


def _extract_tool_result_parts(content: list[Any]) -> tuple[str | None, list[str]]:
    screenshot_url: str | None = None
    texts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "image_url":
            screenshot_url = item.get("image_url", {}).get("url") or screenshot_url
        elif item_type == "image":
            source = item.get("source", {})
            if source.get("type") == "base64":
                media_type = source.get("media_type", "image/png")
                screenshot_url = f"data:{media_type};base64,{source.get('data', '')}"
        elif item_type == "text":
            text = item.get("text", "").strip()
            if text:
                texts.append(text)
    return screenshot_url, texts


def _resolve_step_payloads(
    messages: list[dict],
    message_index: int,
    assistant_message: dict[str, Any],
    *,
    step_num: int,
    step_payloads: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _find_step_payload(step_payloads, step_num)
    if payload is not None:
        request_payload = payload.get("request")
        response_payload = payload.get("response")
        if isinstance(request_payload, dict) and isinstance(response_payload, dict):
            return _sanitize_for_replay(request_payload), _sanitize_for_replay(response_payload)

    return (
        {"messages": _sanitize_for_replay(messages[:message_index])},
        _sanitize_for_replay(assistant_message),
    )


def _parse_tool_calls(message: dict) -> list[dict[str, Any]]:
    tool_calls = message.get("tool_calls") or []
    actions: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        function = tool_call.get("function", {})
        arguments = function.get("arguments") or "{}"
        try:
            parsed_arguments = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
        except (TypeError, json.JSONDecodeError):
            parsed_arguments = {}
        action = {"action_type": function.get("name", "unknown")}
        action.update(parsed_arguments)
        actions.append(action)
    return actions


def _get_action_marker_style(
    action: dict[str, Any],
    coord_space_width: int,
    coord_space_height: int,
) -> dict[str, Any]:
    action_type = str(action.get("action_type", "unknown"))
    marker: dict[str, Any] = {"type": action_type}

    if "ref" in action:
        marker["ref"] = action["ref"]

    if "start_coordinates" in action and action_type.lower() in {"drag", "left_click_drag"}:
        start = action["start_coordinates"]
        end = action.get("coordinates", action.get("end_coordinates", action.get("center_coordinates", [0, 0])))
        marker.update(
            {
                "has_drag": True,
                "start_x": start[0] / coord_space_width * 100,
                "start_y": start[1] / coord_space_height * 100,
                "end_x": end[0] / coord_space_width * 100,
                "end_y": end[1] / coord_space_height * 100,
            }
        )
        return marker

    if "coordinates" in action:
        x, y = action["coordinates"]
        marker.update({"has_point": True, "x": x / coord_space_width * 100, "y": y / coord_space_height * 100})
        return marker

    if "center_coordinates" in action:
        x, y = action["center_coordinates"]
        marker.update({"has_point": True, "x": x / coord_space_width * 100, "y": y / coord_space_height * 100})
        return marker

    marker["has_point"] = False
    marker["has_ref_only"] = "ref" in action
    return marker


def _render_step(step: dict[str, Any]) -> str:
    markers_html = _render_markers(step["step_num"], step["action_markers"])
    image_html = (
        (
            f"<div class=\"image-frame\" data-modal-source=\"step-{step['step_num']}\" "
            f"onclick=\"openReplayModal('step-{step['step_num']}')\">"
            f"<img src=\"{step['screenshot_url']}\" alt=\"Step {step['step_num']} screenshot\">"
            f"{markers_html}</div>"
        )
        if step["screenshot_url"]
        else "<div class=\"empty-shot\">No screenshot recorded for this step.</div>"
    )

    action_items: list[str] = []
    if step["is_final_answer"]:
        answer = step["assistant_response"].strip()
        action_items.append(
            "<div class=\"action-card final\">"
            "<div class=\"action-name\">Final Answer</div>"
            f"<pre>{_escape_html(answer)}</pre>"
            "</div>"
        )
    elif step["actions"]:
        for index, action in enumerate(step["actions"], start=1):
            details = _format_action_details(action)
            action_items.append(
                "<div class=\"action-card\">"
                f"<div class=\"action-name\">{index}. {_escape_html(str(action.get('action_type', 'unknown')))}</div>"
                f"<div class=\"action-details\">{_escape_html(details or 'No additional arguments')}</div>"
                "</div>"
            )
    else:
        action_items.append("<div class=\"action-card empty\">No tool calls in this step.</div>")

    raw_request_html = _render_json_panel("Raw Request", step["raw_request"])
    raw_response_html = _render_json_panel("Raw Response", step["raw_response"])

    return (
        f"<section class=\"step\" id=\"step-{step['step_num']}\">"
        "<div class=\"step-header\">"
        f"<div class=\"step-badge\">{step['step_num']}</div>"
        f"<h2>Step {step['step_num']}</h2>"
        "</div>"
        "<div class=\"step-grid\">"
        f"<div class=\"media-panel\">{image_html}</div>"
        "<div class=\"side-panel\">"
        "<div class=\"panel nested\">"
        "<h3>Actions</h3>"
        f"<div class=\"action-list\">{''.join(action_items)}</div>"
        "</div>"
        f"{raw_request_html}"
        f"{raw_response_html}"
        "</div>"
        "</div>"
        "</section>"
    )


def _render_json_panel(title: str, payload: Any) -> str:
    json_text = _dump_json(payload) or "{}"
    return (
        "<div class=\"panel nested\">"
        f"<h3>{_escape_html(title)}</h3>"
        f"<pre class=\"json-block\">{_escape_html(json_text)}</pre>"
        "</div>"
    )


def _render_markers(step_num: int, markers: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    ref_only: list[tuple[int, dict[str, Any]]] = []

    for index, marker in enumerate(markers, start=1):
        if marker.get("has_point"):
            color_class = _ACTION_COLOR_CLASS.get(str(marker["type"]).lower(), "click")
            parts.append(
                "<div class=\"marker\" "
                f"style=\"left:{marker['x']:.3f}%;top:{marker['y']:.3f}%\">"
                f"<div class=\"marker-dot {color_class}\"></div>"
                f"<div class=\"marker-label\">{index}. {_escape_html(str(marker['type']))}</div>"
                "</div>"
            )
        elif marker.get("has_drag"):
            parts.append(
                "<svg class=\"drag\" viewBox=\"0 0 100 100\" preserveAspectRatio=\"none\">"
                "<defs>"
                f"<marker id=\"drag-arrow-{step_num}-{index}\" markerWidth=\"8\" "
                "markerHeight=\"8\" refX=\"7\" refY=\"4\" orient=\"auto\">"
                "<polygon points=\"0 0, 8 4, 0 8\" fill=\"#f59e0b\"></polygon>"
                "</marker>"
                "</defs>"
                f"<line x1=\"{marker['start_x']:.3f}\" y1=\"{marker['start_y']:.3f}\" "
                f"x2=\"{marker['end_x']:.3f}\" y2=\"{marker['end_y']:.3f}\" "
                f"marker-end=\"url(#drag-arrow-{step_num}-{index})\"></line>"
                "</svg>"
            )
            parts.append(
                "<div class=\"marker\" "
                f"style=\"left:{marker['start_x']:.3f}%;top:{marker['start_y']:.3f}%\">"
                "<div class=\"marker-dot drag-start\"></div>"
                f"<div class=\"marker-label\">{index}. drag</div>"
                "</div>"
            )
        elif marker.get("has_ref_only") and marker.get("ref"):
            ref_only.append((index, marker))

    if ref_only:
        badge_items = "".join(
            (
                "<div class=\"ref-line\">"
                f"<span>{index}. {_escape_html(str(marker['type']))}</span>"
                f"<code>{_escape_html(str(marker['ref']))}</code>"
                "</div>"
            )
            for index, marker in ref_only
        )
        parts.append(f"<div class=\"ref-badge\">{badge_items}</div>")

    return "".join(parts)


def _format_action_details(action: dict[str, Any]) -> str:
    seen: set[str] = {"action_type"}
    details: list[str] = []

    for key in _PREFERRED_ACTION_KEYS:
        if key in action:
            details.append(f"{key}={_format_value(action[key])}")
            seen.add(key)

    for key in sorted(action.keys()):
        if key in seen:
            continue
        details.append(f"{key}={_format_value(action[key])}")

    return ", ".join(details)


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def sanitize_step_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Clip image-heavy request/response payloads before optional replay storage."""

    sanitized = dict(payload)
    if "request" in sanitized:
        sanitized["request"] = _sanitize_for_replay(sanitized["request"])
    if "response" in sanitized:
        sanitized["response"] = _sanitize_for_replay(sanitized["response"])
    return sanitized


def _find_step_payload(step_payloads: list[dict[str, Any]] | None, step_num: int) -> dict[str, Any] | None:
    if not step_payloads:
        return None
    for payload in step_payloads:
        if payload.get("step_num") == step_num:
            return payload
    if len(step_payloads) >= step_num:
        return step_payloads[step_num - 1]
    return None


def _sanitize_for_replay(value: Any) -> Any:
    if isinstance(value, list):
        return [_sanitize_for_replay(item) for item in value]
    if isinstance(value, dict):
        sanitized = {key: _sanitize_for_replay(item) for key, item in value.items()}
        if (
            "image_url" in sanitized
            and isinstance(sanitized["image_url"], dict)
            and isinstance(sanitized["image_url"].get("url"), str)
        ):
            sanitized["image_url"] = dict(sanitized["image_url"])
            sanitized["image_url"]["url"] = _clip_image_url(sanitized["image_url"]["url"])
        if "source" in sanitized and isinstance(sanitized["source"], dict):
            source = dict(sanitized["source"])
            if source.get("type") == "base64" and isinstance(source.get("data"), str):
                source["data"] = _clip_image_url(source["data"])
            sanitized["source"] = source
        return sanitized
    return value


def _clip_image_url(value: str, *, max_len: int = 96) -> str:
    if value.startswith("data:image"):
        prefix_end = value.find(",") + 1
        if prefix_end > 0 and len(value) > prefix_end + max_len:
            return value[: prefix_end + 24] + "...[clipped]"
    if len(value) <= max_len:
        return value
    return value[:max_len] + "...[clipped]"


def _stringify_content(content: Any) -> str | None:
    text = extract_text_content(content)
    if text:
        return text
    if content is None:
        return None
    if isinstance(content, str):
        stripped = content.strip()
        return stripped or None
    try:
        return json.dumps(content, ensure_ascii=False, indent=2, default=_json_default)
    except TypeError:
        return str(content)


def _dump_result_json(result: object | None) -> str | None:
    if result is None:
        return None
    if hasattr(result, "model_dump"):
        payload = result.model_dump(mode="json", exclude_none=True)
    elif isinstance(result, dict):
        payload = result
    else:
        payload = result
    try:
        return json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default)
    except TypeError:
        return str(result)


def _dump_json(payload: Any) -> str | None:
    try:
        return json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default)
    except TypeError:
        return str(payload)


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def _slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-_.").lower()


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_STYLES = """
:root {
  --bg: #0b1220;
  --panel: #121a2a;
  --panel-alt: #172234;
  --border: #2a3b57;
  --text: #e8eef8;
  --muted: #9fb2cc;
  --accent: #7dd3fc;
  --success: #34d399;
  --warning: #f59e0b;
  --danger: #fb7185;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background:
    radial-gradient(circle at top right, rgba(125, 211, 252, 0.12), transparent 28%),
    radial-gradient(circle at top left, rgba(52, 211, 153, 0.08), transparent 22%),
    var(--bg);
  color: var(--text);
  font-family: "SF Mono", "JetBrains Mono", "Fira Code", monospace;
}

.page {
  max-width: 1480px;
  margin: 0 auto;
  padding: 28px 20px 72px;
}

.hero {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  margin-bottom: 20px;
}

.eyebrow {
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-size: 12px;
  margin-bottom: 8px;
}

h1, h2, h3, summary {
  margin: 0;
}

h1 {
  font-size: 28px;
}

.score {
  padding: 10px 14px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.03);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 12px;
}

.score.success {
  color: var(--success);
  border-color: rgba(52, 211, 153, 0.5);
}

.score.failure {
  color: var(--danger);
  border-color: rgba(251, 113, 133, 0.5);
}

.score.partial {
  color: var(--warning);
  border-color: rgba(245, 158, 11, 0.5);
}

main {
  display: grid;
  gap: 18px;
}

.panel {
  background: rgba(18, 26, 42, 0.92);
  border: 1px solid var(--border);
  border-radius: 16px;
  overflow: hidden;
}

.panel > summary,
.panel > h3 {
  padding: 14px 16px;
  background: rgba(255, 255, 255, 0.02);
  cursor: pointer;
  border-bottom: 1px solid rgba(42, 59, 87, 0.8);
}

.panel > h3 {
  cursor: default;
}

.panel > pre {
  margin: 0;
  padding: 16px;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--muted);
}

.json-block {
  max-height: 420px;
  overflow: auto;
}

.panel.empty {
  padding: 24px;
  color: var(--muted);
}

.panel.nested {
  border-radius: 14px;
}

.step {
  background: rgba(18, 26, 42, 0.92);
  border: 1px solid var(--border);
  border-radius: 18px;
  overflow: hidden;
}

.step-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 18px;
  background: linear-gradient(135deg, rgba(125, 211, 252, 0.08), rgba(255, 255, 255, 0.02));
  border-bottom: 1px solid rgba(42, 59, 87, 0.8);
}

.step-badge {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--accent);
  color: #04111d;
  display: grid;
  place-items: center;
  font-weight: 700;
}

.step-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.8fr) minmax(340px, 1fr);
  gap: 18px;
  padding: 18px;
}

.media-panel,
.side-panel {
  min-width: 0;
}

.side-panel {
  display: grid;
  gap: 14px;
}

.image-frame {
  position: relative;
  display: inline-block;
  max-width: 100%;
  border-radius: 14px;
  overflow: hidden;
  cursor: zoom-in;
  line-height: 0;
  background: #0f172a;
}

.image-frame img {
  display: block;
  max-width: 100%;
  height: auto;
}

.empty-shot {
  min-height: 240px;
  display: grid;
  place-items: center;
  border: 1px dashed var(--border);
  border-radius: 14px;
  color: var(--muted);
}

.marker {
  position: absolute;
  transform: translate(-50%, -50%);
  z-index: 3;
}

.marker-dot {
  width: 24px;
  height: 24px;
  border-radius: 999px;
  border: 3px solid white;
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
}

.marker-dot.click { background: #fb7185; }
.marker-dot.scroll { background: #7dd3fc; }
.marker-dot.type { background: #34d399; }
.marker-dot.hover { background: #c084fc; }
.marker-dot.drag-start { background: #f59e0b; }

.marker-label {
  margin-top: 6px;
  padding: 5px 8px;
  border-radius: 999px;
  background: rgba(4, 17, 29, 0.84);
  color: white;
  font-size: 11px;
  white-space: nowrap;
}

.drag {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: 2;
  pointer-events: none;
}

.drag line {
  stroke: #f59e0b;
  stroke-width: 0.45;
}

.ref-badge {
  position: absolute;
  top: 10px;
  right: 10px;
  max-width: min(46%, 360px);
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(4, 17, 29, 0.84);
  border: 1px solid rgba(125, 211, 252, 0.35);
  z-index: 4;
  display: grid;
  gap: 6px;
}

.ref-line {
  display: grid;
  gap: 2px;
  color: var(--muted);
  font-size: 11px;
}

.ref-line code {
  color: var(--text);
  font-family: inherit;
}

.action-list {
  display: grid;
  gap: 10px;
  padding: 16px;
}

.action-card {
  border: 1px solid rgba(42, 59, 87, 0.8);
  border-radius: 12px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.02);
}

.action-card.empty {
  color: var(--muted);
}

.action-card.final {
  border-color: rgba(52, 211, 153, 0.4);
}

.action-card.final pre {
  margin: 10px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.action-name {
  font-weight: 700;
  margin-bottom: 6px;
}

.action-details {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}

.modal {
  position: fixed;
  inset: 0;
  display: none;
  place-items: center;
  background: rgba(3, 8, 18, 0.88);
  padding: 28px;
  z-index: 50;
}

.modal.open {
  display: grid;
}

.modal-inner {
  max-width: 94vw;
  max-height: 92vh;
}

.modal-inner .image-frame {
  max-height: 92vh;
}

.modal-close {
  position: fixed;
  top: 16px;
  right: 16px;
  width: 42px;
  height: 42px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: rgba(18, 26, 42, 0.95);
  color: var(--text);
  cursor: pointer;
}

@media (max-width: 1100px) {
  .step-grid {
    grid-template-columns: 1fr;
  }
}
""".strip()

_SCRIPT = """
function openReplayModal(sourceId) {
  const source = document.querySelector(`[data-modal-source="${sourceId}"]`);
  if (!source) return;
  const modal = document.getElementById("modal");
  const modalInner = document.getElementById("modal-inner");
  modalInner.innerHTML = source.outerHTML;
  modal.classList.add("open");
  document.body.style.overflow = "hidden";
}

function closeReplayModal(event) {
  const modal = document.getElementById("modal");
  if (event && event.target && event.target.closest(".image-frame")) {
    return;
  }
  modal.classList.remove("open");
  document.body.style.overflow = "";
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeReplayModal();
  }
});
""".strip()
