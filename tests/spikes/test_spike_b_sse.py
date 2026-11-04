"""Spike B (B01 开工首验): SDK 2.3.0 的 SSE 服务端 transport 可用性。

事实基础:
- mcp/server/sse.py: SseServerTransport 仍在（Starlette 形态）
- mcp/server/mcpserver/server.py:1083 run_sse_async(host, port, sse_path,
  message_path) 官方支持 transport="sse"

本测试在运行时钉住：起一个 MCPServer SSE 服务（显式绑 127.0.0.1 + 随机
空闲端口），经 Client(sse_client(...)) 握手、list_tools、call_tool 全通。
audit 假上游的 SSE 形态据此定案。
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from mcp import Client
from mcp.client.sse import sse_client
from mcp.server import MCPServer

pytestmark = pytest.mark.spike


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_server() -> MCPServer:
    mcp = MCPServer("spike-b-sse")

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    return mcp


async def wait_port_accepts(port: int, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.1)
    raise TimeoutError(f"SSE server did not accept connections on port {port}")


async def test_sse_transport_roundtrip() -> None:
    port = free_port()
    server = build_server()
    task = asyncio.create_task(
        server.run_sse_async(host="127.0.0.1", port=port)
    )
    try:
        await wait_port_accepts(port)
        transport = sse_client(f"http://127.0.0.1:{port}/sse")
        async with Client(transport) as client:
            tools = await client.list_tools()
            assert [t.name for t in tools.tools] == ["add"]
            result = await client.call_tool("add", {"a": 1, "b": 2})
            assert result.is_error is False
            assert result.content[0].text == "3"
    finally:
        task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await task
