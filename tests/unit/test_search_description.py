"""tool-description delta：render_search_description 纯函数全分支（TDD 先红）。

对照 spec 场景：清单注入、超上限 (of M) 降级、(k unavailable) 后缀、
被隐藏服务、空配置/全挂文案。
"""

from __future__ import annotations

from grep_mcp_tools.search import LiveServer, render_search_description, roster_signature

TRIGGER = "Search the indexed tool catalog FIRST when a task may need upstream tools."


def servers(*pairs: tuple[str, int]) -> list[LiveServer]:
    return [LiveServer(name=name, tool_count=count) for name, count in pairs]


def test_roster_line_lists_live_servers_sorted() -> None:
    text = render_search_description(TRIGGER, servers(("hr", 8), ("itops", 12), ("docs", 6)), 0, 15)
    assert TRIGGER in text
    assert "docs (6 tools), hr (8 tools), itops (12 tools)" in text  # 字典序


def test_unavailable_count_suffix_and_pointer() -> None:
    text = render_search_description(TRIGGER, servers(("hr", 8)), 1, 15)
    assert "(1 unavailable" in text
    assert "check_mcp_status" in text


def test_over_limit_shows_first_n_with_of_m() -> None:
    text = render_search_description(
        TRIGGER, servers(("alpha", 1), ("beta", 2), ("gamma", 3)), 0, 2
    )
    assert "alpha (1 tool), beta (2 tools)" in text  # 前 N 字典序
    assert "gamma" not in text
    assert "(of 3)" in text  # 总数标注


def test_empty_config_self_consistent() -> None:
    text = render_search_description(TRIGGER, [], 0, 15)
    assert "no upstream services configured" in text
    assert "unavailable" not in text


def test_all_down_message_distinct_from_empty() -> None:
    text = render_search_description(TRIGGER, [], 5, 15)
    assert "no upstream services currently connected (5 unavailable)" in text
    assert "Run check_mcp_status for details." in text


def test_signature_visible_set_only_beyond_limit() -> None:
    """签名 = description 文本决定因素：超上限时被隐藏服务变化不改变签名。"""
    base = servers(("alpha", 1), ("beta", 2), ("gamma", 3))
    assert roster_signature(base, 0, 15) == roster_signature(base, 0, 15)
    # 未超上限：全集即可见集，任何变化都变签名
    assert roster_signature(base, 0, 15) != roster_signature(
        servers(("alpha", 1), ("beta", 2), ("gamma", 4)), 0, 15
    )
    # 超上限（max=2，gamma 被隐藏）：gamma 工具数变化不变签名
    assert roster_signature(base, 0, 2) == roster_signature(
        servers(("alpha", 1), ("beta", 2), ("gamma", 9)), 0, 2
    )
    # 可见服务或总数或不可达数变化 → 签名变
    assert roster_signature(base, 0, 2) != roster_signature(
        servers(("alpha", 1), ("beta", 3), ("gamma", 3)), 0, 2
    )
    assert roster_signature(base, 0, 2) != roster_signature(base, 1, 2)
