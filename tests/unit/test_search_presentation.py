"""4.3 路标/空关键词/结果格式的单测（TDD：先于实现）。"""

from __future__ import annotations

from grep_mcp_tools.search import search
from tests.unit.search_helpers import make_ctx, row

LIVE = [("hr", 8), ("itops", 12), ("audit", 3)]


def test_signpost_lists_live_services_with_counts():
    response = search(["zzz_no_match"], make_ctx([], live=LIVE, unavailable_count=1))
    assert response.kind == "signpost"
    for name, count in LIVE:
        assert f"- {name}: {count} tools" in response.text


def test_signpost_has_unavailable_count_line_and_pointer():
    response = search(["zzz_no_match"], make_ctx([], live=LIVE, unavailable_count=1))
    assert "(1 unavailable -- run check_mcp_status" in response.text


def test_signpost_never_leaks_dead_names_or_reasons():
    response = search(
        ["zzz_no_match"],
        make_ctx([], live=LIVE, unavailable_count=2),
    )
    assert "dead_http" not in response.text
    assert "connection refused" not in response.text.lower()


def test_signpost_all_unavailable_is_warning_with_strong_pointer():
    response = search(["zzz"], make_ctx([], live=[], unavailable_count=4))
    assert response.kind == "signpost"
    assert "check_mcp_status" in response.text
    lowered = response.text.lower()
    assert "no upstream services" in lowered or "no services" in lowered
    assert "4" in response.text  # 不可达计数在场，但无明细


def test_signpost_zero_tool_service_listed_as_zero():
    response = search(["zzz"], make_ctx([], live=[("empty_srv", 0)], unavailable_count=0))
    assert "empty_srv" in response.text
    assert "0" in response.text


def test_empty_keywords_usage_and_directions():
    response = search([], make_ctx([], live=LIVE, unavailable_count=1))
    assert response.kind == "empty_keywords"
    assert "usage" in response.text.lower()
    for name, _count in LIVE:
        assert name in response.text
    assert "1 unavailable" in response.text


def test_result_entry_contains_copyable_target_and_description():
    tools = [
        row(
            "hr",
            "get_employee",
            "Fetch the employee record",
            input_schema={
                "type": "object",
                "properties": {
                    "employee_id": {"type": "string"},
                    "include_leave": {"type": "boolean"},
                },
                "required": ["employee_id"],
            },
        )
    ]
    response = search(["employee"], make_ctx(tools))
    assert response.kind == "matches"
    assert "hr__get_employee" in response.text
    assert "Fetch the employee record" in response.text
    assert "employee_id" in response.text
    assert "string" in response.text
    assert "boolean" in response.text
    assert "optional" in response.text  # include_leave 非必填


def test_destructive_tool_marked():
    tools = [
        row(
            "itops",
            "delete_cluster",
            "Delete a cluster permanently",
            annotations={"destructiveHint": True, "readOnlyHint": False},
        )
    ]
    response = search(["delete"], make_ctx(tools))
    assert "[destructive]" in response.text


def test_non_destructive_tool_not_marked():
    tools = [row("hr", "read_only", "Read a record", annotations={"readOnlyHint": True})]
    response = search(["record"], make_ctx(tools))
    assert "[destructive]" not in response.text


def test_tool_without_params_renders_none():
    tools = [row("hr", "ping", "health check")]
    response = search(["health"], make_ctx(tools))
    assert "params: none" in response.text


def test_empty_config_signpost_says_not_configured():
    response = search(["anything"], make_ctx([], live=[], unavailable_count=0))
    assert response.kind == "signpost"
    assert "No upstream services are configured" in response.text
    assert "0 configured" not in response.text


def test_empty_config_empty_keywords_says_not_configured():
    response = search([], make_ctx([], live=[], unavailable_count=0))
    assert response.kind == "empty_keywords"
    assert "No upstream services are configured" in response.text
    assert "0 configured" not in response.text
