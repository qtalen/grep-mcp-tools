"""transcript 序列化、run-id 与出口脱敏断言单测。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.agent.transcript import (
    Transcript,
    assert_no_secret,
    load_transcript,
    make_run_id,
    save_transcript,
)


def _transcript(**overrides: object) -> Transcript:
    values: dict[str, object] = {
        "run_id": "a-t1-r1",
        "arm": "a",
        "task_id": "t1",
        "rep": 1,
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "查一下年假"},
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "先搜索",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "search_mcp_tool",
                            "arguments": '{"keywords": ["leave"]}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "hr__get_leave_balance"},
            {"role": "assistant", "content": "12 天"},
        ],
        "tool_calls": [
            {
                "name": "search_mcp_tool",
                "arguments": {"keywords": ["leave"]},
                "result": "hr__get_leave_balance",
            }
        ],
        "usages": [
            {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            {"prompt_tokens": 120, "completion_tokens": 5, "total_tokens": 125},
        ],
        "final_text": "12 天",
        "gave_up": False,
        "verdict": {"passed": True, "reason": "target called"},
        "duration_s": 1.25,
    }
    values.update(overrides)
    return Transcript(**values)  # type: ignore[arg-type]


def test_make_run_id() -> None:
    assert make_run_id("a", "t1", 3) == "a-t1-r3"
    assert make_run_id("d", "neg-capability", 12) == "d-neg-capability-r12"


def test_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    transcript = _transcript()

    path = save_transcript(transcript, tmp_path / "transcripts")
    loaded = load_transcript(path)

    assert path == tmp_path / "transcripts" / "a-t1-r1.json"
    assert loaded == transcript
    assert loaded.messages[2]["reasoning_content"] == "先搜索"


def test_round_trip_without_verdict(tmp_path: Path) -> None:
    transcript = _transcript(verdict=None)

    loaded = load_transcript(save_transcript(transcript, tmp_path))

    assert loaded.verdict is None


def test_creates_missing_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "deep" / "transcripts"
    assert not out_dir.exists()

    path = save_transcript(_transcript(), out_dir)

    assert out_dir.is_dir()
    assert path.is_file()


def test_output_is_utf8_json_without_ascii_escaping(tmp_path: Path) -> None:
    path = save_transcript(_transcript(), tmp_path)
    raw = path.read_text(encoding="utf-8")

    assert "12 天" in raw
    assert json.loads(raw)["run_id"] == "a-t1-r1"


def test_assert_no_secret_passes_when_secret_absent() -> None:
    value = {
        "messages": [{"content": "nothing sensitive here"}],
        "usages": [{"prompt_tokens": 3}],
    }

    assert_no_secret(value, "sk-live-should-not-appear")


def test_assert_no_secret_detects_nested_secret() -> None:
    value = {
        "messages": [
            {"role": "user", "content": "ok"},
            {"role": "tool", "content": "prefix sk-live-should-not-appear suffix"},
        ]
    }

    with pytest.raises(AssertionError) as excinfo:
        assert_no_secret(value, "sk-live-should-not-appear")

    assert "sk-live-should-not-appear" in str(excinfo.value)


def test_assert_no_secret_detects_secret_in_mapping_key() -> None:
    with pytest.raises(AssertionError):
        assert_no_secret({"sk-live-key": "value"}, "sk-live-key")


def test_assert_no_secret_scans_top_level_string() -> None:
    with pytest.raises(AssertionError):
        assert_no_secret("Bearer sk-live-key", "sk-live-key")
