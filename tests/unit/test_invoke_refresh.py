"""2.3/2.4 invoke 状态面与刷新对齐（TDD：先红后绿）。

- 路由不因启动定格：启动时死 → 假刷新恢复 → invoke 成功
- target 校验随刷新保鲜：新工具可调 / 已删工具拒绝 / 刷新前维持旧校验
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp.types import CallToolResult, TextContent, Tool

from grep_mcp_tools.config import Config, ServerEntry, Settings
from grep_mcp_tools.health import HealthRegistry
from grep_mcp_tools.index import write_tools_index
from grep_mcp_tools.server import ProxyServer


class FakeUpstream:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.client = _FakeClient(handler)
        self.lock = asyncio.Lock()
        self.calls: list[str] = []

    def revive(self) -> None:
        self.client = _FakeClient(self.handler)


class _FakeClient:
    def __init__(self, handler) -> None:
        self._handler = handler

    async def call_tool(self, name: str, arguments: dict | None = None) -> CallToolResult:
        return await self._handler(name, arguments or {})


def text_of(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if hasattr(block, "text"))


def seed_index(tmp_path: Path, server: str, tool_names: list[str]) -> None:
    write_tools_index(
        server,
        [
            Tool(
                name=name,
                description=f"{name} description",
                input_schema={"type": "object", "properties": {}},
            )
            for name in tool_names
        ],
        tmp_path / "idx",
    )


def build_proxy(
    tmp_path: Path, registry: HealthRegistry, upstream: FakeUpstream
) -> ProxyServer:
    config = Config(
        servers={
            "itops": ServerEntry(
                name="itops", transport="http", url="http://127.0.0.1:1/mcp"
            )
        },
        settings=Settings(),
        config_path=tmp_path / "config.json",
    )
    return ProxyServer(config, registry, {"itops": upstream}, index_root=tmp_path / "idx")


async def ok_handler(name: str, args: dict) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=f"ran {name}")])


async def test_invoke_recovers_after_refresh_heals_startup_death(tmp_path: Path) -> None:
    """启动时死 → 刷新自愈恢复（client 从 None 变有值）→ invoke 正常透传。"""
    upstream = FakeUpstream(ok_handler)
    upstream.client = None  # 启动时死
    registry = HealthRegistry()
    registry.record_unreachable("itops", reason="connection refused")
    proxy = build_proxy(tmp_path, registry, upstream)

    denied = await proxy.invoke("itops__search", {})
    assert denied.is_error is True
    assert "unreachable" in text_of(denied)

    # 假刷新轮：连接对象身份不变，client 复活 + 注册表翻转 + 轮尾同步
    upstream.revive()
    registry.record_live("itops", tool_count=1)
    seed_index(tmp_path, "itops", ["search"])
    await proxy.sync_after_refresh()

    healed = await proxy.invoke("itops__search", {"query": "runbook"})
    assert healed.is_error is False
    assert "ran search" in text_of(healed)


async def test_new_tool_callable_after_refresh(tmp_path: Path) -> None:
    """刷新后新增工具：校验通过并透传。"""
    upstream = FakeUpstream(ok_handler)
    registry = HealthRegistry()
    registry.record_live("itops", tool_count=1)
    seed_index(tmp_path, "itops", ["search"])
    proxy = build_proxy(tmp_path, registry, upstream)

    # 上游新增 region_cn_report → 刷新重写索引 + 轮尾重建校验表
    registry.record_live("itops", tool_count=2)
    seed_index(tmp_path, "itops", ["search", "region_cn_report"])
    await proxy.sync_after_refresh()

    result = await proxy.invoke("itops__region_cn_report", {})
    assert result.is_error is False
    assert "ran region_cn_report" in text_of(result)


async def test_removed_tool_rejected_after_refresh(tmp_path: Path) -> None:
    """刷新后已删工具：得"工具不存在"代理级错误，不透传。"""
    upstream = FakeUpstream(ok_handler)
    registry = HealthRegistry()
    registry.record_live("itops", tool_count=2)
    seed_index(tmp_path, "itops", ["search", "legacy_export"])
    proxy = build_proxy(tmp_path, registry, upstream)

    registry.record_live("itops", tool_count=1)
    seed_index(tmp_path, "itops", ["search"])
    await proxy.sync_after_refresh()

    result = await proxy.invoke("itops__legacy_export", {})
    assert result.is_error is True
    text = text_of(result)
    assert "does not exist" in text
    assert "search_mcp_tool" in text


async def test_stale_validation_until_refresh(tmp_path: Path) -> None:
    """刷新前维持旧校验：索引文件已变但轮未到 → 仍按上一轮校验透传。"""
    received: list[str] = []

    async def recorder(name: str, args: dict) -> CallToolResult:
        received.append(name)
        return CallToolResult(
            content=[TextContent(type="text", text="upstream says no such tool")],
            is_error=True,
        )

    upstream = FakeUpstream(recorder)
    registry = HealthRegistry()
    registry.record_live("itops", tool_count=2)
    seed_index(tmp_path, "itops", ["search", "legacy_export"])
    proxy = build_proxy(tmp_path, registry, upstream)

    # 上游已删工具、索引文件已重写，但刷新轮未到达（无 sync_after_refresh）
    seed_index(tmp_path, "itops", ["search"])
    result = await proxy.invoke("itops__legacy_export", {})

    assert received == ["legacy_export"]  # 代理不预判，透传给上游
    assert result.is_error is True  # 上游自己的错误
    assert "upstream says no such tool" in text_of(result)
