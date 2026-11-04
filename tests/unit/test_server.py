"""7.1/7.3 下游服务与 check_mcp_status 的单测（TDD：先于实现）。"""

from __future__ import annotations

from pathlib import Path

from mcp import Client

from grep_mcp_tools.config import Config, Settings
from grep_mcp_tools.health import HealthRegistry
from grep_mcp_tools.server import ProxyServer, status_text


def make_proxy(
    tmp_path: Path,
    *,
    registry: HealthRegistry | None = None,
) -> ProxyServer:
    config = Config(
        servers={},
        settings=Settings(),
        config_path=tmp_path / "config.json",
    )
    return ProxyServer(
        config,
        registry or HealthRegistry(),
        {},
        index_root=tmp_path / "idx",
    )


async def test_tools_list_exposes_exactly_three_tools(tmp_path: Path) -> None:
    async with Client(make_proxy(tmp_path).mcp) as client:
        listing = await client.list_tools()
        names = {tool.name for tool in listing.tools}
        assert names == {"search_mcp_tool", "invoke_this_tool", "check_mcp_status"}
        for tool in listing.tools:
            assert tool.description and tool.description.strip()

        by_name = {tool.name: tool for tool in listing.tools}
        keywords = by_name["search_mcp_tool"].input_schema["properties"]["keywords"]
        assert keywords["type"] == "array"
        assert keywords["items"]["type"] == "string"
        target = by_name["invoke_this_tool"].input_schema["properties"]["target"]
        assert target["type"] == "string"


async def test_check_mcp_status_empty_config_answer(tmp_path: Path) -> None:
    async with Client(make_proxy(tmp_path).mcp) as client:
        result = await client.call_tool("check_mcp_status", {})
        assert result.is_error is False
        assert "no upstream services configured" in result.content[0].text


async def test_search_through_protocol_empty_keywords(tmp_path: Path) -> None:
    async with Client(make_proxy(tmp_path).mcp) as client:
        result = await client.call_tool("search_mcp_tool", {"keywords": []})
        assert result.is_error is False
        assert "Usage" in result.content[0].text


def test_status_all_healthy_is_quiet_list() -> None:
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    registry.record_live("itops", tool_count=12)
    registry.record_live("audit", tool_count=3)

    text = status_text(registry)

    assert "hr" in text and "itops" in text and "audit" in text
    assert "live" in text
    assert "WARNING" not in text
    assert "tell the user" not in text.lower()


def test_status_with_dead_service_warns_and_tells_agent_to_alert_user() -> None:
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    registry.record_unreachable("dead_http", reason="connection refused")

    text = status_text(registry)

    assert "dead_http" in text
    assert "connection refused" in text
    assert "WARNING" in text
    assert "tell the user" in text.lower()


def test_status_empty_config() -> None:
    assert status_text(HealthRegistry()) == "no upstream services configured"


def test_main_fails_fast_on_config_error(tmp_path: Path, capsys) -> None:
    from grep_mcp_tools.__main__ import main

    code = main(["--config", str(tmp_path / "missing.json")])

    assert code == 2
    captured = capsys.readouterr()
    assert "config error" in captured.err
    assert captured.out == ""


def test_main_fails_fast_on_invalid_json(tmp_path: Path, capsys) -> None:
    from grep_mcp_tools.__main__ import main

    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")

    code = main(["--config", str(bad)])

    assert code == 2
    assert "config error" in capsys.readouterr().err
