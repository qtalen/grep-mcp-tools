"""3.2 tools/list 的 search_mcp_tool description 动态含服务清单（TDD 先红）。"""

from __future__ import annotations

from pathlib import Path

from mcp import Client

from grep_mcp_tools.config import Config, Settings
from grep_mcp_tools.health import HealthRegistry
from grep_mcp_tools.server import ProxyServer


def make_proxy(tmp_path: Path, registry: HealthRegistry) -> ProxyServer:
    config = Config(
        servers={},
        settings=Settings(),
        config_path=tmp_path / "config.json",
    )
    return ProxyServer(config, registry, {}, index_root=tmp_path / "idx")


async def test_tools_list_description_contains_roster(tmp_path: Path) -> None:
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    registry.record_live("itops", tool_count=12)
    registry.record_live("docs", tool_count=6)
    registry.record_unreachable("broken", reason="exit code 3")

    async with Client(make_proxy(tmp_path, registry).mcp) as client:
        listing = await client.list_tools()
        by_name = {tool.name: tool for tool in listing.tools}
        description = by_name["search_mcp_tool"].description or ""

    assert "docs (6 tools), hr (8 tools), itops (12 tools)" in description
    assert "(1 unavailable" in description
    assert "check_mcp_status" in description
    assert "broken" not in description  # 被动信道：死服务名不出场


async def test_tools_list_description_empty_config(tmp_path: Path) -> None:
    async with Client(make_proxy(tmp_path, HealthRegistry()).mcp) as client:
        listing = await client.list_tools()
        by_name = {tool.name: tool for tool in listing.tools}
        description = by_name["search_mcp_tool"].description or ""

    assert "no upstream services configured" in description
