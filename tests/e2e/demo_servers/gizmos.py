"""gizmos — stdio demo upstream with deliberately awkward tools (B03).

Five tools, each exercising one edge of the proxy:
- ``huge_schema``: inputSchema serialises to >=50 KB (F-section scale smoke)
- ``slow``: sleeps ``seconds`` (default 5) so timeouts cannot truncate silently
- ``chinese_echo``: Chinese tool description and Chinese result
- ``multiline_quote``: description with newlines, double quotes and Chinese
- ``echo_env``: echoes an environment variable (A-section env injection)

Not part of the shared fixture: these tools exist to break things.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Annotated

from mcp.server import MCPServer
from pydantic import Field

mcp = MCPServer("gizmos")


@mcp.tool()
def huge_schema(payload: Annotated[str, Field(description="x" * 50000)]) -> str:
    """Accept one payload whose parameter description is intentionally ~50 KB."""
    return "ok"


@mcp.tool()
async def slow(seconds: float = 5) -> str:
    """Sleep for the requested number of seconds, then report it."""
    await asyncio.sleep(seconds)
    return f"slept {seconds}s"


@mcp.tool()
def chinese_echo(text: str) -> str:
    """回显收到的中文文本，用于验证中文参数的往返无损。"""
    return f"收到：{text}"


@mcp.tool(description='line one\nline two with "quotes" and 中文描述')
def multiline_quote(name: str) -> str:
    return f"echo: {name}"


@mcp.tool()
def echo_env(key: str) -> str:
    """Return the value of the environment variable ``key`` (or ``<unset>``)."""
    return os.environ.get(key, "<unset>")


if __name__ == "__main__":
    # E 区孤儿回收用例：由 config 的 env 注入路径，代理 spawn 本进程时记录 PID。
    pid_file = os.environ.get("B03_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")
    mcp.run(transport="stdio")
