"""F 区规模冒烟（tasks 9.1）：1000+ 工具预算、50KB schema、大索引拒绝路径。

依据：architecture『性能预算』——1000+ 工具索引搜索 <200ms、超量拒绝路径
不做全量格式化且快速返回；批次文档『F 规模冒烟（3 例）』。

计时纪律：只包住 `call_tool` 本身（进程拉起/索引写盘在计时外），先热身一次
再测量，阈值按 Windows 抖动放宽处理（预算 200ms 为断言界）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from tests.e2e.harness import (
    DEMO_DIR,
    free_port,
    read_index_lines,
    spawn_demo,
    wait_until,
)

if TYPE_CHECKING:
    from tests.e2e.conftest import Variant

FLEET_COUNT = 1000

# architecture：1000+ 工具索引搜索 <200ms；拒绝路径同样不做全量格式化。
SEARCH_BUDGET_SECONDS = 0.2
REJECT_BUDGET_SECONDS = 0.2


def _index_lines(index_dir: Path, server: str) -> list[str]:
    """索引文件尚不存在时返回空表（启动窗口内的轮询不会炸）。"""
    try:
        return read_index_lines(index_dir, server)
    except FileNotFoundError:
        return []


def _wait_index_count(
    index_dir: Path, server: str, expected: int, deadline: float = 60.0
) -> list[str]:
    """等 `<server>.jsonl` 落盘且行数到位（计时前置条件）。"""

    def _ready() -> list[str] | None:
        lines = _index_lines(index_dir, server)
        return lines if len(lines) == expected else None

    return wait_until(_ready, deadline=deadline)


def _text_of(result: dict) -> str:
    return "".join(
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict)
    )


def _launch_fleet(launch: Callable[..., "Variant"], count: int = FLEET_COUNT) -> "Variant":
    """拉一个 `--count 1000` 的 fleet_gen 上游并起独享代理。"""
    port = free_port()
    launch.track_demo(spawn_demo("fleet_gen.py", port, ["--count", str(count)]))
    return launch(
        {"fleet_gen": {"url": f"http://127.0.0.1:{port}/mcp"}},
        settings={"handshake_timeout": 30, "refresh_interval": 600},
    )


def test_large_index_search_within_budget(
    launch: Callable[..., "Variant"],
) -> None:
    """1000 工具索引下单次搜索 <200ms：命中少量结果（真实格式化路径，非拒绝）。"""
    variant = _launch_fleet(launch)
    lines = _wait_index_count(variant.index_dir, "fleet_gen", FLEET_COUNT)
    assert len(lines) == FLEET_COUNT
    # 每行都是合法 JSON，且工具名齐全（1000 工具索引真实可用）
    assert json.loads(lines[0])["name"].endswith("_report")

    session = variant.session
    assert session is not None
    keywords = ["region_north"]  # 命中 8 个 region_north_*_report，不触发阈值拒绝

    warmup = _text_of(session.call_tool("search_mcp_tool", {"keywords": keywords}))
    assert "tool(s) matched" in warmup, warmup

    started = time.monotonic()
    result = session.call_tool("search_mcp_tool", {"keywords": keywords})
    elapsed = time.monotonic() - started

    text = _text_of(result)
    assert "region_north_1_report" in text
    assert "Too many matches" not in text
    assert elapsed < SEARCH_BUDGET_SECONDS, (
        f"1000-tool search took {elapsed * 1000:.0f}ms "
        f"(budget {SEARCH_BUDGET_SECONDS * 1000:.0f}ms)"
    )


def test_huge_schema_survives(launch: Callable[..., "Variant"]) -> None:
    """50KB 巨型 schema：索引行仍是合法单行 JSON，可搜可 invoke。"""
    variant = launch(
        {"gizmos": {"command": sys.executable, "args": [str(DEMO_DIR / "gizmos.py")]}},
        settings={"refresh_interval": 600},
    )
    lines = _wait_index_count(variant.index_dir, "gizmos", 5)

    huge_lines = [line for line in lines if json.loads(line)["name"] == "huge_schema"]
    assert len(huge_lines) == 1
    huge_line = huge_lines[0]
    record = json.loads(huge_line)  # 单行合法 JSON
    assert record["server"] == "gizmos"
    assert len(huge_line) >= 50_000
    # 描述里的换行被 JSON 转义承载，物理上仍是一行（read_index_lines 未拆行）
    assert len(lines) == 5

    session = variant.session
    assert session is not None

    text = _text_of(session.call_tool("search_mcp_tool", {"keywords": ["huge"]}))
    assert "gizmos__huge_schema" in text

    result = session.call_tool(
        "invoke_this_tool",
        {"target": "gizmos__huge_schema", "params": {"payload": "x"}},
    )
    assert not result.get("isError")
    assert _text_of(result).strip() == "ok"


def test_large_index_over_threshold_rejects_fast(
    launch: Callable[..., "Variant"],
) -> None:
    """1000 命中 > 阈值 30：直接返回拒绝文本（含命中数）且 <200ms。"""
    variant = _launch_fleet(launch)
    _wait_index_count(variant.index_dir, "fleet_gen", FLEET_COUNT)

    session = variant.session
    assert session is not None
    keywords = ["report"]  # 1000 个 region_*_report 全命中

    warmup = _text_of(session.call_tool("search_mcp_tool", {"keywords": keywords}))
    assert "Too many matches: 1000 tools match [report]" in warmup, warmup

    started = time.monotonic()
    result = session.call_tool("search_mcp_tool", {"keywords": keywords})
    elapsed = time.monotonic() - started

    text = _text_of(result)
    assert "Too many matches: 1000 tools match [report]" in text
    assert "threshold: 30" in text
    assert "No results were listed" in text
    # 拒绝路径不做全量格式化：正文里不出现任何工具 target
    assert "fleet_gen__region_" not in text
    assert elapsed < REJECT_BUDGET_SECONDS, (
        f"over-threshold rejection took {elapsed * 1000:.0f}ms "
        f"(budget {REJECT_BUDGET_SECONDS * 1000:.0f}ms)"
    )
