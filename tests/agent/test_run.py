"""run.py 纯逻辑单测：退避、断点判定、旗标、fail-fast、常驻测量（不触网）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.agent.config import AgentConfig, ConfigError
from tests.agent.loop import ChatResponse, Usage
from tests.agent.run import (
    ChatRetryError,
    OpenAIChat,
    _load_resident,
    _run_chat_with_retry,
    backoff_schedule,
    build_parser,
    is_run_complete,
    main,
    measure_resident,
    parse_args,
    run_experiment,
)
from tests.agent.tasks import TASKS
from tests.agent.transcript import (
    Transcript,
    load_transcript,
    make_run_id,
    save_transcript,
)


def _transcript(verdict: dict | None) -> Transcript:
    return Transcript(
        run_id=make_run_id("a", "t1_explicit", 1),
        arm="a",
        task_id="t1_explicit",
        rep=1,
        messages=[],
        tool_calls=[],
        usages=[],
        final_text="",
        gave_up=False,
        verdict=verdict,
        duration_s=0.1,
    )


# ---------- 退避 ----------


def test_backoff_schedule_is_exponential_and_capped() -> None:
    assert backoff_schedule(0) == []
    assert backoff_schedule(3) == [1.0, 2.0, 4.0]
    assert backoff_schedule(3, base=0.5) == [0.5, 1.0, 2.0]
    assert backoff_schedule(6, base=1.0, cap=5.0) == [1.0, 2.0, 4.0, 5.0, 5.0, 5.0]


def test_backoff_schedule_negative_attempts_is_empty() -> None:
    assert backoff_schedule(-1) == []


# ---------- 断点续跑 ----------


def test_is_run_complete_false_for_missing_file(tmp_path: Path) -> None:
    assert is_run_complete(tmp_path / "nope.json") is False


def test_is_run_complete_requires_verdict(tmp_path: Path) -> None:
    complete = save_transcript(
        _transcript({"passed": True, "reason": "ok", "hallucinated": False}),
        tmp_path / "done",
    )
    incomplete = save_transcript(_transcript(None), tmp_path / "pending")

    assert is_run_complete(complete) is True
    assert is_run_complete(incomplete) is False


def test_is_run_complete_false_for_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    assert is_run_complete(path) is False


# ---------- 旗标解析 ----------


def test_default_flags() -> None:
    args = parse_args([])

    assert args.arms == ["a", "b", "c", "d"]
    assert args.tasks == [task.id for task in TASKS]
    assert args.reps == 5
    assert args.judge_only is False
    assert args.report_only is False
    assert Path(args.transcripts_dir) == Path("tests/agent/transcripts")


def test_flags_can_be_narrowed() -> None:
    args = parse_args(
        [
            "--arms",
            "a,c",
            "--tasks",
            "t1_explicit,t5_negative",
            "--reps",
            "2",
            "--judge-only",
            "--transcripts-dir",
            "out",
        ]
    )

    assert args.arms == ["a", "c"]
    assert args.tasks == ["t1_explicit", "t5_negative"]
    assert args.reps == 2
    assert args.judge_only is True
    assert Path(args.transcripts_dir) == Path("out")


def test_invalid_arm_is_rejected() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--arms", "a,z"])


def test_build_parser_returns_parser() -> None:
    parser = build_parser()

    assert parser.prog


# ---------- 缺 key fail-fast ----------


def test_main_fails_fast_without_env(monkeypatch, capsys, tmp_path: Path) -> None:
    def fake_load_config(env_file=None):
        raise ConfigError("缺少 DEEPSEEK_API_KEY。请复制 .env.example 为 .env 并填写")

    monkeypatch.setattr("tests.agent.run.load_config", fake_load_config)

    code = main(
        [
            "--arms",
            "a",
            "--tasks",
            "t1_explicit",
            "--reps",
            "1",
            "--transcripts-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert code != 0
    assert ".env" in (captured.out + captured.err)


def test_report_only_does_not_require_config(monkeypatch, capsys, tmp_path: Path) -> None:
    def boom(env_file=None):
        raise AssertionError("load_config must not be called for --report-only")

    monkeypatch.setattr("tests.agent.run.load_config", boom)

    code = main(["--report-only", "--transcripts-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert code == 0
    assert "M2" in captured.out


def test_judge_only_does_not_require_config(monkeypatch, tmp_path: Path) -> None:
    save_transcript(
        _transcript({"passed": False, "reason": "stale", "hallucinated": False}),
        tmp_path,
    )

    def boom(env_file=None):
        raise AssertionError("load_config must not be called for --judge-only")

    monkeypatch.setattr("tests.agent.run.load_config", boom)

    code = main(["--judge-only", "--transcripts-dir", str(tmp_path)])

    assert code == 0


# ---------- 常驻 token 隔离测量 ----------


class FakeChat:
    def __init__(self, with_tools: int = 1500, without_tools: int = 120) -> None:
        self.with_tools = with_tools
        self.without_tools = without_tools
        self.calls: list[tuple[list[dict], list[dict]]] = []

    def create(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        self.calls.append((messages, tools))
        prompt = self.with_tools if tools else self.without_tools
        return ChatResponse(
            message={"role": "assistant", "content": ""},
            usage=Usage(
                prompt_tokens=prompt,
                completion_tokens=1,
                total_tokens=prompt + 1,
            ),
        )


def test_measure_resident_uses_isolated_requests() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_mcp_tool",
                "description": "搜索上游工具",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    chat = FakeChat()

    result = measure_resident(chat, tools)

    assert result == {
        "with_tools": 1500,
        "without_tools": 120,
        "tools_prompt_tokens": 1380,
        "tools_chars": len(json.dumps(tools, ensure_ascii=False)),
    }
    assert [tools_seen for _messages, tools_seen in chat.calls] == [[], tools]
    assert all(
        messages == [{"role": "user", "content": "ping"}]
        for messages, _tools in chat.calls
    )


# ---------- OpenAIChat 包装 ----------


class _FakeCompletions:
    def __init__(self, response: object) -> None:
        self.response = response
        self.kwargs: dict | None = None

    def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return self.response


class _FakeOpenAI:
    instances: list["_FakeOpenAI"] = []

    def __init__(self, **kwargs: object) -> None:
        self.init_kwargs = kwargs
        self.chat = SimpleNamespace(completions=None)
        _FakeOpenAI.instances.append(self)

    def set_completions(self, completions: _FakeCompletions) -> None:
        self.chat.completions = completions


def _fake_response() -> SimpleNamespace:
    message = SimpleNamespace(
        model_dump=lambda: {
            "role": "assistant",
            "content": None,
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
        model_extra={"reasoning_content": "先搜索"},
    )
    usage = SimpleNamespace(prompt_tokens=42, completion_tokens=7, total_tokens=49)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def test_openai_chat_passes_config_and_preserves_reasoning(monkeypatch) -> None:
    completions = _FakeCompletions(_fake_response())
    _FakeOpenAI.instances = []

    def fake_openai(**kwargs: object) -> _FakeOpenAI:
        client = _FakeOpenAI(**kwargs)
        client.set_completions(completions)
        return client

    monkeypatch.setattr("tests.agent.run.OpenAI", fake_openai)
    cfg = AgentConfig(base_url="http://example/v1", api_key="sk-test")

    chat = OpenAIChat(cfg)
    response = chat.create([{"role": "user", "content": "hi"}], [{"type": "function"}])

    assert _FakeOpenAI.instances[-1].init_kwargs == {
        "base_url": "http://example/v1",
        "api_key": "sk-test",
    }
    assert completions.kwargs is not None
    assert completions.kwargs["model"] == "deepseek-flash"
    assert completions.kwargs["reasoning_effort"] == "low"
    assert completions.kwargs["tools"] == [{"type": "function"}]
    assert response.message["reasoning_content"] == "先搜索"
    assert response.message["tool_calls"][0]["function"]["name"] == "search_mcp_tool"
    assert response.usage == Usage(prompt_tokens=42, completion_tokens=7, total_tokens=49)


def test_openai_chat_omits_tools_when_empty(monkeypatch) -> None:
    completions = _FakeCompletions(_fake_response())

    def fake_openai(**kwargs: object) -> _FakeOpenAI:
        client = _FakeOpenAI(**kwargs)
        client.set_completions(completions)
        return client

    monkeypatch.setattr("tests.agent.run.OpenAI", fake_openai)
    chat = OpenAIChat(AgentConfig(base_url="http://example/v1", api_key="sk-test"))

    chat.create([{"role": "user", "content": "ping"}], [])

    assert completions.kwargs is not None
    assert "tools" not in completions.kwargs


# ---------- resident.json 落盘与离线读取 ----------


def test_load_resident_missing_or_corrupt_returns_none(tmp_path: Path) -> None:
    assert _load_resident(tmp_path) is None

    (tmp_path / "resident.json").write_text("{not json", encoding="utf-8")
    assert _load_resident(tmp_path) is None


def test_report_only_reads_resident_file(monkeypatch, capsys, tmp_path: Path) -> None:
    def boom(env_file=None):
        raise AssertionError("load_config must not be called for --report-only")

    monkeypatch.setattr("tests.agent.run.load_config", boom)
    (tmp_path / "resident.json").write_text(
        json.dumps(
            {
                "a": {
                    "with_tools": 200,
                    "without_tools": 100,
                    "tools_prompt_tokens": 100,
                    "tools_chars": 10,
                },
                "b": {
                    "with_tools": 1100,
                    "without_tools": 100,
                    "tools_prompt_tokens": 1000,
                    "tools_chars": 20,
                },
            }
        ),
        encoding="utf-8",
    )

    code = main(["--report-only", "--transcripts-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert code == 0
    assert "90.0%" in captured.out


def test_report_only_without_resident_marks_untested(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    def boom(env_file=None):
        raise AssertionError("load_config must not be called for --report-only")

    monkeypatch.setattr("tests.agent.run.load_config", boom)

    code = main(["--report-only", "--transcripts-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert code == 0
    assert "未测（n/a）" in captured.out


# ---------- 单 run 异常兜底 ----------


class _FakeProxyArm:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    async def __aenter__(self) -> "_FakeProxyArm":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def list_openai_tools(self) -> list[dict]:
        return []

    async def call_upstream(self, name: str, arguments: dict) -> str:
        return "ok"


class _FlakyChat:
    def __init__(self, fail_on: int = 1) -> None:
        self.calls = 0
        self.fail_on = fail_on

    def create(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("boom")
        return ChatResponse(
            message={"role": "assistant", "content": "done"}, usage=Usage()
        )


def _patch_experiment(monkeypatch) -> dict:
    measurement = {
        "with_tools": 10,
        "without_tools": 1,
        "tools_prompt_tokens": 9,
        "tools_chars": 2,
    }
    monkeypatch.setattr("tests.agent.run.pick_fleet_ports", lambda: [1, 2, 3, 4])
    monkeypatch.setattr("tests.agent.run.spawn_http_demos", lambda ports: [])
    monkeypatch.setattr(
        "tests.agent.run.write_config",
        lambda directory, servers, settings=None: Path(directory) / "config.json",
    )
    monkeypatch.setattr("tests.agent.run.ProxyArm", _FakeProxyArm)
    monkeypatch.setattr(
        "tests.agent.run.measure_resident", lambda chat, tools: measurement
    )
    return {"a": measurement}


def test_run_experiment_records_failure_and_continues(
    monkeypatch, tmp_path: Path
) -> None:
    resident = _patch_experiment(monkeypatch)
    args = parse_args(
        [
            "--arms",
            "a",
            "--tasks",
            "t1_explicit",
            "--reps",
            "2",
            "--transcripts-dir",
            str(tmp_path),
        ]
    )
    chat = _FlakyChat(fail_on=1)

    result = asyncio.run(
        run_experiment(
            args,
            chat=chat,
            cfg=AgentConfig(base_url="http://example/v1", api_key="sk-test"),
        )
    )

    assert result == resident
    saved = json.loads((tmp_path / "resident.json").read_text(encoding="utf-8"))
    assert saved == resident

    first_path = tmp_path / "a-t1_explicit-r1.json"
    first = load_transcript(first_path)
    assert first.gave_up is True
    assert first.final_text == ""
    assert first.messages == []
    assert first.verdict == {
        "passed": False,
        "reason": "run failed: RuntimeError",
        "hallucinated": False,
    }
    assert is_run_complete(first_path) is True

    second = load_transcript(tmp_path / "a-t1_explicit-r2.json")
    assert second.gave_up is False
    assert chat.calls == 2


async def test_chat_retry_exhausted_raises_chat_retry_error(monkeypatch) -> None:
    class _Retryable(Exception):
        pass

    # 用替身替换 run.py 里的限流异常类，避免依赖真实 SDK 的响应对象。
    monkeypatch.setattr("tests.agent.run.RateLimitError", _Retryable)

    async def boom(*args: object, **kwargs: object):
        raise _Retryable("rate limited")

    monkeypatch.setattr("tests.agent.run.run_tool_loop", boom)

    with pytest.raises(ChatRetryError):
        await _run_chat_with_retry(
            object(),
            SimpleNamespace(call_upstream=None),
            TASKS[0],
            [],
            attempts=0,
        )
