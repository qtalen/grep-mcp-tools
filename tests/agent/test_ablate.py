"""消融剥离器单测：钉死真实文案样例，验证「只删不改」。"""

from __future__ import annotations

from tests.agent.ablate import strip_service_injection, strip_signpost

TRIGGER = (
    "Search across upstream MCP tools by keyword. Multiple keywords must all "
    "match; returns exact targets to pass to invoke_this_tool."
)


# ---------- description 注入块 ----------


def test_strips_connected_services_block() -> None:
    description = f"{TRIGGER}\n\nConnected services: audit (3 tools), hr (8 tools)"

    stripped = strip_service_injection(description)

    assert stripped == TRIGGER
    assert "Connected services" not in stripped
    assert description.startswith(stripped)


def test_strips_connected_services_with_total_overflow_and_unavailable_suffix() -> None:
    description = (
        f"{TRIGGER}\n\nConnected services (of 9): audit (3 tools), hr (8 tools) "
        "(2 unavailable - run check_mcp_status)"
    )

    assert strip_service_injection(description) == TRIGGER


def test_strips_no_upstream_services_variants() -> None:
    configured = f"{TRIGGER}\n\nno upstream services configured"
    unavailable = (
        f"{TRIGGER}\n\nno upstream services currently connected "
        "(2 unavailable). Run check_mcp_status for details."
    )

    assert strip_service_injection(configured) == TRIGGER
    assert strip_service_injection(unavailable) == TRIGGER


def test_description_without_injection_is_returned_unchanged() -> None:
    assert strip_service_injection(TRIGGER) == TRIGGER


# ---------- 双零路标块 ----------


def test_strips_signpost_block_after_no_match() -> None:
    response = (
        "No tools matched [zzz-unmatched-zzz].\n\n"
        "Available services:\n"
        "- audit: 3 tools\n"
        "- hr: 8 tools\n\n"
        'Tip: try fewer or different keywords, or include a server name '
        '(e.g. "hr leave").'
    )

    stripped = strip_signpost(response)

    assert stripped == "No tools matched [zzz-unmatched-zzz]."
    assert "Available services" not in stripped
    assert response.startswith(stripped)


def test_strips_signpost_block_from_empty_keywords_usage() -> None:
    response = (
        'Usage: search_mcp_tool(keywords: ["leave", "hr"]) - pass one or more '
        "keywords.\n\n"
        "Available services:\n"
        "- audit: 3 tools\n"
        "- hr: 8 tools"
    )

    stripped = strip_signpost(response)

    assert stripped.startswith("Usage: search_mcp_tool(keywords:")
    assert "Available services" not in stripped
    assert response.startswith(stripped)


def test_signpost_no_upstream_variant_has_no_services_block() -> None:
    # 没有 live 服务时产品文案是「No upstream services are configured.」，
    # 不含 `Available services:` 块，剥离器原样返回（断言仍不会命中）。
    response = "No tools matched [zzz]. No upstream services are configured."

    assert strip_signpost(response) == response
    assert "Available services" not in strip_signpost(response)


def test_text_without_signpost_is_returned_unchanged() -> None:
    response = "1 tool(s) matched [leave]:\n\nhr__get_leave_balance - ..."

    assert strip_signpost(response) == response
