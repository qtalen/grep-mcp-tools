"""6.1 JSONL 索引写盘与加载的单测（TDD：先于实现）。

契约：一行一工具六字段；缺 title/annotations 写 null；description 多行
仍单行（ensure_ascii=False 中文明文）；tmp+rename 原子替换；0 工具不落
文件；加载只吃 live 服务文件（陈旧索引隔离）。
"""

from __future__ import annotations

import json

from mcp.types import Tool, ToolAnnotations

from grep_mcp_tools.index import load_live_tools, write_tools_index


def make_tool(name: str, **kwargs) -> Tool:
    kwargs.setdefault("description", f"{name} description")
    kwargs.setdefault("input_schema", {"type": "object", "properties": {}})
    return Tool(name=name, **kwargs)


def read_lines(path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_writes_one_line_per_tool_with_six_fields(tmp_path):
    tools = [make_tool("alpha"), make_tool("beta")]
    path = write_tools_index("hr", tools, tmp_path)

    assert path == tmp_path / "hr.jsonl"
    lines = read_lines(path)
    assert len(lines) == 2
    for line, tool in zip(lines, tools):
        record = json.loads(line)
        assert set(record) == {
            "server",
            "name",
            "title",
            "description",
            "inputSchema",
            "annotations",
        }
        assert record["server"] == "hr"
        assert record["name"] == tool.name
        assert record["description"] == tool.description
        assert record["inputSchema"] == tool.input_schema


def test_missing_title_and_annotations_written_as_null(tmp_path):
    path = write_tools_index("hr", [make_tool("alpha")], tmp_path)
    record = json.loads(read_lines(path)[0])
    assert record["title"] is None
    assert record["annotations"] is None


def test_multiline_description_stays_single_line(tmp_path):
    tool = make_tool("multi", description="line one\nline two\nline three")
    path = write_tools_index("hr", [tool], tmp_path)

    lines = read_lines(path)
    assert len(lines) == 1
    assert "\n" not in lines[0].replace("\\n", "")
    record = json.loads(lines[0])
    assert record["description"] == "line one\nline two\nline three"


def test_chinese_description_written_as_utf8(tmp_path):
    tool = make_tool("cn", description="提交请假申请")
    path = write_tools_index("hr", [tool], tmp_path)

    raw = path.read_bytes()
    assert "提交请假申请".encode("utf-8") in raw
    assert json.loads(read_lines(path)[0])["description"] == "提交请假申请"


def test_annotations_preserved_with_wire_aliases(tmp_path):
    tool = make_tool(
        "delete_cluster",
        annotations=ToolAnnotations(destructive_hint=True, read_only_hint=False),
    )
    path = write_tools_index("itops", [tool], tmp_path)
    record = json.loads(read_lines(path)[0])
    assert record["annotations"]["destructiveHint"] is True
    assert record["annotations"]["readOnlyHint"] is False


def test_rewrite_replaces_not_appends(tmp_path):
    write_tools_index("hr", [make_tool("a"), make_tool("b")], tmp_path)
    write_tools_index("hr", [make_tool("c")], tmp_path)
    lines = read_lines(tmp_path / "hr.jsonl")
    assert len(lines) == 1
    assert json.loads(lines[0])["name"] == "c"


def test_zero_tools_writes_nothing(tmp_path):
    result = write_tools_index("empty_srv", [], tmp_path)
    assert result is None
    assert not (tmp_path / "empty_srv.jsonl").exists()


def test_zero_tools_removes_previous_index_file(tmp_path):
    """0 工具上游必须清掉上一轮的索引文件（否则幽灵工具被搜出）。"""
    write_tools_index("empty_srv", [make_tool("old_tool")], tmp_path)
    assert (tmp_path / "empty_srv.jsonl").is_file()

    result = write_tools_index("empty_srv", [], tmp_path)

    assert result is None
    assert not (tmp_path / "empty_srv.jsonl").exists()


def test_no_temp_residue_after_write(tmp_path):
    write_tools_index("hr", [make_tool("a")], tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["hr.jsonl"]


def test_load_live_tools_roundtrip(tmp_path):
    tool = make_tool(
        "get_employee",
        input_schema={
            "type": "object",
            "properties": {"employee_id": {"type": "string"}},
            "required": ["employee_id"],
        },
    )
    write_tools_index("hr", [tool], tmp_path)

    loaded = load_live_tools(["hr"], tmp_path)
    assert len(loaded) == 1
    entry = loaded[0]
    assert entry.server == "hr"
    assert entry.name == "get_employee"
    assert entry.input_schema == tool.input_schema
    assert entry.raw_line == read_lines(tmp_path / "hr.jsonl")[0]
    assert entry.line_no == 1
    assert entry.target == "hr__get_employee"


def test_load_live_tools_skips_stale_files(tmp_path):
    """陈旧索引隔离：只加载本轮 live 服务的文件。"""
    write_tools_index("hr", [make_tool("a")], tmp_path)
    write_tools_index("stale", [make_tool("old")], tmp_path)

    loaded = load_live_tools(["hr"], tmp_path)
    assert {entry.server for entry in loaded} == {"hr"}


def test_load_live_tools_missing_file_is_empty(tmp_path):
    assert load_live_tools(["ghost"], tmp_path) == []


def test_load_live_tools_assigns_line_numbers(tmp_path):
    write_tools_index("hr", [make_tool("a"), make_tool("b"), make_tool("c")], tmp_path)
    loaded = load_live_tools(["hr"], tmp_path)
    assert [entry.line_no for entry in loaded] == [1, 2, 3]
