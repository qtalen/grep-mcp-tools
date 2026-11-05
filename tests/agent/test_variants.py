"""消融臂包装单测：剥离生效、其余工具不受影响、剥不动即断言失败。"""

from __future__ import annotations

import copy

import pytest

from tests.agent.variants import AblatedArm

SEARCH = "search_mcp_tool"
TRIGGER = "Search across upstream tools."
INJECTED = f"{TRIGGER}\n\nConnected services: hr (8 tools)"


def _tool(name: str, description: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


class FakeInner:
    """最小代理 Arm 替身。"""

    def __init__(self, tools: list[dict], results: dict[str, str]) -> None:
        self._tools = tools
        self._results = results
        self.calls: list[tuple[str, dict]] = []

    async def list_openai_tools(self) -> list[dict]:
        return copy.deepcopy(self._tools)

    async def call_upstream(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return self._results[name]


def _names(tools: list[dict]) -> list[str]:
    return [item["function"]["name"] for item in tools]


async def test_description_strip_only_touches_search_tool() -> None:
    inner = FakeInner(
        [
            _tool(SEARCH, INJECTED),
            _tool("invoke_this_tool", "Invoke a tool."),
            _tool("check_mcp_status", "Check status."),
        ],
        {},
    )
    arm = AblatedArm(inner, strip_description=True)

    tools = await arm.list_openai_tools()

    assert _names(tools) == [SEARCH, "invoke_this_tool", "check_mcp_status"]
    assert tools[0]["function"]["description"] == TRIGGER
    assert tools[1]["function"]["description"] == "Invoke a tool."
    assert tools[2]["function"]["description"] == "Check status."


async def test_description_is_unchanged_without_flag() -> None:
    inner = FakeInner([_tool(SEARCH, INJECTED)], {})
    arm = AblatedArm(inner)

    tools = await arm.list_openai_tools()

    assert tools[0]["function"]["description"] == INJECTED


async def test_strip_does_not_mutate_inner_tool_objects() -> None:
    original = _tool(SEARCH, INJECTED)
    inner = FakeInner([original], {})
    arm = AblatedArm(inner, strip_description=True)

    await arm.list_openai_tools()

    assert original["function"]["description"] == INJECTED


async def test_description_strip_failure_raises_assertion() -> None:
    # 有 `Connected services` 但没有 `\n\n` 起始标记：剥离器认不出，必须炸。
    bad = "prefix Connected services: hr (8 tools)"
    inner = FakeInner([_tool(SEARCH, bad)], {})
    arm = AblatedArm(inner, strip_description=True)

    with pytest.raises(AssertionError):
        await arm.list_openai_tools()


async def test_signpost_strip_only_touches_search_tool_result() -> None:
    no_hit = "No tools matched [zzz].\n\nAvailable services:\n- hr: 8 tools"
    inner = FakeInner(
        [],
        {
            SEARCH: no_hit,
            "invoke_this_tool": "invoke result mentions Available services: inline",
        },
    )
    arm = AblatedArm(inner, strip_signpost=True)

    stripped = await arm.call_upstream(SEARCH, {"keywords": ["zzz"]})
    other = await arm.call_upstream("invoke_this_tool", {"target": "hr__search"})

    assert stripped == "No tools matched [zzz]."
    assert "Available services" not in stripped
    assert other == "invoke result mentions Available services: inline"
    assert inner.calls == [
        (SEARCH, {"keywords": ["zzz"]}),
        ("invoke_this_tool", {"target": "hr__search"}),
    ]


async def test_signpost_result_unchanged_without_flag() -> None:
    no_hit = "No tools matched [zzz].\n\nAvailable services:\n- hr: 8 tools"
    inner = FakeInner([], {SEARCH: no_hit})
    arm = AblatedArm(inner)

    assert await arm.call_upstream(SEARCH, {}) == no_hit


async def test_signpost_strip_failure_raises_assertion() -> None:
    bad = "Some result without the marker block but mentioning Available services:"
    inner = FakeInner([], {SEARCH: bad})
    arm = AblatedArm(inner, strip_signpost=True)

    with pytest.raises(AssertionError):
        await arm.call_upstream(SEARCH, {})
