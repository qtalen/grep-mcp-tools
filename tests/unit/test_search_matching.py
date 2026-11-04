"""4.1 匹配语义族单测（TDD：先于实现）。

契约：匹配域 = 索引行原始 JSON 整行文本；大小写不敏感、子串、非正则。
"""

from __future__ import annotations

from grep_mcp_tools.search import search
from tests.unit.search_helpers import make_ctx, row


def test_case_insensitive_three_forms():
    tools = [row("hr", "get_leave", "Submit Leave Request")]
    for keywords in (["leave"], ["LEAVE"], ["Leave"]):
        response = search(keywords, make_ctx(tools))
        assert response.kind == "matches"
        assert [t.name for t in response.tools] == ["get_leave"]


def test_substring_not_word_boundary():
    tools = [row("hr", "get_employee", "employee directory")]
    response = search(["ploy"], make_ctx(tools))
    assert [t.name for t in response.tools] == ["get_employee"]


def test_regex_metacharacters_are_literal():
    literal = row("ops", "regex_doc", "matches region.* pattern")
    plain = row("ops", "region_doc", "regional overview", line_no=2)
    response = search(["region.*"], make_ctx([literal, plain]))
    assert [t.name for t in response.tools] == ["regex_doc"]


def test_chinese_keywords():
    tools = [row("hr", "ask_leave", "提交请假申请")]
    response = search(["请假"], make_ctx(tools))
    assert [t.name for t in response.tools] == ["ask_leave"]


def test_keyword_only_in_parameter_description_hits():
    tools = [
        row(
            "hr",
            "lookup",
            "Look up a record",
            input_schema={
                "type": "object",
                "properties": {
                    "badge": {
                        "type": "string",
                        "description": "The employee badge identifier",
                    }
                },
            },
        )
    ]
    response = search(["badge"], make_ctx(tools))
    assert [t.name for t in response.tools] == ["lookup"]


def test_server_name_as_keyword_scopes_to_server():
    tools = [
        row("hr", "get_employee", "employee record"),
        row("hr", "get_leave", "leave balance", line_no=2),
        row("itops", "get_employee", "service employee registry"),
    ]
    response = search(["hr"], make_ctx(tools))
    assert {t.server for t in response.tools} == {"hr"}
    assert len(response.tools) == 2


def test_structural_word_string_matches_everything():
    """已知噪音锁定（D-22）：schema 结构词命中一切。"""
    tools = [
        row("hr", "a", "first", input_schema={"type": "object", "properties": {"x": {"type": "string"}}}),
        row("itops", "b", "second", input_schema={"type": "object", "properties": {"y": {"type": "string"}}}, line_no=2),
    ]
    response = search(["string"], make_ctx(tools))
    assert response.kind == "matches"
    assert len(response.tools) == 2


def test_empty_keyword_strings_are_ignored():
    tools = [row("hr", "get_leave", "leave balance")]
    # 空串/纯空白关键词被丢弃；剩下 "leave" 正常搜索
    response = search(["", "   ", "leave"], make_ctx(tools))
    assert response.kind == "matches"
    assert [t.name for t in response.tools] == ["get_leave"]


def test_all_blank_keywords_treated_as_empty():
    tools = [row("hr", "get_leave", "leave balance")]
    response = search(["", "  "], make_ctx(tools))
    assert response.kind == "empty_keywords"
