"""下游 MCP 服务：三工具注册、invoke 路由、健康快照。

契约唯一来源：docs/design/architecture.md『稳定契约』与『健康通道』。
下游只暴露 search_mcp_tool / invoke_this_tool / check_mcp_status（D-01）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Mapping, Protocol

from mcp import MCPError
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from grep_mcp_tools.config import Config
from grep_mcp_tools.health import HealthRegistry, emit_startup_summary, write_status_json
from grep_mcp_tools.index import (
    close_all,
    describe_error,
    handshake_all,
    load_live_tools,
)
from grep_mcp_tools.search import SearchContext, search

__all__ = ["ProxyServer", "run_proxy", "status_text"]

SEARCH_DESCRIPTION = (
    "Search the indexed tool catalog of all connected upstream MCP servers. "
    "Call this FIRST whenever the task might need a capability provided by an "
    "upstream service (records, HR, operations, deployments, tickets, audits, "
    "reports...). Pass one or more keywords; multiple keywords must all match. "
    "If no tool matches all keywords, partial matches are returned ranked by how "
    "many matched. A server name works as a keyword too: [\"hr\", \"leave\"] "
    "searches within the hr service. Results contain copy-ready targets for "
    "invoke_this_tool, parameter types, and [destructive] warnings."
)

INVOKE_DESCRIPTION = (
    "Invoke an upstream tool. Pass the exact target copied from search_mcp_tool "
    'results (format "server__tool") and a JSON object of arguments; arguments '
    "and results pass through to the upstream service unchanged. Do not "
    "improvise targets - copy them from search results."
)

STATUS_DESCRIPTION = (
    "Report upstream service health. Call this when a tool seems missing, a "
    "target is rejected, or you need to know which services are currently "
    "connected. Shows each configured service with either live status and tool "
    "count, or the reason it is unreachable. When services are down, tell the "
    "user which ones and why."
)


class Upstream(Protocol):
    """ProxyServer 依赖的最小上游接口（真实实现 = index.UpstreamConnection）。"""

    client: object | None
    lock: asyncio.Lock


def status_text(registry: HealthRegistry) -> str:
    """check_mcp_status 的响应文本（快照，不主动探测）。"""
    entries = registry.entries()
    if not entries:
        return "no upstream services configured"

    lines = ["Upstream services:"]
    dead = 0
    for entry in entries:
        if entry.state == "live":
            lines.append(f"- {entry.server}: live, {entry.tools} tool(s)")
        else:
            dead += 1
            lines.append(f"- {entry.server}: UNREACHABLE - {entry.reason}")
    if dead:
        lines.append("")
        lines.append(
            f"WARNING: {dead} upstream service(s) unavailable. Tell the user "
            "which services are down and why; their tools are not searchable "
            "or callable until the service recovers."
        )
    return "\n".join(lines)


def _proxy_error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


class ProxyServer:
    """三工具的下游服务端；搜索/透传/健康快照的接线层。"""

    def __init__(
        self,
        config: Config,
        registry: HealthRegistry,
        connections: Mapping[str, Upstream],
        index_root: Path | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.connections: dict[str, Upstream] = dict(connections)
        self.index_root = Path(index_root) if index_root is not None else config.index_root()
        self._known_tools = self._load_known_tools()
        self.mcp = MCPServer("grep-mcp-tools")
        self._register_tools()

    # ---------- 工具注册 ----------

    def _register_tools(self) -> None:
        mcp = self.mcp

        @mcp.tool(description=SEARCH_DESCRIPTION)
        async def search_mcp_tool(keywords: list[str]) -> str:
            return await self.search(keywords)

        @mcp.tool(description=INVOKE_DESCRIPTION)
        async def invoke_this_tool(target: str, params: dict | None = None):
            # 无返回注解：不发布 output schema，让 CallToolResult 原样透传
            # （spike A 实证：convert_result 对 CallToolResult 直接 return）。
            return await self.invoke(target, params)

        @mcp.tool(description=STATUS_DESCRIPTION)
        async def check_mcp_status() -> str:
            return status_text(self.registry)

    # ---------- 搜索 ----------

    async def search(self, keywords: list[str]) -> str:
        live = self.registry.live_servers()
        tools = load_live_tools([server.name for server in live], self.index_root)
        context = SearchContext(
            tools=tuple(tools),
            live_servers=tuple(live),
            unavailable_count=self.registry.unavailable_count(),
            threshold=self.config.settings.threshold,
            fallback_sample_size=self.config.settings.fallback_sample_size,
        )
        return search(keywords, context).text

    # ---------- 透传 ----------

    async def invoke(self, target: str, params: dict | None = None) -> CallToolResult:
        if "__" not in target:
            return _proxy_error(
                f"Proxy error: invalid target {target!r} - expected 'server__tool'. "
                "Copy an exact target from search_mcp_tool results."
            )
        server_name, tool_name = target.split("__", 1)
        if not server_name or not tool_name:
            return _proxy_error(
                f"Proxy error: invalid target {target!r} - expected 'server__tool'. "
                "Copy an exact target from search_mcp_tool results."
            )

        entry = self.config.servers.get(server_name)
        if entry is None:
            return _proxy_error(
                f"Proxy error: server {server_name!r} is not configured. "
                "Copy an exact target from search_mcp_tool results."
            )

        connection = self.connections.get(server_name)
        if connection is None or connection.client is None:
            health = self.registry.get(server_name)
            reason = health.reason if health is not None and health.reason else "not connected"
            return _proxy_error(
                f"Proxy error: server {server_name!r} is configured but unreachable "
                f"({reason}). Run check_mcp_status for details."
            )

        if tool_name not in self._known_tools.get(server_name, set()):
            return _proxy_error(
                f"Proxy error: tool {tool_name!r} does not exist on server "
                f"{server_name!r}. Run search_mcp_tool to find the right target."
            )

        arguments = params if params is not None else {}
        try:
            async with connection.lock:  # 同一上游串行化，跨上游并发
                return await connection.client.call_tool(tool_name, arguments)
        except MCPError:
            raise  # 上游协议错误原样透传（JSON-RPC 层）
        except Exception as exc:  # 传输层故障 → 自包含的代理级错误
            reason = describe_error(exc)
            return _proxy_error(
                f"Proxy error: call to {target!r} failed: {reason}. "
                "Run check_mcp_status if the service looks unhealthy."
            )

    # ---------- 启动 ----------

    def _load_known_tools(self) -> dict[str, set[str]]:
        """启动快照：每个 live 服务的工具名集合（供 target 校验）。"""
        known: dict[str, set[str]] = {
            entry.server: set()
            for entry in self.registry.entries()
            if entry.state == "live"
        }
        live_names = list(known)
        for tool in load_live_tools(live_names, self.index_root):
            known.setdefault(tool.server, set()).add(tool.name)
        return known


async def run_proxy(config: Config) -> None:
    """完整启动编排：急切握手 → 健康快照/摘要 → 下游 stdio 服务 → 逆序回收。"""
    registry = HealthRegistry()
    connections = await handshake_all(config, registry)
    try:
        write_status_json(registry, config.index_root())
        emit_startup_summary(registry)
        proxy = ProxyServer(config, registry, connections)
        await proxy.mcp.run_stdio_async()
    finally:
        await close_all(connections.values())
