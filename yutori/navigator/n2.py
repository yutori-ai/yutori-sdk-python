"""The Navigator n2 computer-use agent loop.

This SDK-owned loop lets computer-use hosts drive n2 without another agent
framework or a private dependency. Its behavior follows the public n2
contract, with these deliberate implementation choices:

- The model call goes through the SDK's own chat-completions surface (or any
  object with a compatible async ``create``); the SDK chat namespace's bundled
  client already retries transient failures, so the loop adds no second retry
  layer.
- ``usage`` on each yielded step is the raw Chat Completions usage dict.
- No telemetry, cost accounting, or trajectory persistence.
- ``instructions`` becomes the first user message of the run's history — the
  same wire effect as the reference's prompt-instructions callback.
- ``on_run_end`` always fires, including when the first ``on_run_continue``
  stops the run (the reference raised there).

The trajectory is a list of "responses items" dicts — ``message``,
``reasoning``, ``function_call``, ``function_call_output`` — converted to Chat
Completions messages per request. Executor-only fields ride on function_call
items under underscore keys and never reach the wire.

Callbacks are duck-typed and all optional: ``on_run_start``,
``on_run_continue`` (return False to stop), ``on_api_start``, ``on_api_end``,
``on_usage``, ``on_screenshot``, ``on_computer_call_start``,
``on_computer_call_end``, ``on_text``, ``on_run_end``.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import re
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, Protocol, Union

from .macos.sanitize import sanitize_command_preview
from .macos.types import N2Observation, N2Presentation
from .models import NAVIGATOR_N2_MODEL, TOOL_SET_COMPUTER_USE_LATEST
from .n2_actions import (
    BASH_TOOL_NAME,
    FILE_TOOL_NAMES,
    SAFE_WITHOUT_CONFIRMATION,
    SHELL_COMMAND_TOOL_NAMES,
    SUPPORTED_N2_TOOL_SETS,
    TOOL_SETS_WITH_BASH,
    TOOL_SETS_WITH_BATCH,
    TOOL_SETS_WITH_CLICK_MODIFIERS,
    TOOL_SETS_WITH_FILE_TOOLS,
    TOOL_SETS_WITH_SHELL_COMMAND,
    TOOL_SETS_WITH_STANDALONE_SCREENSHOT,
    N2ActionValidationError,
    translate_n2_action,
    translate_n2_bash,
    translate_n2_batch,
    translate_n2_edit,
    translate_n2_read,
    translate_n2_shell_command,
    translate_n2_write,
)
from .n2_payload import (
    DEFAULT_MAX_MESSAGES_BYTES,
    MAX_REQUEST_BODY_BYTES,
    convert_request_images,
    fit_n2_request_images_to_budget,
    image_dimensions,
    latest_image_url,
    retain_n2_image_window,
    serialized_messages_bytes,
)

# Shell results follow the n2 server contract: bounded output with an explicit
# truncation marker, and a recoverable tool error when the handler lacks the
# optional shell capability.
SHELL_RESULT_MAX_CHARS = 8_000
SHELL_RESULT_TRUNCATION_SUFFIX = "\n[result truncated]"

# The two shell tools the n2 tool sets serve, each mapped to the optional
# handler method that runs it. They are separate rather than one normalized
# action because their contracts genuinely differ: ``shell_command`` takes a
# per-call ``cwd`` and caps at 30s, while ``bash`` has a 600s ceiling, a
# detached mode, and a working directory that persists across calls.
SHELL_ACTION_HANDLERS = {
    "run_shell_command": "run_shell_command",
    "run_bash_command": "run_bash_command",
}
FILE_ACTION_HANDLERS = {
    "read_file": "read_file",
    "write_file": "write_file",
    "edit_file": "edit_file",
}

ConfirmationCallback = Callable[[dict], Union[bool, Awaitable[bool]]]

N2_MAX_COMPLETION_TOKENS = 16_384
_FINAL_MARKER = re.compile(r"\s*\[(?:DONE|INFEASIBLE)\]\s*", re.IGNORECASE)


class SupportsN2ChatCompletionsCreate(Protocol):
    """The chat-completions subset the n2 loop calls.

    ``yutori.AsyncYutoriClient().chat.completions`` satisfies this; so does any test
    double whose ``create`` returns an object with ``model_dump()`` (or a plain
    dict) in the Chat Completions response shape.
    """

    async def create(self, messages: Any, *, model: str = ..., **kwargs: Any) -> Any:
        """Create a chat completion."""


def _random_id() -> str:
    return str(uuid.uuid4())


def make_reasoning_item(reasoning: str) -> dict[str, Any]:
    return {
        "type": "reasoning",
        "id": _random_id(),
        "summary": [{"text": reasoning, "type": "summary_text"}],
    }


def make_output_text_item(content: str) -> dict[str, Any]:
    return {
        "type": "message",
        "id": _random_id(),
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": content, "annotations": []}],
    }


def _strip_final_markers(content: str) -> str:
    return _FINAL_MARKER.sub(" ", content).strip()


def _observation_data(observation: Any) -> tuple[str, int, int, str]:
    """Normalize the new observation object and legacy raw-base64 screenshot handlers."""
    if isinstance(observation, N2Observation):
        return observation.data_url, observation.native_width, observation.native_height, observation.base64
    if not isinstance(observation, str) or not observation:
        raise RuntimeError("Failed to capture screenshot from the computer handler.")
    data_url = observation if observation.startswith("data:") else f"data:image/png;base64,{observation}"
    width, height = image_dimensions(data_url)
    raw_base64 = data_url.split(",", 1)[1]
    return data_url, width, height, raw_base64


def make_function_call_item(
    function_name: str, arguments: dict[str, Any], call_id: "str | None" = None
) -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": _random_id(),
        "call_id": call_id if call_id else _random_id(),
        "name": function_name,
        "arguments": json.dumps(arguments),
        "status": "completed",
    }


def convert_n2_items_to_completion_messages(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert the n2 trajectory's responses items to Chat Completions messages.

    The n2 subset of the reference converter: user messages (string or parts),
    assistant messages, reasoning summaries (sent as assistant text so the
    server's ``preserve_thinking`` has something to preserve), function calls
    folded into the preceding assistant message's ``tool_calls``, and tool
    results — a plain string, or an image dict whose optional ``result`` rides
    as a text part before the screenshot.
    """
    completion_messages: list[dict[str, Any]] = []

    for item in items:
        item_type = item.get("type")
        role = item.get("role")

        if role == "user" or item_type == "user":
            content = item.get("content", "")
            if isinstance(content, list):
                parts = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") in {"input_image", "image_url"}:
                        url = part.get("image_url")
                        if isinstance(url, dict):
                            url = url.get("url")
                        parts.append({"type": "image_url", "image_url": {"url": url}})
                    elif part.get("type") in {"input_text", "text"}:
                        parts.append({"type": "text", "text": part.get("text")})
                completion_messages.append({"role": "user", "content": parts})
            elif isinstance(content, str):
                completion_messages.append({"role": "user", "content": content})

        elif role == "assistant" or item_type == "message":
            content = item.get("content", [])
            if isinstance(content, str):
                # A caller-provided chat-style history carries assistant turns
                # as plain strings; dropping them would silently erase context.
                if content:
                    completion_messages.append({"role": "assistant", "content": content})
                continue
            texts = [
                part.get("text", "")
                for part in content or []
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}
            ]
            if texts:
                completion_messages.append({"role": "assistant", "content": "\n".join(texts)})

        elif item_type == "reasoning":
            texts = [
                part.get("text", "")
                for part in item.get("summary", []) or []
                if isinstance(part, dict) and part.get("type") == "summary_text"
            ]
            if texts:
                completion_messages.append({"role": "assistant", "content": "\n".join(texts)})

        elif item_type == "function_call":
            if not completion_messages or completion_messages[-1].get("role") != "assistant":
                completion_messages.append({"role": "assistant", "content": "", "tool_calls": []})
            completion_messages[-1].setdefault("tool_calls", [])
            arguments = item.get("arguments")
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments)
            completion_messages[-1]["tool_calls"].append(
                {
                    "id": item.get("call_id"),
                    "type": "function",
                    "function": {"name": item.get("name"), "arguments": arguments},
                }
            )

        elif item_type == "function_call_output":
            output = item.get("output")
            call_id = item.get("call_id")
            if isinstance(output, dict) and output.get("type") == "input_image":
                content = []
                result_metadata = output.get("result")
                if result_metadata is not None:
                    # String results (e.g. shell command output) pass through
                    # verbatim; structured metadata is JSON.
                    content.append(
                        {
                            "type": "text",
                            "text": (
                                result_metadata
                                if isinstance(result_metadata, str)
                                else json.dumps(result_metadata, separators=(",", ":"), ensure_ascii=False)
                            ),
                        }
                    )
                content.append({"type": "image_url", "image_url": {"url": output.get("image_url")}})
                completion_messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
            else:
                completion_messages.append({"role": "tool", "tool_call_id": call_id, "content": str(output)})

    return completion_messages


def _shell_tool_name(action_type: str) -> str:
    return "bash" if action_type == "run_bash_command" else "shell_command"


def _shell_not_supported_error(action_type: str) -> str:
    return f"{_shell_tool_name(action_type)} is not supported by this computer environment."


def _file_tool_name(action_type: str) -> str:
    return action_type.removesuffix("_file")


def _file_not_supported_error(action_type: str) -> str:
    return f"{_file_tool_name(action_type)} is not supported by this computer environment."


def truncate_shell_result(output: str) -> str:
    """Bound shell output to the n2 wire contract's 8,000-character cap."""
    if len(output) <= SHELL_RESULT_MAX_CHARS:
        return output
    return f"{output[:SHELL_RESULT_MAX_CHARS]}{SHELL_RESULT_TRUNCATION_SUFFIX}"


def _function_call_with_execution(
    name: str,
    args: dict[str, Any],
    call_id: str,
    actions: list[dict[str, Any]],
    *,
    batch_actions: "list[dict[str, Any]] | None" = None,
    execution_deadline: "float | None" = None,
) -> dict[str, Any]:
    item = make_function_call_item(name, args, call_id=call_id)
    item["_computer_actions"] = actions
    # Confirmation callbacks always see Yutori-shaped entries: one
    # {"action": name, **arguments} per member for batches and singles alike.
    item["_confirmation_actions"] = batch_actions if batch_actions is not None else [{"action": name, **args}]
    item["_requires_confirmation"] = (
        any(action.get("action") not in SAFE_WITHOUT_CONFIRMATION for action in batch_actions)
        if batch_actions is not None
        else name not in SAFE_WITHOUT_CONFIRMATION
    )
    if batch_actions is not None:
        item["_batch_actions"] = batch_actions
    if execution_deadline is not None:
        item["_execution_deadline"] = execution_deadline
    return item


def parse_n2_tool_calls(
    message: dict[str, Any],
    native_width: int,
    native_height: int,
    *,
    tool_set: str = TOOL_SET_COMPUTER_USE_LATEST,
    execution_deadline: "float | None" = None,
    allow_click_modifiers: bool = False,
) -> list[dict[str, Any]]:
    """Turn one model message into trajectory items with attached executions.

    Order matches the reference loop: an optional reasoning item, the message
    text when tool calls accompany it, then one function_call item per tool
    call — followed immediately by a recoverable ``[ERROR]`` result when the
    call fails validation, so history stays consistent and the model can
    correct itself. A turn with no tool calls yields a terminal assistant
    message ("Task completed." when the model sent nothing at all).
    """
    if tool_set not in SUPPORTED_N2_TOOL_SETS:
        raise ValueError(f"Unsupported n2 tool_set: {tool_set}")

    content_text = message.get("content") or ""
    reasoning_text = message.get("reasoning_content") or message.get("reasoning") or ""
    tool_calls = message.get("tool_calls") or []
    output: list[dict[str, Any]] = []

    if reasoning_text:
        output.append(make_reasoning_item(reasoning_text))
    if tool_calls and content_text:
        output.append(make_output_text_item(content_text))

    # The model may return parallel calls despite the desktop freshness contract.
    # Only the first consumes this turn; a computer_batch is the sole way to run
    # multiple actions from one observed frame.
    for tool_call in tool_calls[:1]:
        function = tool_call.get("function") or {}
        name = function.get("name") or ""
        arguments = function.get("arguments", "{}")
        call_id = tool_call.get("id") or "call_0"
        call_item: dict[str, Any] = {
            "type": "function_call",
            "id": tool_call.get("id") or call_id,
            "call_id": call_id,
            "name": name,
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
            "status": "completed",
        }
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
            if not isinstance(args, dict):
                raise N2ActionValidationError(f"{name} arguments must be an object")
            if name == "computer_batch":
                if tool_set not in TOOL_SETS_WITH_BATCH:
                    raise N2ActionValidationError(f"{tool_set} does not expose computer_batch")
                batch_actions, translated = translate_n2_batch(
                    args,
                    native_width,
                    native_height,
                    tool_set=tool_set,
                    allow_click_modifiers=allow_click_modifiers,
                )
                call_item = _function_call_with_execution(
                    name,
                    args,
                    call_id,
                    translated,
                    batch_actions=batch_actions,
                    execution_deadline=execution_deadline,
                )
            elif name in SHELL_COMMAND_TOOL_NAMES:
                if tool_set not in TOOL_SETS_WITH_SHELL_COMMAND:
                    raise N2ActionValidationError(f"{tool_set} does not expose shell_command")
                translated = translate_n2_shell_command(args)
                call_item = _function_call_with_execution(
                    name, args, call_id, translated, execution_deadline=execution_deadline
                )
            elif name == BASH_TOOL_NAME:
                if tool_set not in TOOL_SETS_WITH_BASH:
                    raise N2ActionValidationError(f"{tool_set} does not expose bash")
                translated = translate_n2_bash(args)
                call_item = _function_call_with_execution(
                    name, args, call_id, translated, execution_deadline=execution_deadline
                )
            elif name in FILE_TOOL_NAMES:
                if tool_set not in TOOL_SETS_WITH_FILE_TOOLS:
                    raise N2ActionValidationError(f"{tool_set} does not expose {name}")
                translators = {
                    "read": translate_n2_read,
                    "write": translate_n2_write,
                    "edit": translate_n2_edit,
                }
                translated = translators[name](args)
                call_item = _function_call_with_execution(
                    name, args, call_id, translated, execution_deadline=execution_deadline
                )
            elif name == "screenshot" and tool_set not in TOOL_SETS_WITH_STANDALONE_SCREENSHOT:
                raise N2ActionValidationError(f"{tool_set} does not expose screenshot")
            elif tool_set == TOOL_SET_COMPUTER_USE_LATEST:
                raise N2ActionValidationError(f"{tool_set} does not expose {name}")
            else:
                translated = translate_n2_action(
                    name,
                    args,
                    native_width,
                    native_height,
                    allow_click_modifiers=allow_click_modifiers,
                )
                call_item = _function_call_with_execution(
                    name, args, call_id, translated, execution_deadline=execution_deadline
                )
            output.append(call_item)
        except (json.JSONDecodeError, N2ActionValidationError, TypeError, ValueError) as error:
            output.append(call_item)
            output.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": f"[ERROR] Invalid {name} call: {error}",
                }
            )

    for index, tool_call in enumerate(tool_calls[1:], start=1):
        function = tool_call.get("function") or {}
        name = function.get("name") or "unknown"
        call_id = tool_call.get("id") or f"call_{index}"
        arguments = function.get("arguments", "{}")
        output.extend(
            [
                {
                    "type": "function_call",
                    "id": tool_call.get("id") or call_id,
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                    "status": "completed",
                },
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": (
                        "[ERROR] Refused stale parallel tool call. Only the first call from a desktop frame "
                        "may execute; use computer_batch for multiple actions."
                    ),
                },
            ]
        )

    if not tool_calls:
        output.append(make_output_text_item(_strip_final_markers(content_text) or "Task completed."))

    return output


class _CallbackDispatcher:
    """hasattr-guarded dispatch to a list of duck-typed callback objects."""

    def __init__(self, callbacks: "list[Any] | None"):
        self._callbacks = list(callbacks or [])

    async def fire(self, name: str, *args: Any) -> None:
        for callback in self._callbacks:
            method = getattr(callback, name, None)
            if method is not None:
                await method(*args)

    async def should_continue(self, kwargs: dict, old_items: list, new_items: list) -> bool:
        for callback in self._callbacks:
            method = getattr(callback, "on_run_continue", None)
            if method is not None and not await method(kwargs, old_items, new_items):
                return False
        return True


async def _present(presentation: "N2Presentation | None", event: dict[str, Any]) -> None:
    if presentation is None:
        return
    try:
        await presentation.present(event)
    except Exception:
        # Presentation is optional. A native controller records its own degradation;
        # a third-party sink is afforded the same fail-soft boundary.
        pass


async def _await_model_response(computer: Any, awaitable: Awaitable[Any]) -> Any:
    """Cancel an in-flight model request when the computer session is stopped."""
    cancellation = getattr(computer, "cancellation", None)
    if cancellation is None:
        return await awaitable
    if cancellation.cancelled:
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        cancellation.raise_if_cancelled()
    request = asyncio.create_task(awaitable)
    stopped = asyncio.create_task(cancellation.wait())
    try:
        done, _ = await asyncio.wait({request, stopped}, return_when=asyncio.FIRST_COMPLETED)
        if request in done:
            return request.result()
        raise asyncio.CancelledError(stopped.result())
    finally:
        for task in (request, stopped):
            if not task.done():
                task.cancel()
        await asyncio.gather(request, stopped, return_exceptions=True)


def _presentation_text(item: dict[str, Any], field: str) -> str:
    return "\n".join(
        str(part.get("text") or "") for part in item.get(field) or [] if isinstance(part, dict) and part.get("text")
    )


def _safe_presentation_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    safe = copy.deepcopy(arguments)
    if name in SHELL_COMMAND_TOOL_NAMES or name == BASH_TOOL_NAME:
        command = safe.get("command")
        if isinstance(command, str):
            safe["command"] = sanitize_command_preview(command)
    elif name == "type" and "text" in safe:
        safe["text"] = "[text]"
    return safe


async def _confirm_computer_item(item: dict[str, Any], confirmation_callback: "ConfirmationCallback | None") -> bool:
    """Run the opt-in confirmation hook for a fully validated action item."""
    if not item.get("_requires_confirmation") or confirmation_callback is None:
        return True
    request = {
        "call_id": item.get("call_id"),
        "tool_name": item.get("name"),
        "arguments": item.get("arguments"),
        # Every entry uses the Yutori wire shape {"action": name, **arguments}
        # for singles and batch members alike.
        "actions": item.get("_confirmation_actions")
        or item.get("_batch_actions")
        or item.get("_computer_actions")
        or [],
    }
    decision = confirmation_callback(request)
    if inspect.isawaitable(decision):
        decision = await decision
    return bool(decision)


async def execute_n2_computer_call(
    item: dict[str, Any],
    computer: Any,
    *,
    callbacks: _CallbackDispatcher,
    confirmation_callback: "ConfirmationCallback | None" = None,
    screenshot_delay: float = 0.5,
    presentation: "N2Presentation | None" = None,
) -> list[dict[str, Any]]:
    """Execute one validated Yutori call and capture exactly one result frame."""
    call_id = item.get("call_id")

    async def finish_with_error(message: str, observation: Any = None) -> list[dict[str, Any]]:
        output: Any = f"[ERROR] {message}"
        if observation is not None:
            try:
                data_url, _, _, raw_base64 = _observation_data(observation)
                output = {"type": "input_image", "image_url": data_url, "result": f"[ERROR] {message}"}
                await callbacks.fire("on_screenshot", raw_base64, "screenshot_after")
            except Exception:
                output = f"[ERROR] {message}"
        result = [
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": output,
            }
        ]
        await callbacks.fire("on_computer_call_end", item, result)
        await _present(presentation, {"type": "action_done", "call_id": call_id, "error": message})
        return result

    actions = item.get("_computer_actions") or []
    batch_actions = item.get("_batch_actions")
    reference_observation = getattr(computer, "current_observation", None)

    await callbacks.fire("on_computer_call_start", item)
    for action in actions:
        action_type = str(action.get("type"))
        handler_name = SHELL_ACTION_HANDLERS.get(action_type) or FILE_ACTION_HANDLERS.get(action_type)
        if handler_name is not None and not callable(getattr(computer, handler_name, None)):
            message = (
                _shell_not_supported_error(action_type)
                if action_type in SHELL_ACTION_HANDLERS
                else _file_not_supported_error(action_type)
            )
            return await finish_with_error(message)
    try:
        confirmed = await _confirm_computer_item(item, confirmation_callback)
    except Exception as error:  # noqa: BLE001 - a broken hook must not kill the run
        return await finish_with_error(f"Action confirmation failed: {error}")
    if not confirmed:
        result = [
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": "[ERROR] Action was not confirmed by the user.",
            }
        ]
        await callbacks.fire("on_computer_call_end", item, result)
        await _present(presentation, {"type": "action_done", "call_id": call_id, "refused": True})
        return result

    record_action = getattr(computer, "record_model_action", None)
    if callable(record_action):
        arguments = item.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        record_action(str(item.get("name") or ""), arguments if isinstance(arguments, dict) else {})

    batch_size = len(batch_actions) if isinstance(batch_actions, list) else 1
    action_counts: dict[int, int] = {}
    if isinstance(batch_actions, list):
        for action in actions:
            batch_index = action.get("batch_index")
            if isinstance(batch_index, int):
                action_counts[batch_index] = action_counts.get(batch_index, 0) + 1
    else:
        action_counts[0] = len(actions)

    completed_members: set[int] = set()
    failed_index: "int | None" = None
    stopped_reason: "str | None" = None
    screenshot_observation: Any = None
    shell_output_text: "str | None" = None
    file_output_text: "str | None" = None
    started_at = time.monotonic()
    presented_member: "int | None" = None

    batch_presentation = None
    if isinstance(batch_actions, list):
        members = [
            {
                "name": str(member.get("action") or ""),
                "arguments": _safe_presentation_arguments(
                    str(member.get("action") or ""),
                    {key: value for key, value in member.items() if key != "action"},
                ),
            }
            for member in batch_actions
        ]
        batch_presentation = {"id": str(call_id), "members": members}
    elif item.get("name") not in SHELL_COMMAND_TOOL_NAMES and item.get("name") != BASH_TOOL_NAME:
        arguments = item.get("arguments")
        try:
            parsed_arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            parsed_arguments = {}
        if isinstance(parsed_arguments, dict):
            await _present(
                presentation,
                {
                    "type": "action",
                    "call_id": call_id,
                    "name": item.get("name"),
                    "arguments": _safe_presentation_arguments(str(item.get("name") or ""), parsed_arguments),
                },
            )

    for action in actions:
        current_task = asyncio.current_task()
        # Task.cancelling() is 3.11+; on older interpreters plain cancellation
        # still propagates as CancelledError at the next await.
        cancelling = getattr(current_task, "cancelling", None)
        if cancelling is not None and cancelling():
            stopped_reason = "cancelled"
            break
        deadline = item.get("_execution_deadline")
        if isinstance(deadline, (int, float)) and time.monotonic() >= deadline:
            stopped_reason = "deadline_reached"
            break
        cancellation = getattr(computer, "cancellation", None)
        if cancellation is not None and cancellation.cancelled:
            raise asyncio.CancelledError(cancellation.cause)

        action_type = action.get("type")
        batch_index = action.get("batch_index")
        action_args = {key: value for key, value in action.items() if key not in {"type", "batch_index"}}
        try:
            if isinstance(batch_index, int) and batch_index != presented_member and batch_presentation is not None:
                member = batch_presentation["members"][batch_index]
                await _present(
                    presentation,
                    {
                        "type": "batch_member",
                        "call_id": call_id,
                        "name": member["name"],
                        "arguments": member["arguments"],
                        "batch": {**batch_presentation, "index": batch_index},
                    },
                )
                presented_member = batch_index
                cancellation = getattr(computer, "cancellation", None)
                if cancellation is not None and cancellation.cancelled:
                    raise asyncio.CancelledError(cancellation.cause)
            if action_type == "screenshot":
                if not isinstance(batch_actions, list):
                    screenshot_observation = await computer.screenshot()
            elif action_type in SHELL_ACTION_HANDLERS:
                shell_method = getattr(computer, SHELL_ACTION_HANDLERS[action_type], None)
                if shell_method is None:
                    raise RuntimeError(_shell_not_supported_error(str(action_type)))
                shell_result = await shell_method(**action_args)
                shell_output_text = truncate_shell_result("" if shell_result is None else str(shell_result))
            elif action_type in FILE_ACTION_HANDLERS:
                file_method = getattr(computer, FILE_ACTION_HANDLERS[action_type], None)
                if file_method is None:
                    raise RuntimeError(_file_not_supported_error(str(action_type)))
                file_result = await file_method(**action_args)
                file_output_text = truncate_shell_result("" if file_result is None else str(file_result))
            elif (
                action_type == "wait"
                and not isinstance(batch_actions, list)
                and isinstance(reference_observation, N2Observation)
                and callable(getattr(computer, "wait_for_change", None))
            ):
                screenshot_observation = await computer.wait_for_change(action_args.get("ms", 0), reference_observation)
            else:
                computer_method = getattr(computer, str(action_type), None)
                if action_type == "triple_click" and computer_method is None:
                    # Older handlers expose only double_click and click. Keep
                    # that compatibility fallback while allowing native
                    # desktop handlers to preserve the OS multi-click timing
                    # with one explicit triple_click primitive.
                    action_result = await computer.double_click(**action_args)
                    if not (
                        isinstance(action_result, dict)
                        and action_result.get("success") is False
                    ):
                        action_result = await computer.click(
                            **action_args, button="left"
                        )
                elif computer_method is None:
                    raise RuntimeError(f"Unknown computer action: {action_type}")
                else:
                    action_result = await computer_method(**action_args)
                if isinstance(action_result, dict) and action_result.get("success") is False:
                    raise RuntimeError(str(action_result.get("error") or action_result))

            member_index = batch_index if isinstance(batch_index, int) else 0
            action_counts[member_index] = action_counts.get(member_index, 1) - 1
            if action_counts[member_index] == 0:
                completed_members.add(member_index)
        except Exception as error:  # noqa: BLE001 - classified below
            if action_type in SHELL_ACTION_HANDLERS:
                # Handler failures (timeouts included) surface as recoverable
                # tool errors rather than crashing the loop, so the model can
                # retry or fall back to the GUI.
                return await finish_with_error(f"{_shell_tool_name(str(action_type))} failed: {error}")
            if action_type in FILE_ACTION_HANDLERS:
                return await finish_with_error(f"{_file_tool_name(str(action_type))} failed: {error}")
            if getattr(error, "recoverable", False):
                observation = getattr(error, "observation", None)
                if observation is None:
                    try:
                        observation = await computer.screenshot()
                    except Exception:
                        observation = None
                return await finish_with_error(str(error), observation)
            failed_index = batch_index if isinstance(batch_index, int) else 0
            stopped_reason = str(error)
            break

    if isinstance(batch_actions, list):
        release_held_mouse_button = getattr(computer, "release_held_mouse_button", None)
        if callable(release_held_mouse_button):
            try:
                await release_held_mouse_button()
            except Exception as error:  # noqa: BLE001 - report a driver cleanup failure to the model
                stopped_reason = stopped_reason or f"Failed to release held mouse button: {error}"

    if file_output_text is not None and not isinstance(batch_actions, list):
        result = [{"type": "function_call_output", "call_id": call_id, "output": file_output_text}]
        await callbacks.fire("on_computer_call_end", item, result)
        await _present(presentation, {"type": "action_done", "call_id": call_id})
        return result

    if screenshot_observation is None:
        try:
            if screenshot_delay and screenshot_delay > 0 and not isinstance(reference_observation, N2Observation):
                await asyncio.sleep(screenshot_delay)
            screenshot_observation = await computer.screenshot()
        except Exception as error:  # noqa: BLE001 - reported on the wire
            return await finish_with_error(f"Post-action screenshot failed: {error}")
    if (
        stopped_reason is None
        and isinstance(reference_observation, N2Observation)
        and isinstance(screenshot_observation, N2Observation)
        and callable(getattr(computer, "poll_after_action", None))
    ):
        poll_action_name = str(item.get("name") or "")
        if isinstance(batch_actions, list) and batch_actions:
            poll_action_name = str(batch_actions[-1].get("action") or "")
        screenshot_observation = await computer.poll_after_action(
            poll_action_name,
            reference_observation,
            screenshot_observation,
        )
    try:
        data_url, _, _, raw_base64 = _observation_data(screenshot_observation)
    except Exception as error:
        return await finish_with_error(f"Post-action screenshot was unusable: {error}")
    try:
        await callbacks.fire("on_screenshot", raw_base64, "screenshot_after")
    except Exception as error:  # noqa: BLE001 - callback failures are reported on the wire
        if stopped_reason is None:
            stopped_reason = f"Screenshot callback failed: {error}"

    completed = len(completed_members)
    failed = 1 if failed_index is not None else 0
    skipped = max(0, batch_size - completed - failed)
    metadata = {
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "failed_action_index": failed_index,
        "status": "completed" if stopped_reason is None else "stopped",
        "error": stopped_reason,
        "duration_ms": round((time.monotonic() - started_at) * 1000),
    }
    output: dict[str, Any] = {
        "type": "input_image",
        "image_url": data_url,
    }
    if shell_output_text is not None:
        # Shell keeps the standard post-action screenshot and additionally
        # returns the command's (already truncated) output text. A late failure
        # (e.g. the screenshot callback) must not discard output from a command
        # that already ran — carry both.
        if stopped_reason is None:
            output["result"] = shell_output_text
        else:
            output["result"] = {**metadata, "output": shell_output_text}
    elif isinstance(batch_actions, list) or stopped_reason is not None:
        output["result"] = metadata
    result = [
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        }
    ]
    await callbacks.fire("on_computer_call_end", item, result)
    await _present(
        presentation,
        {
            "type": "action_done",
            "call_id": call_id,
            "batch_complete": isinstance(batch_actions, list),
        },
    )
    return result


class N2ComputerAgent:
    """Drive one Navigator n2 computer-use conversation to completion.

    ``computer`` is any object with the async computer-handler surface
    (``screenshot``, ``click``, ``double_click``, ``scroll``, ``type``,
    ``keypress``, ``drag``, ``move``, ``wait``, and optionally
    ``run_shell_command``/``run_bash_command``). ``completions`` is a
    chat-completions surface such as ``AsyncYutoriClient().chat.completions``;
    when omitted, the agent owns an ``AsyncYutoriClient`` built from
    ``api_key``/``base_url`` and closes it via ``aclose()`` or the async
    context manager.

    Set ``supports_click_modifiers`` only for a handler that can execute a
    modifier as one gesture for clicks and scrolls. It may also expose
    ``triple_click`` to preserve native multi-click timing; otherwise the loop
    falls back to double-click followed by left-click.

    ``run()`` yields step dicts: ``{"output": [items...], "usage": {...}}`` for
    each model turn, then ``{"output": [result frame]}`` per executed tool
    call. It terminates when the model answers with a plain assistant message
    or a callback's ``on_run_continue`` returns False.
    """

    def __init__(
        self,
        *,
        computer: Any,
        tool_set: str = TOOL_SET_COMPUTER_USE_LATEST,
        completions: "SupportsN2ChatCompletionsCreate | None" = None,
        api_key: "str | None" = None,
        base_url: "str | None" = None,
        model: str = NAVIGATOR_N2_MODEL,
        instructions: "str | None" = None,
        callbacks: "list[Any] | None" = None,
        action_confirmation_callback: "ConfirmationCallback | None" = None,
        presentation: "N2Presentation | None" = None,
        screenshot_delay: float = 0.5,
        execution_deadline: "float | None" = None,
        temperature: "float | None" = None,
        supports_click_modifiers: bool = False,
    ):
        if tool_set not in SUPPORTED_N2_TOOL_SETS:
            raise ValueError(f"Unsupported n2 tool_set: {tool_set}")
        if completions is None and api_key is None:
            raise ValueError("Provide either completions or api_key")
        if execution_deadline is not None and not isinstance(execution_deadline, (int, float)):
            raise ValueError("execution_deadline must be a monotonic timestamp in seconds")
        if supports_click_modifiers and tool_set not in TOOL_SETS_WITH_CLICK_MODIFIERS:
            raise ValueError(f"Click modifiers require a modifier-capable n2 tool set, not {tool_set}")
        self.computer = computer
        self.tool_set = tool_set
        self.model = model
        self.instructions = instructions
        self.temperature = temperature
        self.screenshot_delay = screenshot_delay
        self.execution_deadline = execution_deadline
        self.supports_click_modifiers = supports_click_modifiers
        self.action_confirmation_callback = action_confirmation_callback
        self.presentation = presentation
        self._callbacks = _CallbackDispatcher(callbacks)
        self._completions = completions
        self._api_key = api_key
        self._base_url = base_url
        self._owned_client: Any = None
        self.timings: dict[str, float] = {"model_ms": 0}

    async def __aenter__(self) -> "N2ComputerAgent":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        client, self._owned_client = self._owned_client, None
        if client is not None:
            await client.close()

    def _resolve_completions(self) -> Any:
        if self._completions is not None:
            return self._completions
        if self._owned_client is None:
            # Imported here, not at module level: the async client imports
            # navigator.models, so a top-level import would be circular.
            from ..async_client import AsyncYutoriClient

            kwargs: dict[str, Any] = {"api_key": self._api_key}
            if self._base_url is not None:
                kwargs["base_url"] = self._base_url
            self._owned_client = AsyncYutoriClient(**kwargs)
        return self._owned_client.chat.completions

    def _initial_items(self, messages: Any) -> list[dict[str, Any]]:
        if isinstance(messages, str):
            items: list[dict[str, Any]] = [{"role": "user", "content": messages}]
        else:
            items = [dict(message) for message in messages]
        if self.instructions:
            items.insert(0, {"role": "user", "content": self.instructions})
        return items

    async def _predict_step(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        completion_messages = convert_n2_items_to_completion_messages(copy.deepcopy(items))

        latest_url = latest_image_url(completion_messages)
        if latest_url is None:
            observation = await self.computer.screenshot()
            latest_url, native_width, native_height, screenshot_b64 = _observation_data(observation)
            await self._callbacks.fire("on_screenshot", screenshot_b64, "screenshot_before")
            completion_messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": latest_url}},
                        {"type": "text", "text": "Current desktop screen"},
                    ],
                }
            )
        else:
            current_observation = getattr(self.computer, "current_observation", None)
            if isinstance(current_observation, N2Observation):
                native_width = current_observation.native_width
                native_height = current_observation.native_height
            else:
                native_width, native_height = image_dimensions(latest_url)
        # Strip historical screenshots before compression so long-running
        # trajectories do not repeatedly re-encode images that will not be
        # sent. Apply the byte budget after conversion because it measures the
        # actual request representation.
        completion_messages = retain_n2_image_window(completion_messages)
        convert_request_images(completion_messages)
        completion_messages = fit_n2_request_images_to_budget(completion_messages, DEFAULT_MAX_MESSAGES_BYTES)

        request_bytes = serialized_messages_bytes(completion_messages)
        if request_bytes > MAX_REQUEST_BODY_BYTES:
            raise ValueError(
                f"Serialized n2 request is {request_bytes} bytes, above the "
                f"{MAX_REQUEST_BODY_BYTES}-byte request limit."
            )

        api_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": completion_messages,
            "tool_set": self.tool_set,
            "max_completion_tokens": N2_MAX_COMPLETION_TOKENS,
            "parallel_tool_calls": True,
        }
        if self.temperature is not None:
            api_kwargs["temperature"] = self.temperature
        await self._callbacks.fire("on_api_start", api_kwargs)
        model_started_at = time.monotonic()
        try:
            response = await _await_model_response(self.computer, self._resolve_completions().create(**api_kwargs))
        finally:
            self.timings["model_ms"] += (time.monotonic() - model_started_at) * 1000
        await self._callbacks.fire("on_api_end", api_kwargs, response)

        response_dict = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        usage = response_dict.get("usage") or {}
        await self._callbacks.fire("on_usage", usage)

        message = (response_dict.get("choices") or [{}])[0].get("message") or {}
        output = parse_n2_tool_calls(
            message,
            native_width,
            native_height,
            tool_set=self.tool_set,
            execution_deadline=self.execution_deadline,
            allow_click_modifiers=self.supports_click_modifiers,
        )
        for output_item in output:
            if output_item.get("type") == "reasoning":
                text = _presentation_text(output_item, "summary")
                if text:
                    await _present(self.presentation, {"type": "reasoning", "text": text})
            elif output_item.get("type") == "message" and not message.get("tool_calls"):
                await _present(
                    self.presentation,
                    {"type": "final", "text": _presentation_text(output_item, "content")},
                )
        return {"output": output, "usage": usage}

    async def run(self, messages: Any) -> "AsyncGenerator[dict[str, Any], None]":
        """Run the agent until the model finishes or a callback stops it."""
        old_items = self._initial_items(messages)
        new_items: list[dict[str, Any]] = []
        run_kwargs = {
            "messages": messages,
            "model": self.model,
            "tool_set": self.tool_set,
        }
        await self._callbacks.fire("on_run_start", run_kwargs, old_items)
        try:
            while new_items[-1].get("role") != "assistant" if new_items else True:
                if not await self._callbacks.should_continue(run_kwargs, old_items, new_items):
                    break

                result = await self._predict_step(old_items + new_items)
                yield result
                new_items += result.get("output") or []

                # A validation failure already produced this call's result
                # frame; executing it anyway would run an action the model was
                # just told is invalid.
                answered_call_ids = [
                    item.get("call_id")
                    for item in result.get("output") or []
                    if item.get("type") == "function_call_output"
                ]
                for item in result.get("output") or []:
                    if item.get("type") == "message":
                        await self._callbacks.fire("on_text", item)
                        continue
                    if item.get("type") != "function_call":
                        continue
                    if item.get("_computer_actions") is None:
                        continue
                    if item.get("call_id") in answered_call_ids:
                        continue
                    partial_items = await execute_n2_computer_call(
                        item,
                        self.computer,
                        callbacks=self._callbacks,
                        confirmation_callback=self.action_confirmation_callback,
                        screenshot_delay=self.screenshot_delay,
                        presentation=self.presentation,
                    )
                    new_items += partial_items
                    if partial_items:
                        yield {"output": partial_items, "usage": {}}
        finally:
            # Unlike the reference, this fires even when the very first
            # on_run_continue stops the run.
            await self._callbacks.fire("on_run_end", run_kwargs, old_items, new_items)
