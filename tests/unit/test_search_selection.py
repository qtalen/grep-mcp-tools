"""4.2 AND/OR 优先级、阈值、排序、样品数的单测（TDD：先于实现）。"""

from __future__ import annotations

from grep_mcp_tools.search import search
from tests.unit.search_helpers import make_ctx, row


def _leave_tools() -> list:
    return [
        row("hr", "get_leave", "query leave balance"),
        row("hr", "apply_leave", "submit a leave request", line_no=2),
        row("itops", "deploy_leave", "deploy leave service"),
        row("itops", "unrelated", "ticket queue", line_no=2),
    ]


def test_and_returns_only_tools_matching_all_keywords():
    response = search(["hr", "leave"], make_ctx(_leave_tools()))
    assert response.kind == "matches"
    assert {t.name for t in response.tools} == {"get_leave", "apply_leave"}


def test_threshold_boundary_inclusive():
    tools = [row("s", f"tool_{i}", "region report", line_no=i + 1) for i in range(3)]
    response = search(["region"], make_ctx(tools, threshold=3))
    assert response.kind == "matches"
    assert len(response.tools) == 3


def test_over_threshold_rejects_with_count():
    tools = [row("s", f"tool_{i}", "region report", line_no=i + 1) for i in range(4)]
    response = search(["region"], make_ctx(tools, threshold=3))
    assert response.kind == "over_threshold"
    assert response.total_matches == 4
    assert response.tools == ()  # 拒绝路径不做全量格式化
    assert "4" in response.text
    assert "Narrow" in response.text or "narrow" in response.text


def test_threshold_override_allows_previous_rejection():
    tools = [row("s", f"tool_{i}", "region report", line_no=i + 1) for i in range(4)]
    response = search(["region"], make_ctx(tools, threshold=5))
    assert response.kind == "matches"
    assert len(response.tools) == 4


def _or_tools() -> list:
    # 3 个关键词、无工具全命中（AND=0）：
    # two_of 命中 2 词，其余各命中 1 词——OR 排序差异可见
    return [
        row("b", "two_of", "kubernetes deploy guide", line_no=1),
        row("a", "deploy_only_1", "deploy guide", line_no=2),
        row("a", "deploy_only_2", "deploy guide again", line_no=5),
        row("b", "kube_only", "kubernetes guide", line_no=2),
    ]


def test_or_fallback_returns_sample_with_partial_marker():
    tools = [row("s", f"d_{i}", "deploy guide", line_no=i + 1) for i in range(15)]
    response = search(["kubernetes", "deploy"], make_ctx(tools, fallback_sample_size=10))
    assert response.kind == "partial"
    assert len(response.tools) == 10
    assert response.total_matches == 15
    assert "Partial match" in response.text
    assert "Showing 10 of 15" in response.text


def test_or_sort_by_match_count_desc_then_position():
    response = search(
        ["kubernetes", "deploy", "helm"], make_ctx(_or_tools(), fallback_sample_size=10)
    )
    assert response.kind == "partial"
    # "two_of" 命中 2 词排第一；其余命中 1 词按（服务文件名, 行号）
    assert [t.name for t in response.tools] == [
        "two_of",
        "deploy_only_1",
        "deploy_only_2",
        "kube_only",
    ]


def test_and_sort_is_pure_position_order():
    tools = [
        row("b", "x", "leave", line_no=2),
        row("a", "y", "leave", line_no=9),
        row("a", "z", "leave", line_no=1),
        row("c", "w", "leave", line_no=1),
    ]
    response = search(["leave"], make_ctx(tools))
    assert [(t.server, t.name) for t in response.tools] == [
        ("a", "z"),
        ("a", "y"),
        ("b", "x"),
        ("c", "w"),
    ]


def test_duplicate_keywords_counted_once():
    tools = [
        row("a", "one", "deploy kubernetes", line_no=1),
        row("b", "two", "deploy only", line_no=1),
    ]
    # 大小写/空白变体视为同一关键词（去重后 deploy/kubernetes/helm）：
    # one 命中 2 词、two 命中 1 词；无工具全命中 → OR 兜底
    response = search(["Deploy", " deploy ", "kubernetes", "helm"], make_ctx(tools))
    assert response.kind == "partial"
    assert [t.name for t in response.tools] == ["one", "two"]


def test_and_beats_or_when_both_exist():
    tools = [
        row("a", "both", "kubernetes deploy", line_no=1),
        row("b", "one", "deploy only", line_no=1),
    ]
    response = search(["kubernetes", "deploy"], make_ctx(tools))
    assert response.kind == "matches"
    assert [t.name for t in response.tools] == ["both"]
