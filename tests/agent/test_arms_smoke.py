"""两臂真实进程连接冒烟（不需要 API key）。

拉起 `tests/e2e/demo_servers/` 的 4 个 HTTP/SSE 上游；hr 由各臂自己拉起：
DirectArm 直连 stdio，ProxyArm 经 config 让代理 spawn。
"""

from __future__ import annotations

from pathlib import Path

from tests.agent.arms import DirectArm, ProxyArm
from tests.agent.fleet import (
    build_server_config,
    build_upstream_specs,
    pick_fleet_ports,
    spawn_http_demos,
)
from tests.e2e.harness import write_config

PROXY_TOOL_NAMES = {"search_mcp_tool", "invoke_this_tool", "check_mcp_status"}


def _names(tools: list[dict]) -> list[str]:
    return [item["function"]["name"] for item in tools]


async def test_direct_arm_sees_all_71_upstream_tools() -> None:
    ports = pick_fleet_ports()
    demos = spawn_http_demos(ports)
    try:
        async with DirectArm(build_upstream_specs(ports)) as arm:
            tools = await arm.list_openai_tools()
    finally:
        for demo in reversed(demos):
            demo.stop()

    names = _names(tools)
    assert len(names) == 71, f"expected 71 tools, got {len(names)}: {names}"
    assert len(set(names)) == 71
    assert "hr__search" in names
    assert "itops__search" in names


async def test_proxy_arm_exposes_only_its_three_tools(tmp_path: Path) -> None:
    ports = pick_fleet_ports()
    demos = spawn_http_demos(ports)
    try:
        config_path = write_config(
            tmp_path,
            build_server_config(ports),
            settings={"refresh_interval": 600, "handshake_timeout": 10},
        )
        async with ProxyArm(config_path) as arm:
            tools = await arm.list_openai_tools()
    finally:
        for demo in reversed(demos):
            demo.stop()

    assert set(_names(tools)) == PROXY_TOOL_NAMES
