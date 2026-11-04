"""Spike A (B01 开工首验): MCPServer 装饰器工具能否原样转发手建 CallToolResult。

源码证据链 (mcp 2.3.0):
- mcp/server/mcpserver/server.py:442  _handle_call_tool -> call_tool
- mcp/server/mcpserver/tools/base.py:132 Tool.run(convert_result=True)
- mcp/server/mcpserver/utilities/func_metadata.py:186 convert_result:
    if isinstance(result, CallToolResult): ... return result

本测试在运行时钉住该行为：in-memory Client 往返，验证 content 列表与
is_error 标志原样到达下游（100KB 载荷为压力测试）。

同时对比两种返回注解形态：
- relay_annotated: 注解 -> CallToolResult（若 SDK 据此发布 output schema，
  非 error 结果的 structured_content 会被校验，可能拒绝）
- relay_plain: 无返回注解（output schema 不发布，透传零干扰）
"""

from __future__ import annotations

from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

import pytest

pytestmark = pytest.mark.spike

BIG_TEXT = "x" * (100 * 1024)


def build_server() -> MCPServer:
    mcp = MCPServer("spike-a")

    @mcp.tool()
    async def relay_annotated(flag: bool) -> CallToolResult:
        """Hand-built CallToolResult with explicit return annotation."""
        if flag:
            return CallToolResult(
                content=[TextContent(type="text", text=BIG_TEXT)], is_error=True
            )
        return CallToolResult(content=[TextContent(type="text", text="ok")], is_error=False)

    @mcp.tool()
    async def relay_plain(flag: bool):
        """Hand-built CallToolResult without return annotation."""
        if flag:
            return CallToolResult(
                content=[TextContent(type="text", text=BIG_TEXT)], is_error=True
            )
        return CallToolResult(content=[TextContent(type="text", text="ok")], is_error=False)

    return mcp


async def test_error_result_passes_through_with_content_intact() -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool("relay_plain", {"flag": True})
        assert result.is_error is True
        assert len(result.content) == 1
        assert result.content[0].text == BIG_TEXT


async def test_ok_result_passes_through() -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool("relay_plain", {"flag": False})
        assert result.is_error is False
        assert result.content[0].text == "ok"


async def test_annotated_variant_behavior() -> None:
    """记录注解形态的实际行为（发布 output schema 与否不影响裁决即可用性）。"""
    async with Client(build_server()) as client:
        result = await client.call_tool("relay_annotated", {"flag": True})
        assert result.is_error is True
        assert result.content[0].text == BIG_TEXT
