from __future__ import annotations

import copy

import pytest

from yutori.navigator import NAVIGATOR_N1_MODEL
from yutori.navigator.loop import update_trimmed_history
from yutori.navigator.replay import (
    TrajectoryRecorder,
    _extract_observation_parts,
    _extract_tool_result_parts,
    generate_visualization_html,
    make_run_id,
    sanitize_step_payload,
)

from ._client_fixtures import _image_message


def _tool_call(name: str, arguments: str, *, call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _tool_call_message(*calls: dict, content: object = None) -> dict:
    return {"role": "assistant", "content": content, "tool_calls": list(calls)}


class FakeResult:
    score = 1.0

    def model_dump(self, mode: str = "json", exclude_none: bool = True) -> dict:
        return {"score": self.score, "status": "ok"}


def test_make_run_id_slugifies_label() -> None:
    run_id = make_run_id(prefix="navigator", label="List the team / members")

    assert run_id.startswith("navigator_list-the-team-members_")


def test_update_trimmed_history_keeps_full_history_intact() -> None:
    large_url = "data:image/png;base64," + ("A" * 5000)
    messages = [
        _image_message("user", url=large_url, text="one"),
        _image_message("tool", url=large_url, text="two"),
        _image_message("tool", url=large_url, text="three"),
    ]

    request_messages, _, removed = update_trimmed_history(messages, max_bytes=12_000, keep_recent=1)

    assert removed > 0
    assert request_messages is not messages
    assert messages[0]["content"][1]["image_url"]["url"] == large_url


def test_update_trimmed_history_reuses_existing_request_copy_when_trimming() -> None:
    large_url = "data:image/png;base64," + ("A" * 5000)
    messages = [
        _image_message("user", url=large_url, text="one"),
        _image_message("tool", url=large_url, text="two"),
        _image_message("tool", url=large_url, text="three"),
    ]
    request_messages = copy.deepcopy(messages)

    updated_request_messages, _, removed = update_trimmed_history(
        messages,
        request_messages,
        max_bytes=12_000,
        keep_recent=1,
    )

    assert removed > 0
    assert updated_request_messages is request_messages
    assert messages[0]["content"][1]["image_url"]["url"] == large_url


def test_sanitize_step_payload_clips_images_before_storage() -> None:
    large_url = "data:image/png;base64," + ("A" * 400)

    sanitized = sanitize_step_payload(
        {
            "step_num": 1,
            "request": {
                "model": NAVIGATOR_N1_MODEL,
                "messages": [_image_message("user", url=large_url, text="Inspect page")],
            },
            "response": {"id": "resp_123"},
        }
    )

    assert sanitized["request"]["messages"][0]["content"][1]["image_url"]["url"].endswith("...[clipped]")


def test_generate_visualization_html_includes_steps_and_result() -> None:
    messages = [
        _image_message("user", text="Open the page"),
        _tool_call_message(
            _tool_call("left_click", '{"coordinates":[250,500]}'),
            content=[{"type": "text", "text": "Click the main CTA"}],
        ),
        _image_message("tool", text="Clicked button"),
        {"role": "assistant", "content": "The CTA is now open."},
    ]

    html = generate_visualization_html("demo-task", messages, result=FakeResult())

    assert "Trajectory Replay" in html
    assert "demo-task" in html
    assert "left_click" in html
    assert "Final Answer" in html
    assert "Raw Request" in html
    assert "Raw Response" in html
    assert "Result Artifact" in html
    assert "data:image/png;base64,abc" in html


def test_generate_visualization_html_renders_raw_request_and_response_json() -> None:
    large_url = "data:image/png;base64," + ("A" * 400)
    messages = [
        _image_message("user", url=large_url, text="Inspect page"),
        _tool_call_message(_tool_call("left_click", '{"coordinates":[100,200]}')),
    ]
    step_payloads = [
        {
            "step_num": 1,
            "request": {
                "model": NAVIGATOR_N1_MODEL,
                "messages": [_image_message("user", url=large_url, text="Inspect page")],
            },
            "response": {
                "id": "resp_123",
                "choices": [{"message": {"role": "assistant", "content": None}}],
            },
        }
    ]

    html = generate_visualization_html("tool-only", messages, step_payloads=step_payloads)

    assert "<h3>Raw Request</h3>" in html
    assert "<h3>Raw Response</h3>" in html
    assert "...[clipped]" in html
    assert "Text Observations" not in html


@pytest.mark.asyncio
async def test_trajectory_recorder_writes_artifacts(tmp_path) -> None:
    recorder = TrajectoryRecorder(tmp_path, "run-123")
    messages = [
        _image_message("user", text="Inspect page"),
        _tool_call_message(_tool_call("left_click", '{"coordinates":[100,200]}')),
    ]
    large_url = "data:image/png;base64," + ("A" * 400)
    step_payloads = [
        {
            "step_num": 1,
            "request": {
                "model": NAVIGATOR_N1_MODEL,
                "messages": [_image_message("user", url=large_url, text="Inspect page")],
            },
            "response": {"id": "resp_123"},
        }
    ]

    await recorder.save_messages(messages)
    await recorder.save_step_payloads(step_payloads)
    await recorder.save_html(messages, step_payloads=step_payloads)
    await recorder.save_json("result.json", {"score": 1.0})

    assert await recorder.load_messages() == messages
    loaded_step_payloads = await recorder.load_step_payloads()
    assert loaded_step_payloads[0]["step_num"] == 1
    assert loaded_step_payloads[0]["request"]["messages"][0]["content"][1]["image_url"]["url"].endswith("...[clipped]")
    assert await recorder.load_json("result.json") == {"score": 1.0}
    assert recorder.artifact_path("visualization.html").exists()
    html = recorder.artifact_path("visualization.html").read_text(encoding="utf-8")
    assert "Trajectory Replay" in html
    assert "Raw Request" in html


@pytest.mark.asyncio
async def test_trajectory_recorder_load_methods_handle_missing_artifacts(tmp_path) -> None:
    recorder = TrajectoryRecorder(tmp_path, "run-456")

    assert await recorder.load_json("result.json") is None
    assert await recorder.load_jsonl("messages.jsonl") == []
    assert await recorder.load_messages() == []
    assert await recorder.load_step_payloads() == []


def test_generate_visualization_html_survives_non_object_tool_arguments() -> None:
    # Valid JSON that isn't an object must not crash the render.
    messages = [
        _image_message("user", text="Open the page"),
        _tool_call_message(
            _tool_call("left_click", "[1, 2]"),
            _tool_call("type_text", '"hello"', call_id="call_2"),
        ),
    ]

    html = generate_visualization_html("demo-task", messages)

    assert "left_click" in html
    assert "type_text" in html


def test_generate_visualization_html_escapes_screenshot_url() -> None:
    hostile_url = 'https://example.com/shot.png"><script>alert(1)</script>'
    messages = [
        _image_message("user", url=hostile_url, text="Open the page"),
        {"role": "assistant", "content": "Done."},
    ]

    html = generate_visualization_html("demo-task", messages)

    assert "<script>alert(1)</script>" not in html
    # The URL must survive in escaped form — not merely be dropped from the render.
    assert "shot.png&quot;&gt;&lt;script&gt;" in html


def test_extract_observation_parts_handles_falsy_observation() -> None:
    assert _extract_observation_parts(None) == (None, [])
    assert _extract_observation_parts([]) == (None, [])


def test_extract_observation_parts_collects_image_and_text_blocks() -> None:
    observation = [
        {"type": "text", "text": "  hello  "},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        {"type": "text", "text": "   "},
    ]

    url, texts = _extract_observation_parts(observation)

    assert url == "data:image/png;base64,abc"
    assert texts == ["hello"]


def test_extract_observation_parts_last_nonempty_image_url_wins() -> None:
    observation = [
        {"type": "image_url", "image_url": {"url": "first"}},
        {"type": "image_url", "image_url": {"url": ""}},
        {"type": "image_url", "image_url": {"url": "second"}},
    ]

    url, _ = _extract_observation_parts(observation)

    assert url == "second"


def test_extract_observation_parts_recurses_into_tool_result_content() -> None:
    observation = [
        {
            "type": "tool_result",
            "content": [
                {"type": "image_url", "image_url": {"url": "nested-url"}},
                {"type": "text", "text": "nested text"},
            ],
        }
    ]

    url, texts = _extract_observation_parts(observation)

    assert url == "nested-url"
    assert texts == ["nested text"]


def test_extract_observation_parts_ignores_non_list_tool_result_content() -> None:
    observation = [{"type": "tool_result", "content": "not-a-list"}]

    url, texts = _extract_observation_parts(observation)

    assert url is None
    assert texts == []


def test_extract_tool_result_parts_handles_base64_image_source() -> None:
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "xyz"}}]

    url, _ = _extract_tool_result_parts(content)

    assert url == "data:image/jpeg;base64,xyz"


def test_extract_tool_result_parts_defaults_media_type_to_png() -> None:
    content = [{"type": "image", "source": {"type": "base64", "data": "xyz"}}]

    url, _ = _extract_tool_result_parts(content)

    assert url == "data:image/png;base64,xyz"


def test_extract_tool_result_parts_ignores_non_base64_image_source() -> None:
    content = [{"type": "image", "source": {"type": "url", "url": "https://example.com/x.png"}}]

    url, _ = _extract_tool_result_parts(content)

    assert url is None


def test_extract_tool_result_parts_skips_non_dict_items() -> None:
    content = ["not-a-dict", {"type": "text", "text": "keep"}]

    url, texts = _extract_tool_result_parts(content)

    assert url is None
    assert texts == ["keep"]


def test_text_only_user_message_keeps_pending_tool_screenshot() -> None:
    # A human interjection between the tool screenshot and the assistant's
    # next step must not blank out the screenshot in the replay.
    messages = [
        _image_message("user", text="Open the page"),
        _tool_call_message(_tool_call("left_click", "{}")),
        _image_message("tool", url="data:image/png;base64,toolshot", text="Clicked"),
        {"role": "user", "content": "Looks good, continue with checkout"},
        {"role": "assistant", "content": "Proceeding."},
    ]

    html = generate_visualization_html("demo-task", messages)

    assert "data:image/png;base64,toolshot" in html
    assert "No screenshot recorded" not in html
    assert "Looks good, continue with checkout" in html
