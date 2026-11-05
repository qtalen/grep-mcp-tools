"""D 区：description 注入（4 例）。

契约来源：`src/grep_mcp_tools/search.py::render_search_description` 与
`docs/design/batches/B03-e2e-matrix.md`『D description 注入（4 例）』。
下游可见的 `search_mcp_tool` description = 静态触发文案 + 动态服务清单。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Callable

from tests.e2e.harness import DEMO_DIR, RawSession, pick_ports, spawn_demo

if TYPE_CHECKING:
    from tests.e2e.conftest import StdStack, Variant

HR_TOOLS = 8
ITOPS_TOOLS = 12
AUDIT_TOOLS = 3


def _search_description(session: RawSession) -> str:
    """取 `search_mcp_tool` 的 description（tools/list 中的实际文案）。"""
    for tool in session.list_tools():
        if tool.get("name") == "search_mcp_tool":
            description = tool.get("description")
            assert isinstance(description, str) and description
            return description
    raise AssertionError("search_mcp_tool missing from tools/list")


def test_description_lists_live_services(std: "StdStack") -> None:
    """tools/list 的 search_mcp_tool description 含各活服务名与工具数。"""
    description = _search_description(std.session)

    assert "Connected services:" in description
    assert f"hr ({HR_TOOLS} tools)" in description
    assert f"itops ({ITOPS_TOOLS} tools)" in description
    assert f"audit ({AUDIT_TOOLS} tools)" in description


def test_description_degrades_over_limit(launch: Callable[..., "Variant"]) -> None:
    """超上限降级：只列字典序前 N 个服务，并标注总数 (of M)。"""
    itops_port, audit_port = pick_ports(2)
    launch.track_demo(spawn_demo("itops.py", itops_port))
    launch.track_demo(spawn_demo("audit.py", audit_port))
    variant = launch(
        {
            "hr": {"command": sys.executable, "args": [str(DEMO_DIR / "hr.py")]},
            "itops": {"url": f"http://127.0.0.1:{itops_port}/mcp"},
            "audit": {"url": f"http://127.0.0.1:{audit_port}/sse", "type": "sse"},
        },
        settings={
            "description_max_servers": 2,
            "handshake_timeout": 10,
            "refresh_interval": 600,
        },
    )
    assert variant.session is not None
    description = _search_description(variant.session)

    # 字典序前二：audit、hr；itops 被降级隐藏
    assert "Connected services (of 3):" in description
    assert f"audit ({AUDIT_TOOLS} tools)" in description
    assert f"hr ({HR_TOOLS} tools)" in description
    assert "itops" not in description
    assert "unavailable" not in description


def test_description_excludes_fail_soft_services(std: "StdStack") -> None:
    """fail-soft 服务不计入清单，只留 (k unavailable) 计数后缀。"""
    description = _search_description(std.session)

    assert "dead_http" not in description
    assert "(1 unavailable" in description
    # 清单条目数 = 3 个活服务（每个条目都是复数 "tools)" 形态）
    assert description.count(" tools)") == 3


def test_description_empty_config_is_self_consistent(
    launch: Callable[..., "Variant"],
) -> None:
    """空 mcpServers：description 提示无上游配置，且不出现孤儿清单。"""
    variant = launch({})
    assert variant.session is not None
    description = _search_description(variant.session)

    assert "no upstream services configured" in description
    assert "Connected services" not in description
    assert "unavailable" not in description
