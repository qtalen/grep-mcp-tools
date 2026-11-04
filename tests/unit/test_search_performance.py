"""4.5 性能预算单测（契约：1000+ 工具 <200ms；拒绝路径快速返回）。"""

from __future__ import annotations

import time

from grep_mcp_tools.search import search
from tests.unit.search_helpers import make_ctx, row


def build_large_index(count: int = 1200) -> list:
    tools = []
    for i in range(count):
        tools.append(
            row(
                "fleet" if i % 2 else "itops",
                f"region_{i:04d}_report",
                f"Generate the report for region {i} including deployment status",
                input_schema={
                    "type": "object",
                    "properties": {
                        "region_id": {"type": "string", "description": "region identifier"},
                        "dry_run": {"type": "boolean"},
                    },
                    "required": ["region_id"],
                },
                line_no=i + 1,
            )
        )
    return tools


def test_thousand_plus_tools_search_within_budget():
    tools = build_large_index()
    ctx = make_ctx(tools, threshold=2000)  # 放宽阈值，走完整格式化路径
    # 预热一次
    search(["region", "deploy"], ctx)
    started = time.perf_counter()
    response = search(["region", "deploy"], ctx)
    elapsed = time.perf_counter() - started
    assert response.kind == "matches"
    assert len(response.tools) == 1200
    assert elapsed < 0.200, f"search took {elapsed * 1000:.1f}ms, budget is 200ms"


def test_over_threshold_path_skips_formatting():
    tools = build_large_index()
    ctx = make_ctx(tools, threshold=30)
    started = time.perf_counter()
    response = search(["region"], ctx)
    elapsed = time.perf_counter() - started
    assert response.kind == "over_threshold"
    assert response.tools == ()
    assert elapsed < 0.200, f"rejection path took {elapsed * 1000:.1f}ms"
