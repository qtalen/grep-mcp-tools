"""B03 B 区：搜索语义（tasks 5.1 / 5.2，约 20 例）。

契约来源：`docs/design/architecture.md`『读路径』与『稳定契约』
（AND/OR 排序、双零路标、阈值、匹配域=索引行原文）。

- 共享 fixture `std`（hr + itops + audit + dead_http，23 工具）承载不依赖
  变体的用例；其中 `std` 只读，不做任何写操作。
- 变体（阈值、全挂、陈旧索引、gizmos）一律走 `launch`，每个变体独立进程。

代理生成的提示文案为英文（架构约定），断言按实际文案锁定现状。
"""

from __future__ import annotations

import json
import re
import sys
from typing import TYPE_CHECKING

from tests.e2e.harness import DEMO_DIR, free_port, read_index_lines, spawn_demo

if TYPE_CHECKING:
    from typing import Callable

    from tests.e2e.conftest import StdStack, Variant
    from tests.e2e.harness import RawSession

# 每条结果的首段是 target（可复制），可能紧跟 " [destructive]" 标记。
_TARGET_LINE = re.compile(r"^([A-Za-z0-9_-]+__[A-Za-z0-9_-]+)", re.MULTILINE)


def _search(session: "RawSession", keywords: list[str]) -> str:
    """调 search_mcp_tool 取纯文本响应。"""
    return session.call_tool_text("search_mcp_tool", {"keywords": keywords})


def _targets(text: str) -> list[str]:
    """按出现顺序抽取响应中的 target 串（每个工具条目首段）。"""
    return _TARGET_LINE.findall(text)


def _itops_variant(launch: "Callable[..., Variant]", threshold: int) -> "Variant":
    """只含 itops 的变体（itops 有 4 个 region 工具，正好压阈值边界）。"""
    port = free_port()
    launch.track_demo(spawn_demo("itops.py", port))
    return launch(
        {"itops": {"url": f"http://127.0.0.1:{port}/mcp"}},
        {"threshold": threshold, "handshake_timeout": 5, "refresh_interval": 600},
    )


def _gizmos_variant(launch: "Callable[..., Variant]") -> "Variant":
    """只含 gizmos 的变体：中文描述 / 50KB 参数描述 / 特殊形态描述。"""
    return launch(
        {"gizmos": {"command": sys.executable, "args": [str(DEMO_DIR / "gizmos.py")]}},
        {"handshake_timeout": 10, "refresh_interval": 600},
    )


# ---------- 5.1 共享 fixture：匹配 / 排序 / 格式 ----------


def test_single_word_hit_leave(std: "StdStack") -> None:
    """单词命中：搜 leave 得 hr 的请假工具，且只有 hr 命中。"""
    text = _search(std.session, ["leave"])
    assert text.startswith("3 tool(s) matched [leave]:")
    assert _targets(text) == [
        "hr__get_leave_balance",
        "hr__apply_leave",
        "hr__approve_leave",
    ]


def test_multiword_and_returns_scope_intersection(std: "StdStack") -> None:
    """多词 AND：服务名+词（hr leave）收窄到该服务内，leave approve 收窄到单词工具。"""
    scoped = _search(std.session, ["hr", "leave"])
    assert scoped.startswith("3 tool(s) matched [hr, leave]:")
    assert _targets(scoped) == [
        "hr__get_leave_balance",
        "hr__apply_leave",
        "hr__approve_leave",
    ]

    narrowed = _search(std.session, ["leave", "approve"])
    assert narrowed.startswith("1 tool(s) matched [leave, approve]:")
    assert _targets(narrowed) == ["hr__approve_leave"]


def test_or_fallback_partial_marked_and_ranked(std: "StdStack") -> None:
    """OR 兜底：AND=0 时标注 partial match，同分并列按（服务名, 行号）位置序。"""
    # "leave" 与 "ticket" 无任何工具同时命中 → AND=0，OR 命中 5 条且均为 1 词。
    text = _search(std.session, ["leave", "ticket"])
    assert text.startswith(
        "Partial match: no tool matched all keywords [leave, ticket]."
    )
    assert "Showing 5 of 5 tools matching at least one keyword" in text
    assert "sorted by match count (best first)" in text
    assert _targets(text) == [
        "hr__get_leave_balance",
        "hr__apply_leave",
        "hr__approve_leave",
        "itops__create_ticket",
        "itops__list_tickets",
    ]


def test_or_fallback_orders_by_matched_keyword_count(std: "StdStack") -> None:
    """OR 兜底主键=命中词数降序：2 词命中的 get_logs 排在 1 词命中之前。"""
    first = _search(std.session, ["service", "ticket", "log"])
    assert first.startswith(
        "Partial match: no tool matched all keywords [service, ticket, log]."
    )
    targets = _targets(first)
    # itops__get_logs 同时命中 service + log（2 词），其余仅 1 词。
    assert targets[0] == "itops__get_logs"
    assert len(targets) == 6

    # 同分排序确定：同一查询连续两次结果完全一致。
    assert _search(std.session, ["service", "ticket", "log"]) == first


def test_dual_zero_signpost_lists_services(std: "StdStack") -> None:
    """双零路标：无匹配 → live 服务清单（名+工具数）+ 指针，不返回工具条目。"""
    text = _search(std.session, ["zzz"])
    assert text.startswith("No tools matched [zzz].")
    assert "Available services:" in text
    assert "- audit: 3 tools" in text
    assert "- hr: 8 tools" in text
    assert "- itops: 12 tools" in text
    assert '(1 unavailable -- run check_mcp_status for details)' in text
    assert "check_mcp_status" in text
    assert _targets(text) == []


def test_signpost_unavailable_count_without_dead_name(std: "StdStack") -> None:
    """路标只给不可达计数行与指针：不含死服务名，也不含失败原因（被动信道纪律）。"""
    text = _search(std.session, ["zzz"])
    assert "(1 unavailable" in text
    assert "check_mcp_status" in text
    assert "dead_http" not in text
    assert "refused" not in text.lower()
    assert "error" not in text.lower()


# ---------- 5.2 变体：全挂 / 陈旧索引 / 阈值 ----------


def test_all_upstreams_unreachable_warns(
    launch: "Callable[..., Variant]",
) -> None:
    """全部上游不可达：搜索给警告语气 + 强指针，不给全量服务清单。"""
    dead_port = free_port()
    variant = launch(
        {"dead_only": {"url": f"http://127.0.0.1:{dead_port}/mcp"}},
        {"handshake_timeout": 2, "refresh_interval": 600},
    )
    text = _search(variant.session, ["leave"])
    assert "NO upstream services are currently available" in text
    assert "(1 configured service(s) unreachable)" in text
    assert "Run check_mcp_status to see which services failed and why." in text
    # 强指针而非清单：不列服务名/工具数，也不列死服务名与原因。
    assert "Available services:" not in text
    assert "dead_only" not in text
    assert _targets(text) == []


def test_stale_index_file_isolated(
    tmp_path, launch: "Callable[..., Variant]"
) -> None:
    """陈旧索引隔离：预植入的死服务 jsonl 不被搜索加载，live 索引照常可搜。"""
    stale_dir = tmp_path / "stale_index"
    stale_dir.mkdir(parents=True, exist_ok=True)
    ghost = {
        "server": "dead_srv",
        "name": "ghost_tool",
        "title": None,
        "description": "ghost tool that must never be searchable",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": None,
    }
    (stale_dir / "dead_srv.jsonl").write_text(
        json.dumps(ghost, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    dead_port = free_port()
    variant = launch(
        {
            "hr": {"command": sys.executable, "args": [str(DEMO_DIR / "hr.py")]},
            "dead_srv": {"url": f"http://127.0.0.1:{dead_port}/mcp"},
        },
        {
            "index_dir": str(stale_dir),
            # stdio 上游冷启动（导入 SDK）会吃掉几秒，这里不能压到 2s。
            "handshake_timeout": 10,
            "refresh_interval": 600,
        },
    )

    # 死服务从启动起不可达 → 其陈旧索引不参与搜索：双零路标。
    ghost_text = _search(variant.session, ["ghost"])
    assert ghost_text.startswith("No tools matched [ghost].")
    assert "ghost_tool" not in ghost_text
    assert "dead_srv" not in ghost_text
    assert "- hr: 8 tools" in ghost_text

    # 预植入文件仍在盘上，只是不被本轮加载。
    assert (stale_dir / "dead_srv.jsonl").is_file()

    # live 服务索引照常可搜。
    leave_text = _search(variant.session, ["leave"])
    assert leave_text.startswith("3 tool(s) matched [leave]:")
    assert "hr__apply_leave" in leave_text


def test_threshold_at_limit_returns(
    launch: "Callable[..., Variant]",
) -> None:
    """阈值 T 返回：命中数 = 阈值（4）时返回完整列表。"""
    variant = _itops_variant(launch, threshold=4)
    text = _search(variant.session, ["region"])
    assert text.startswith("4 tool(s) matched [region]:")
    assert "(threshold" not in text
    assert _targets(text) == [
        "itops__region_deploy_status",
        "itops__region_log_summary",
        "itops__region_capacity",
        "itops__region_incident_digest",
    ]


def test_threshold_over_limit_rejects(
    launch: "Callable[..., Variant]",
) -> None:
    """阈值 T+1 拒绝：命中 4 > 阈值 3 → 只报命中数与收窄提示，不列结果。"""
    variant = _itops_variant(launch, threshold=3)
    text = _search(variant.session, ["region"])
    assert "Too many matches: 4 tools match [region] (threshold: 3)." in text
    assert "No results were listed." in text
    assert "Narrow the search: add more keywords, or include a server name" in text
    assert '(e.g. "hr leave")' in text
    assert _targets(text) == []


def test_threshold_raised_returns_previous_rejection(
    launch: "Callable[..., Variant]",
) -> None:
    """阈值调高后原拒返回：同一 region 查询在 threshold=4 变体下返回全量。"""
    variant = _itops_variant(launch, threshold=4)
    text = _search(variant.session, ["region"])
    assert "Too many matches" not in text
    assert text.startswith("4 tool(s) matched [region]:")
    assert len(_targets(text)) == 4


# ---------- 5.1 共享 fixture：大小写 / 子串 / 作用域 / 参数描述 / 边界 ----------


def test_case_insensitive_three_forms(std: "StdStack") -> None:
    """大小写三态：leave / LEAVE / Leave 结果完全一致。"""
    lower = _search(std.session, ["leave"])
    upper = _search(std.session, ["LEAVE"])
    mixed = _search(std.session, ["Leave"])
    assert lower.startswith("3 tool(s) matched [leave]:")
    assert lower == upper == mixed


def test_substring_matches_inside_word(std: "StdStack") -> None:
    """子串非词边界：eav（leave 的子串）照样命中三个 leave 工具。"""
    text = _search(std.session, ["eav"])
    assert text.startswith("3 tool(s) matched [eav]:")
    assert _targets(text) == [
        "hr__get_leave_balance",
        "hr__apply_leave",
        "hr__approve_leave",
    ]


def test_server_name_scopes_results(std: "StdStack") -> None:
    """服务名作用域：itops deploy 只出 itops 的部署工具（含描述里 deployment 的 search）。"""
    text = _search(std.session, ["itops", "deploy"])
    assert text.startswith("4 tool(s) matched [itops, deploy]:")
    targets = _targets(text)
    assert targets == [
        "itops__search",
        "itops__deploy_service",
        "itops__rollback_deploy",
        "itops__region_deploy_status",
    ]
    assert all(target.startswith("itops__") for target in targets)
    assert "hr__" not in text


def test_keyword_matches_parameter_description(
    launch: "Callable[..., Variant]",
) -> None:
    """关键词只在参数描述里也命中：gizmos.huge_schema 的 50KB 参数描述可被搜到。"""
    variant = _gizmos_variant(launch)
    keyword = "x" * 10
    text = _search(variant.session, [keyword])
    assert text.startswith("1 tool(s) matched [xxxxxxxxxx]:")
    assert _targets(text) == ["gizmos__huge_schema"]
    # 命中来自参数描述，不是工具名/工具描述。
    assert keyword not in "gizmos__huge_schema"


def test_empty_keywords_usage_and_service_hint(std: "StdStack") -> None:
    """空关键词 []：用法提示 + 服务方向（live 名单 + 不可达计数）。"""
    text = _search(std.session, [])
    assert "Usage: search_mcp_tool(keywords:" in text
    assert "- audit: 3 tools" in text
    assert "- hr: 8 tools" in text
    assert "- itops: 12 tools" in text
    assert "(1 unavailable -- run check_mcp_status" in text
    assert _targets(text) == []


def test_regex_metacharacters_treated_as_literals(std: "StdStack") -> None:
    """正则元字符按字面：region_.* / (leave 不解释为正则，不报错、无命中。"""
    dotted = _search(std.session, ["region_.*"])
    assert dotted.startswith("No tools matched [region_.*].")
    assert _targets(dotted) == []

    paren = _search(std.session, ["(leave"])
    assert paren.startswith("No tools matched [(leave].")
    assert _targets(paren) == []

    # 对照：去掉元字符后 region_ 前缀按字面照常命中。
    prefix = _search(std.session, ["region_"])
    assert prefix.startswith("6 tool(s) matched [region_]:")


def test_chinese_emoji_and_overlong_keywords(
    launch: "Callable[..., Variant]",
) -> None:
    """中文可命中中文描述；emoji 与超长关键词落到双零路标而不崩溃。"""
    variant = _gizmos_variant(launch)

    chinese = _search(variant.session, ["中文"])
    assert chinese.startswith("2 tool(s) matched [中文]:")
    assert _targets(chinese) == ["gizmos__chinese_echo", "gizmos__multiline_quote"]

    emoji = _search(variant.session, ["🚀"])
    assert emoji.startswith("No tools matched [🚀].")
    assert "Available services:" in emoji

    overlong = _search(variant.session, ["q" * 4000])
    assert overlong.startswith("No tools matched [qqq")
    assert "Available services:" in overlong


def test_result_format_target_params_and_destructive_marker(std: "StdStack") -> None:
    """返回格式：可复制 target、参数类型、[destructive] 标记不拼进 target。"""
    text = _search(std.session, ["leave"])

    # target 独占条目首段，参数类型单独一行；同一响应内 target 可原样复制。
    assert "params: employee_id (string), days (integer), reason (string)" in text
    assert "params: request_id (string)" in text

    # destructive 条目的首段 = target + 空格 + 标记（标记不在 target 内）。
    first_segments = [
        line.split(" - ", 1)[0]
        for line in text.splitlines()
        if line.startswith("hr__approve_leave")
    ]
    assert first_segments == ["hr__approve_leave [destructive]"]
    assert text.count("[destructive]") == 1
    assert "hr__approve_leave[destructive]" not in text

    # target 抽取不含标记，说明标记不在可复制串内。
    assert "[destructive]" not in " ".join(_targets(text))
    assert set(_targets(text)) == {
        "hr__get_leave_balance",
        "hr__apply_leave",
        "hr__approve_leave",
    }


def test_equal_score_ordering_is_deterministic(std: "StdStack") -> None:
    """同分排序确定：AND 下退化为（服务文件名, 行号）位置序，两次结果一致。"""
    first = _search(std.session, ["region"])
    second = _search(std.session, ["region"])
    assert first == second
    assert first.startswith("6 tool(s) matched [region]:")
    assert _targets(first) == [
        "audit__region_audit_export",
        "hr__region_hr_report",
        "itops__region_deploy_status",
        "itops__region_log_summary",
        "itops__region_capacity",
        "itops__region_incident_digest",
    ]


def test_string_matches_almost_everything(std: "StdStack") -> None:
    """搜 string 命中一切（已知噪音锁现状）：std 23 工具中 20 条含 string。"""
    index_lines: list[str] = []
    for server in ("audit", "hr", "itops"):
        index_lines.extend(read_index_lines(std.index_dir, server))
    expected = sum(1 for line in index_lines if "string" in line.casefold())
    # 锁定现状：仅有 3 个无参工具（firehose/boom/audit_trail_retention）不含 string。
    assert len(index_lines) == 23
    assert expected == 20

    text = _search(std.session, ["string"])
    assert text.startswith(f"{expected} tool(s) matched [string]:")
    assert len(_targets(text)) == expected
    assert "threshold" not in text
