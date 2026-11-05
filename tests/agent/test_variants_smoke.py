"""消融臂真实进程冒烟（不需要 API key）。

起真实代理（hr + itops + audit + docs + fleet_gen），验证：
- 臂 c 的 `search_mcp_tool` description 不含 `Connected services`；
- 臂 d 的双零路标两条路径（空关键词 / 不可命中关键词）均不含
  `Available services`，同时保留主体文案。
"""

from __future__ import annotations

from pathlib import Path

from tests.agent.arms import ProxyArm
from tests.agent.fleet import (
    build_server_config,
    pick_fleet_ports,
    spawn_http_demos,
)
from tests.agent.variants import AblatedArm
from tests.e2e.harness import write_config


async def test_arm_c_description_has_no_service_injection(tmp_path: Path) -> None:
    ports = pick_fleet_ports()
    demos = spawn_http_demos(ports)
    try:
        config_path = write_config(
            tmp_path,
            build_server_config(ports),
            settings={"refresh_interval": 600, "handshake_timeout": 10},
        )
        async with ProxyArm(config_path) as inner:
            arm = AblatedArm(inner, strip_description=True)
            tools = await arm.list_openai_tools()
    finally:
        for demo in reversed(demos):
            demo.stop()

    search = next(
        item for item in tools if item["function"]["name"] == "search_mcp_tool"
    )
    description = search["function"]["description"]

    assert description
    assert "Connected services" not in description
    assert "no upstream services" not in description


async def test_arm_d_strips_signpost_on_both_paths(tmp_path: Path) -> None:
    ports = pick_fleet_ports()
    demos = spawn_http_demos(ports)
    try:
        config_path = write_config(
            tmp_path,
            build_server_config(ports),
            settings={"refresh_interval": 600, "handshake_timeout": 10},
        )
        async with ProxyArm(config_path) as inner:
            arm = AblatedArm(inner, strip_signpost=True)
            empty = await arm.call_upstream("search_mcp_tool", {"keywords": []})
            unmatched = await arm.call_upstream(
                "search_mcp_tool", {"keywords": ["zzz-unmatched-zzz"]}
            )
    finally:
        for demo in reversed(demos):
            demo.stop()

    assert empty.startswith("Usage: search_mcp_tool(")
    assert "Available services" not in empty
    assert unmatched.startswith("No tools matched")
    assert "Available services" not in unmatched
