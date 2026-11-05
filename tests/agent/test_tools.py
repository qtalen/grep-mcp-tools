"""MCP `tools/list` → OpenAI tools 参数转换单测。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from mcp.types import Tool

from tests.agent.tools import mcp_tools_to_openai

EMPTY_SCHEMA = {"type": "object", "properties": {}}


def _tool(
    name: str,
    description: str | None = "a tool",
    input_schema: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """构造假 MCP tool（字段名与 `mcp.types.Tool` 的输入面保持一致）。"""
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=input_schema,
    )


def test_converts_basic_tool() -> None:
    schema = {
        "type": "object",
        "properties": {"employee_id": {"type": "string"}},
        "required": ["employee_id"],
    }

    converted = mcp_tools_to_openai([_tool("get_employee", "Fetch one.", schema)])

    assert converted == [
        {
            "type": "function",
            "function": {
                "name": "get_employee",
                "description": "Fetch one.",
                "parameters": schema,
            },
        }
    ]


def test_missing_description_becomes_empty_string() -> None:
    converted = mcp_tools_to_openai([_tool("noop", None)])

    assert converted[0]["function"]["description"] == ""


def test_missing_input_schema_gets_empty_object_schema() -> None:
    tool = SimpleNamespace(name="noop", description="no args")

    converted = mcp_tools_to_openai([tool])

    assert converted[0]["function"]["parameters"] == EMPTY_SCHEMA


def test_empty_input_schema_gets_empty_object_schema() -> None:
    converted = mcp_tools_to_openai([_tool("noop", input_schema={})])

    assert converted[0]["function"]["parameters"] == EMPTY_SCHEMA


def test_required_is_preserved_and_schema_passed_through() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    }

    converted = mcp_tools_to_openai([_tool("needs_two", input_schema=schema)])

    parameters = converted[0]["function"]["parameters"]
    assert parameters is schema
    assert parameters["required"] == ["a", "b"]


def test_does_not_mutate_input_tools() -> None:
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    tool = _tool("search", "Search.", schema)

    mcp_tools_to_openai([tool])

    assert tool.name == "search"
    assert tool.description == "Search."
    assert tool.inputSchema is schema
    assert schema == {"type": "object", "properties": {"q": {"type": "string"}}}


def test_preserves_order() -> None:
    tools = [_tool(f"tool_{index}") for index in range(5)]

    converted = mcp_tools_to_openai(tools)

    assert [item["function"]["name"] for item in converted] == [
        "tool_0",
        "tool_1",
        "tool_2",
        "tool_3",
        "tool_4",
    ]


def test_empty_list() -> None:
    assert mcp_tools_to_openai([]) == []


def test_supports_real_mcp_types_tool() -> None:
    """真实 SDK 的 Tool 字段名是 `input_schema`（序列化别名才是 inputSchema）。"""
    tool = Tool(
        name="real_tool",
        description="Real SDK tool.",
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        },
    )

    converted = mcp_tools_to_openai([tool])

    assert converted[0]["function"]["name"] == "real_tool"
    assert converted[0]["function"]["description"] == "Real SDK tool."
    assert converted[0]["function"]["parameters"]["required"] == ["x"]
