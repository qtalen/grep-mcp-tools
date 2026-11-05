"""MCP 工具定义 → OpenAI `tools` 参数。

只做字段搬运：`name` / `description` / `inputSchema` 原样透传（含 `required`），
不重写 schema。两个上游字段名都要兼容：MCP Python SDK 的 `mcp.types.Tool`
真实属性是 `input_schema`，而 OpenAI 兼容格式与测试夹具习惯写 `inputSchema`。
"""

from __future__ import annotations

from typing import Any, Sequence

EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def _input_schema(tool: Any) -> dict[str, Any]:
    """读取 MCP tool 的 JSON Schema；两种属性名都兼容，缺失时给空对象。"""
    schema = getattr(tool, "inputSchema", None)
    if schema is None:
        schema = getattr(tool, "input_schema", None)
    return schema or EMPTY_SCHEMA


def mcp_tools_to_openai(tools: Sequence[Any]) -> list[dict[str, Any]]:
    """把 MCP `tools/list` 结果转换成 OpenAI `tools` 数组（保持输入顺序）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": getattr(tool, "description", None) or "",
                "parameters": _input_schema(tool),
            },
        }
        for tool in tools
    ]
