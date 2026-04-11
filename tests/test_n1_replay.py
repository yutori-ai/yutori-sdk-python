from __future__ import annotations

import copy

import pytest

from yutori.n1.loop import update_trimmed_history
from yutori.n1.replay import (
    TrajectoryRecorder,
    generate_visualization_html,
    make_run_id,
    sanitize_step_payload,
)


def _image_message(role: str, *, url: str = "data:image/png;base64,abc", text: str | None = None) -> dict:
    content: list[dict] = []
    if text is not None:
        content.append({"type": "text", "text": text})
    content.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})
    return {"role": role, "content": content}


class FakeResult:
    score = 1.0

    def model_dump(self, mode: str = "json", exclude_none: bool = True) -> dict:
        return {"score": self.score, "status": "ok"}


def test_make_run_id_slugifies_label() -> None:
    run_id = make_run_id(prefix="n1.5", label="List the team / members")

    assert run_id.startswith("n1.5_list-the-team-members_")


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
                "model": "n1-latest",
                "messages": [_image_message("user", url=large_url, text="Inspect page")],
            },
            "response": {"id": "resp_123"},
        }
    )

    assert sanitized["request"]["messages"][0]["content"][1]["image_url"]["url"].endswith("...[clipped]")


def test_generate_visualization_html_includes_steps_and_result() -> None:
    messages = [
        _image_message("user", text="Open the page"),
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Click the main CTA"}],
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "left_click", "arguments": '{"coordinates":[250,500]}'},
                }
            ],
        },
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
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "left_click", "arguments": '{"coordinates":[100,200]}'},
                }
            ],
        },
    ]
    step_payloads = [
        {
            "step_num": 1,
            "request": {
                "model": "n1-latest",
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
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "left_click", "arguments": '{"coordinates":[100,200]}'},
                }
            ],
        },
    ]
    large_url = "data:image/png;base64," + ("A" * 400)
    step_payloads = [
        {
            "step_num": 1,
            "request": {
                "model": "n1-latest",
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
