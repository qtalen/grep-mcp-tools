"""工具循环单测：假 chat 客户端驱动，不联网。"""

from __future__ import annotations

from typing import Any

import pytest

from tests.agent.loop import (
    ChatResponse,
    ToolArgumentsError,
    ToolCallRecord,
    Usage,
    run_tool_loop,
)


def _usage(prompt: int = 10, completion: int = 5) -> Usage:
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


def _response(
    content: str | None = "done",
    tool_calls: list[dict[str, Any]] | None = None,
    usage: Usage | None = None,
) -> ChatResponse:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return ChatResponse(message=message, usage=usage or _usage())


def _call(call_id: str, name: str, arguments: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class FakeChat:
    """按顺序返回预置响应，并记录每轮看到的 messages/tools。"""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[list[dict], list[dict]]] = []

    def create(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        self.calls.append((list(messages), tools))
        return self._responses.pop(0)


async def test_no_tool_calls_terminates_immediately() -> None:
    response = _response("final answer", usage=_usage(11, 7))
    chat = FakeChat([response])

    result = await run_tool_loop(
        chat,
        "hello",
        tools=[{"type": "function"}],
        call_tool=_unexpected_call,
        system="be terse",
    )

    assert result.final_text == "final answer"
    assert result.turns == 1
    assert result.gave_up is False
    assert result.usages == [_usage(11, 7)]
    assert result.tool_calls == []
    assert result.messages == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello"},
        response.message,
    ]
    seen_messages, seen_tools = chat.calls[0]
    assert seen_messages == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello"},
    ]
    assert seen_tools == [{"type": "function"}]


async def test_system_is_omitted_when_none() -> None:
    chat = FakeChat([_response("ok")])

    result = await run_tool_loop(chat, "hello", tools=[], call_tool=_unexpected_call)

    assert result.messages[0] == {"role": "user", "content": "hello"}


async def test_tool_call_is_executed_and_result_injected() -> None:
    first = _response(
        content=None,
        tool_calls=[_call("call_1", "hr__get_leave_balance", '{"employee_id": "E1001"}')],
    )
    chat = FakeChat([first, _response("12 days")])
    invoked: list[tuple[str, dict]] = []

    async def call_tool(name: str, arguments: dict) -> str:
        invoked.append((name, arguments))
        return "employee E1001: 12 days of leave remaining"

    result = await run_tool_loop(chat, "how much leave?", tools=[], call_tool=call_tool)

    assert invoked == [("hr__get_leave_balance", {"employee_id": "E1001"})]
    assert result.final_text == "12 days"
    assert result.turns == 2
    assert result.tool_calls == [
        ToolCallRecord(
            name="hr__get_leave_balance",
            arguments={"employee_id": "E1001"},
            result="employee E1001: 12 days of leave remaining",
        )
    ]
    tool_messages = [message for message in result.messages if message["role"] == "tool"]
    assert tool_messages == [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "employee E1001: 12 days of leave remaining",
        }
    ]
    assert result.messages[1] is first.message


async def test_assistant_message_identity_and_reasoning_content_preserved() -> None:
    first = _response(content="", tool_calls=[_call("call_1", "search", "{}")])
    first.message["reasoning_content"] = "I should search first."
    chat = FakeChat([first, _response("found")])

    async def call_tool(name: str, arguments: dict) -> str:
        return "ok"

    result = await run_tool_loop(chat, "find it", tools=[], call_tool=call_tool)

    assistant_messages = [
        message for message in result.messages if message["role"] == "assistant"
    ]
    assert assistant_messages[0] is first.message
    assert assistant_messages[0]["reasoning_content"] == "I should search first."


async def test_gives_up_after_max_turns() -> None:
    responses = [
        _response(content="still going", tool_calls=[_call(f"call_{i}", "search", "{}")])
        for i in range(3)
    ]
    chat = FakeChat(responses)
    invoked: list[str] = []

    async def call_tool(name: str, arguments: dict) -> str:
        invoked.append(name)
        return "ok"

    result = await run_tool_loop(
        chat, "loop", tools=[], call_tool=call_tool, max_turns=2
    )

    assert result.gave_up is True
    assert result.turns == 2
    assert result.final_text == "still going"
    assert len(invoked) == 2
    assert len(result.usages) == 2


async def test_multiple_tool_calls_run_in_order() -> None:
    first = _response(
        content=None,
        tool_calls=[
            _call("call_a", "hr__search", '{"query": "ada"}'),
            _call("call_b", "itops__search", '{"query": "deploy"}'),
        ],
    )
    chat = FakeChat([first, _response("both done")])
    invoked: list[tuple[str, dict]] = []

    async def call_tool(name: str, arguments: dict) -> str:
        invoked.append((name, arguments))
        return f"result of {name}"

    result = await run_tool_loop(chat, "search both", tools=[], call_tool=call_tool)

    assert invoked == [
        ("hr__search", {"query": "ada"}),
        ("itops__search", {"query": "deploy"}),
    ]
    assert [record.name for record in result.tool_calls] == [
        "hr__search",
        "itops__search",
    ]
    tool_messages = [message for message in result.messages if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in tool_messages] == ["call_a", "call_b"]


async def test_empty_arguments_string_is_empty_dict() -> None:
    first = _response(content=None, tool_calls=[_call("call_1", "check_mcp_status", "")])
    chat = FakeChat([first, _response("ok")])
    invoked: list[tuple[str, dict]] = []

    async def call_tool(name: str, arguments: dict) -> str:
        invoked.append((name, arguments))
        return "ok"

    await run_tool_loop(chat, "status", tools=[], call_tool=call_tool)

    assert invoked == [("check_mcp_status", {})]


async def test_malformed_arguments_raise_tool_arguments_error() -> None:
    first = _response(
        content=None,
        tool_calls=[_call("call_1", "search", "{not-json")],
    )
    chat = FakeChat([first])

    with pytest.raises(ToolArgumentsError):
        await run_tool_loop(chat, "boom", tools=[], call_tool=_unexpected_call)


async def test_usage_is_accumulated_per_turn() -> None:
    first = _response(
        content=None,
        tool_calls=[_call("call_1", "search", "{}")],
        usage=_usage(100, 10),
    )
    second = _response("done", usage=_usage(200, 20))
    chat = FakeChat([first, second])

    async def call_tool(name: str, arguments: dict) -> str:
        return "ok"

    result = await run_tool_loop(chat, "go", tools=[], call_tool=call_tool)

    assert result.usages == [_usage(100, 10), _usage(200, 20)]


async def _unexpected_call(name: str, arguments: dict) -> str:
    pytest.fail(f"call_tool should not be called, got {name}({arguments})")
