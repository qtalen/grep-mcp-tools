"""实验两臂的 MCP 客户端。

- `ProxyArm`（臂 a）：用 SDK `Client` 以 stdio 拉起 grep-mcp-tools 代理，
  模型只看到代理自有 3 工具。
- `DirectArm`（臂 b）：为每个上游各建一个 `Client`（stdio / Streamable HTTP /
  SSE 三种传输），把全部工具扁平化为 `server__tool`，调用时按前缀分发。

两臂对上层暴露同一窄接口 `list_openai_tools` / `call_upstream`，工具循环无需
关心背后是代理还是直挂。所有 Client 由 `AsyncExitStack` 统一关闭。
"""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from tests.agent.tools import mcp_tools_to_openai


@dataclass(frozen=True)
class UpstreamSpec:
    """直挂臂的一个上游连接参数。"""

    name: str
    transport: str  # "stdio" | "streamable-http" | "sse"
    url: str = ""
    command: str = ""
    args: tuple[str, ...] = ()


class Arm(Protocol):
    """两臂共享的窄接口。"""

    async def list_openai_tools(self) -> list[dict]:
        ...

    async def call_upstream(self, name: str, arguments: dict) -> str:
        ...


def flatten_tools(server: str, tools: Sequence[Any]) -> list[dict]:
    """把单个上游的 MCP 工具转换成 `server__tool` 命名的 OpenAI tools。"""
    converted = mcp_tools_to_openai(tools)
    for item in converted:
        function = item["function"]
        function["name"] = f"{server}__{function['name']}"
    return converted


def merge_tool_lists(per_server: dict[str, list[dict]]) -> list[dict]:
    """按字典插入顺序拼接各上游的工具清单。"""
    merged: list[dict] = []
    for tools in per_server.values():
        merged.extend(tools)
    return merged


def _join_text(result: Any) -> str:
    """拼接 CallToolResult 中所有文本块。"""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "".join(parts)


def _build_client(spec: UpstreamSpec) -> Client:
    """按传输类型构造 SDK Client（返回的是可 async with 的对象）。"""
    if spec.transport == "stdio":
        if not spec.command:
            raise ValueError(f"stdio upstream {spec.name!r} requires command")
        return Client(
            StdioServerParameters(command=spec.command, args=list(spec.args))
        )
    if spec.transport == "streamable-http":
        return Client(streamable_http_client(spec.url, terminate_on_close=False))
    if spec.transport == "sse":
        return Client(sse_client(spec.url))
    raise ValueError(f"unknown transport {spec.transport!r} for upstream {spec.name!r}")


class ProxyArm:
    """臂 a：stdio 挂代理子进程。"""

    def __init__(self, config_path: Path) -> None:
        self.config_path = Path(config_path)
        self._stack = AsyncExitStack()
        self._client: Client | None = None

    async def __aenter__(self) -> "ProxyArm":
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "grep_mcp_tools", "--config", str(self.config_path)],
        )
        self._client = await self._stack.enter_async_context(Client(params))
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._stack.aclose()
        self._client = None

    def _require_client(self) -> Client:
        if self._client is None:
            raise RuntimeError("ProxyArm used outside its async context manager")
        return self._client

    async def list_openai_tools(self) -> list[dict]:
        result = await self._require_client().list_tools()
        return mcp_tools_to_openai(result.tools)

    async def call_upstream(self, name: str, arguments: dict) -> str:
        result = await self._require_client().call_tool(name, arguments)
        return _join_text(result)


class DirectArm:
    """臂 b：直连全部上游，`server__tool` 扁平命名。"""

    def __init__(self, upstreams: Sequence[UpstreamSpec]) -> None:
        self._upstreams = list(upstreams)
        self._stack = AsyncExitStack()
        self._clients: dict[str, Client] = {}

    async def __aenter__(self) -> "DirectArm":
        for spec in self._upstreams:
            client = await self._stack.enter_async_context(_build_client(spec))
            self._clients[spec.name] = client
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._stack.aclose()
        self._clients.clear()

    async def list_openai_tools(self) -> list[dict]:
        per_server: dict[str, list[dict]] = {}
        for name, client in self._clients.items():
            result = await client.list_tools()
            per_server[name] = flatten_tools(name, result.tools)
        return merge_tool_lists(per_server)

    async def call_upstream(self, name: str, arguments: dict) -> str:
        server, separator, tool_name = name.partition("__")
        if not separator or server not in self._clients:
            raise KeyError(f"unknown upstream tool {name!r}")
        result = await self._clients[server].call_tool(tool_name, arguments)
        return _join_text(result)
