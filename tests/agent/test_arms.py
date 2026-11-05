"""两臂纯函数与分发单测：不拉真实进程。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from tests.agent.arms import (
    DirectArm,
    ProxyArm,
    UpstreamSpec,
    flatten_tools,
    merge_tool_lists,
)


def _tool(name: str, schema: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"{name} description",
        inputSchema=schema
        or {"type": "object", "properties": {}, "required": []},
    )


class FakeClient:
    """最小 MCP Client 替身：list_tools 返回 .tools，call_tool 返回文本块。"""

    def __init__(self, tools: list[Any]) -> None:
        self.tools = list(tools)
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(tools=list(self.tools))

    async def call_tool(
        self, name: str, arguments: dict | None = None
    ) -> SimpleNamespace:
        self.calls.append((name, arguments or {}))
        payload = json.dumps(arguments or {}, ensure_ascii=False)
        return SimpleNamespace(
            content=[
                SimpleNamespace(text=f"{name}("),
                SimpleNamespace(text=payload),
                SimpleNamespace(text=")"),
            ]
        )


def test_flatten_tools_prefixes_server_name() -> None:
    flattened = flatten_tools("hr", [_tool("search"), _tool("get_employee")])

    assert [item["function"]["name"] for item in flattened] == [
        "hr__search",
        "hr__get_employee",
    ]
    assert all(item["type"] == "function" for item in flattened)
    assert flattened[0]["function"]["description"] == "search description"


def test_same_named_tools_from_different_servers_do_not_collide() -> None:
    hr = flatten_tools("hr", [_tool("search")])
    itops = flatten_tools("itops", [_tool("search")])

    names = [item["function"]["name"] for item in hr + itops]

    assert names == ["hr__search", "itops__search"]
    assert len(set(names)) == 2


def test_merge_preserves_dict_insertion_order() -> None:
    per_server = {
        "hr": flatten_tools("hr", [_tool("search")]),
        "itops": flatten_tools("itops", [_tool("search"), _tool("create_ticket")]),
        "audit": flatten_tools("audit", [_tool("list_audit_events")]),
    }

    merged = merge_tool_lists(per_server)

    assert [item["function"]["name"] for item in merged] == [
        "hr__search",
        "itops__search",
        "itops__create_ticket",
        "audit__list_audit_events",
    ]


async def test_direct_arm_lists_all_upstreams_in_order() -> None:
    arm = DirectArm(
        [
            UpstreamSpec(name="hr", transport="stdio", command="python", args=("hr.py",)),
            UpstreamSpec(name="itops", transport="streamable-http", url="http://x/mcp"),
        ]
    )
    arm._clients = {
        "hr": FakeClient([_tool("search"), _tool("get_leave_balance")]),
        "itops": FakeClient([_tool("search"), _tool("create_ticket")]),
    }

    converted = await arm.list_openai_tools()

    assert [item["function"]["name"] for item in converted] == [
        "hr__search",
        "hr__get_leave_balance",
        "itops__search",
        "itops__create_ticket",
    ]


async def test_direct_arm_dispatches_by_first_double_underscore() -> None:
    hr = FakeClient([_tool("search")])
    itops = FakeClient([_tool("search")])
    arm = DirectArm([])
    arm._clients = {"hr": hr, "itops": itops}

    text = await arm.call_upstream("itops__search", {"query": "deploy"})

    assert hr.calls == []
    assert itops.calls == [("search", {"query": "deploy"})]
    assert text == 'search({"query": "deploy"})'


async def test_direct_arm_keeps_double_underscore_in_tool_name() -> None:
    client = FakeClient([_tool("a__b")])
    arm = DirectArm([])
    arm._clients = {"hr": client}

    await arm.call_upstream("hr__a__b", {})

    assert client.calls == [("a__b", {})]


async def test_direct_arm_unknown_prefix_raises_key_error() -> None:
    arm = DirectArm([])
    arm._clients = {"hr": FakeClient([])}

    with pytest.raises(KeyError):
        await arm.call_upstream("nope__search", {})

    with pytest.raises(KeyError):
        await arm.call_upstream("search", {})


async def test_proxy_arm_lists_only_its_own_three_tools() -> None:
    arm = ProxyArm("unused-config.json")
    arm._client = FakeClient(
        [
            _tool("search_mcp_tool"),
            _tool("invoke_this_tool"),
            _tool("check_mcp_status"),
        ]
    )

    converted = await arm.list_openai_tools()

    assert [item["function"]["name"] for item in converted] == [
        "search_mcp_tool",
        "invoke_this_tool",
        "check_mcp_status",
    ]


async def test_proxy_arm_calls_proxy_tool_and_joins_text() -> None:
    client = FakeClient([_tool("invoke_this_tool")])
    arm = ProxyArm("unused-config.json")
    arm._client = client

    text = await arm.call_upstream(
        "invoke_this_tool",
        {"target": "hr__get_leave_balance", "params": {"employee_id": "E1001"}},
    )

    assert client.calls == [
        (
            "invoke_this_tool",
            {"target": "hr__get_leave_balance", "params": {"employee_id": "E1001"}},
        )
    ]
    assert text.startswith("invoke_this_tool(")
    assert "hr__get_leave_balance" in text
