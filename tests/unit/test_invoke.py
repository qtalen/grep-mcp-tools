"""7.2 invoke 路由的单测（TDD：先于实现）。

Fake 上游：只实现 ProxyServer 依赖的接口（client.call_tool + 每上游锁），
聚焦路由/错误两分/串行化语义；真实传输的透传由 probe/E2E 覆盖。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from mcp import MCPError
from mcp.types import CallToolResult, TextContent, Tool

from grep_mcp_tools.config import Config, ServerEntry, Settings
from grep_mcp_tools.health import HealthRegistry
from grep_mcp_tools.index import write_tools_index
from grep_mcp_tools.server import ProxyServer


class FakeClient:
    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict | None = None) -> CallToolResult:
        self.calls.append((name, arguments or {}))
        return await self._handler(name, arguments or {})


class FakeUpstream:
    def __init__(self, handler) -> None:
        self.client = FakeClient(handler)
        self.lock = asyncio.Lock()


def text_of(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if hasattr(block, "text"))


def build_proxy(
    tmp_path: Path,
    *,
    registry: HealthRegistry,
    connections: dict,
    config_servers: dict[str, ServerEntry] | None = None,
) -> ProxyServer:
    servers = config_servers or {
        name: ServerEntry(name=name, transport="stdio", command="python")
        for name in connections
    }
    config = Config(
        servers=servers,
        settings=Settings(),
        config_path=tmp_path / "config.json",
    )
    return ProxyServer(config, registry, connections, index_root=tmp_path / "idx")


def seed_index(tmp_path: Path, server: str, tool_names: list[str]) -> None:
    tools = [
        Tool(
            name=name,
            description=f"{name} description",
            input_schema={"type": "object", "properties": {}},
        )
        for name in tool_names
    ]
    write_tools_index(server, tools, tmp_path / "idx")


async def test_invoke_passes_through_result_and_arguments(tmp_path: Path) -> None:
    received: dict = {}

    async def handler(name: str, args: dict) -> CallToolResult:
        received["name"] = name
        received["args"] = args
        return CallToolResult(content=[TextContent(type="text", text="upstream ok")])

    registry = HealthRegistry()
    registry.record_live("hr", tool_count=2)
    seed_index(tmp_path, "hr", ["get_employee", "run__deploy"])
    upstream = FakeUpstream(handler)
    proxy = build_proxy(tmp_path, registry=registry, connections={"hr": upstream})

    deep_args = {"employee_id": "E-1", "nested": {"a": [1, 2, {"b": None}]}, "extra": True}
    result = await proxy.invoke("hr__get_employee", deep_args)

    assert result.is_error is False
    assert text_of(result) == "upstream ok"
    assert received["name"] == "get_employee"
    assert received["args"] == deep_args


async def test_target_splits_at_first_double_underscore(tmp_path: Path) -> None:
    received: dict = {}

    async def handler(name: str, args: dict) -> CallToolResult:
        received["name"] = name
        return CallToolResult(content=[TextContent(type="text", text="ok")])

    registry = HealthRegistry()
    registry.record_live("itops", tool_count=1)
    seed_index(tmp_path, "itops", ["run__deploy"])
    proxy = build_proxy(
        tmp_path, registry=registry, connections={"itops": FakeUpstream(handler)}
    )

    result = await proxy.invoke("itops__run__deploy", {})

    assert result.is_error is False
    assert received["name"] == "run__deploy"


async def test_bad_target_format_is_proxy_error(tmp_path: Path) -> None:
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=1)
    seed_index(tmp_path, "hr", ["get_employee"])
    proxy = build_proxy(
        tmp_path,
        registry=registry,
        connections={"hr": FakeUpstream(lambda n, a: None)},
    )

    result = await proxy.invoke("no_separator", {})

    assert result.is_error is True
    text = text_of(result)
    assert "invalid target" in text.lower()
    assert "search_mcp_tool" in text


async def test_unknown_server_is_proxy_error(tmp_path: Path) -> None:
    registry = HealthRegistry()
    proxy = build_proxy(tmp_path, registry=registry, connections={})

    result = await proxy.invoke("ghost__tool", {})

    assert result.is_error is True
    text = text_of(result)
    assert "not configured" in text
    assert "search_mcp_tool" in text


async def test_unknown_tool_on_known_server_is_proxy_error(tmp_path: Path) -> None:
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=1)
    seed_index(tmp_path, "hr", ["get_employee"])
    proxy = build_proxy(
        tmp_path,
        registry=registry,
        connections={"hr": FakeUpstream(lambda n, a: None)},
    )

    result = await proxy.invoke("hr__no_such_tool", {})

    assert result.is_error is True
    text = text_of(result)
    assert "does not exist" in text
    assert "search_mcp_tool" in text


async def test_configured_but_unreachable_is_self_contained_error(tmp_path: Path) -> None:
    registry = HealthRegistry()
    registry.record_unreachable("dead_http", reason="connection refused")
    proxy = build_proxy(
        tmp_path,
        registry=registry,
        connections={},
        config_servers={
            "dead_http": ServerEntry(
                name="dead_http", transport="http", url="http://127.0.0.1:1/mcp"
            )
        },
    )

    result = await proxy.invoke("dead_http__tool", {})

    assert result.is_error is True
    text = text_of(result)
    assert "unreachable" in text.lower()
    assert "connection refused" in text  # 自包含原因，不要求另查状态
    assert "not configured" not in text  # 与"服务不在 config"可区分
    assert "check_mcp_status" in text


async def test_upstream_error_result_passes_through_untouched(tmp_path: Path) -> None:
    async def handler(name: str, args: dict) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text="boom: upstream exploded")],
            is_error=True,
        )

    registry = HealthRegistry()
    registry.record_live("itops", tool_count=1)
    seed_index(tmp_path, "itops", ["boom"])
    proxy = build_proxy(
        tmp_path, registry=registry, connections={"itops": FakeUpstream(handler)}
    )

    result = await proxy.invoke("itops__boom", {})

    assert result.is_error is True
    assert text_of(result) == "boom: upstream exploded"
    assert "Proxy error" not in text_of(result)


async def test_upstream_mcp_error_is_reraised_as_protocol_error(tmp_path: Path) -> None:
    async def handler(name: str, args: dict) -> CallToolResult:
        raise MCPError(-32602, "unknown tool from upstream")

    registry = HealthRegistry()
    registry.record_live("hr", tool_count=1)
    seed_index(tmp_path, "hr", ["weird"])
    proxy = build_proxy(
        tmp_path, registry=registry, connections={"hr": FakeUpstream(handler)}
    )

    with pytest.raises(MCPError):
        await proxy.invoke("hr__weird", {})


async def test_same_upstream_invokes_are_serialized(tmp_path: Path) -> None:
    active = 0
    max_active = 0

    async def handler(name: str, args: dict) -> CallToolResult:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.05)
            return CallToolResult(content=[TextContent(type="text", text="ok")])
        finally:
            active -= 1

    registry = HealthRegistry()
    registry.record_live("hr", tool_count=1)
    seed_index(tmp_path, "hr", ["slow"])
    proxy = build_proxy(
        tmp_path, registry=registry, connections={"hr": FakeUpstream(handler)}
    )

    results = await asyncio.gather(
        proxy.invoke("hr__slow", {}),
        proxy.invoke("hr__slow", {}),
    )

    assert all(r.is_error is False for r in results)
    assert max_active == 1


async def test_slow_tool_is_not_cut_off_by_proxy(tmp_path: Path) -> None:
    """D-06：代理不设 invoke 超时，慢工具必须正常返回。"""

    async def slow_handler(name: str, args: dict) -> CallToolResult:
        await asyncio.sleep(0.05)
        return CallToolResult(content=[TextContent(type="text", text="slow ok")])

    registry = HealthRegistry()
    registry.record_live("hr", tool_count=1)
    seed_index(tmp_path, "hr", ["slow"])
    proxy = build_proxy(
        tmp_path, registry=registry, connections={"hr": FakeUpstream(slow_handler)}
    )

    result = await proxy.invoke("hr__slow", {})

    assert result.is_error is False
    assert text_of(result) == "slow ok"


async def test_cross_upstream_invokes_run_concurrently(tmp_path: Path) -> None:
    async def slow_handler(name: str, args: dict) -> CallToolResult:
        await asyncio.sleep(0.1)
        return CallToolResult(content=[TextContent(type="text", text="ok")])

    registry = HealthRegistry()
    registry.record_live("hr", tool_count=1)
    registry.record_live("itops", tool_count=1)
    seed_index(tmp_path, "hr", ["slow"])
    seed_index(tmp_path, "itops", ["slow"])
    proxy = build_proxy(
        tmp_path,
        registry=registry,
        connections={
            "hr": FakeUpstream(slow_handler),
            "itops": FakeUpstream(slow_handler),
        },
    )

    started = time.perf_counter()
    results = await asyncio.gather(
        proxy.invoke("hr__slow", {}),
        proxy.invoke("itops__slow", {}),
    )
    elapsed = time.perf_counter() - started

    assert all(r.is_error is False for r in results)
    assert elapsed < 0.18, f"cross-upstream invokes serialized (took {elapsed:.3f}s)"
