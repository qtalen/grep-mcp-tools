"""下游 MCP 服务：三工具注册、invoke 路由、健康快照。

契约唯一来源：docs/design/architecture.md『稳定契约』与『健康通道』。
下游只暴露 search_mcp_tool / invoke_this_tool / check_mcp_status（D-01）。
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Mapping, Protocol

from mcp import MCPError
from mcp.server import MCPServer
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.subscriptions import InMemorySubscriptionBus, ToolsListChanged
from mcp.types import CallToolResult, TextContent
from mcp_types import CONNECTION_CLOSED

from grep_mcp_tools.config import Config
from grep_mcp_tools.health import HealthRegistry, emit_startup_summary, write_status_json
from grep_mcp_tools.index import (
    close_all,
    describe_error,
    handshake_all,
    load_live_tools,
)
from grep_mcp_tools.search import (
    SearchContext,
    LiveServer,
    render_search_description,
    roster_signature,
    search,
)

__all__ = ["ProxyServer", "run_proxy", "run_refresh_loop", "status_text"]

# 触发文案（静态部分）；服务清单随健康注册表动态注入（B02）。
SEARCH_TRIGGER_TEXT = (
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


class _SessionCapture:
    """旧代线（initialize 握手）：middleware 捕获首个下游请求的 session。

    每个 inbound 请求/通知都经过 middleware（含 initialize），因此首个
    下游请求即可拿到 session 句柄，供签名变化时 send_tool_list_changed。
    """

    def __init__(self) -> None:
        self.session = None

    async def __call__(self, ctx, call_next):
        if self.session is None and getattr(ctx, "session", None) is not None:
            self.session = ctx.session
        return await call_next(ctx)


def _advertise_tools_list_changed(mcp: MCPServer) -> None:
    """能力位修正：initialize 声明 tools.listChanged=true。

    SDK 2.3.0 的 ``run_stdio_async`` 无参调用 ``create_initialization_options``
    → ``NotificationOptions`` 默认全 False → 能力位 false，严格按能力过滤
    的客户端可能不装通知 handler。MCPServer 只无参调用该工厂，wrap 强制
    ``tools_changed=True``（依赖私有属性，SDK 升级需复验——钉 mcp==2.3.0）。
    """
    lowlevel = mcp._lowlevel_server
    original = lowlevel.create_initialization_options

    def wrapped(*args, **kwargs):
        kwargs["notification_options"] = NotificationOptions(tools_changed=True)
        return original(*args, **kwargs)

    lowlevel.create_initialization_options = wrapped


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
        self._roster_signature = self._current_roster_signature()
        # list_changed 双线（D-25）：旧代 middleware 捕获 session；
        # 新代向注入的 SubscriptionBus publish（自建实例以持引用）。
        self.bus = InMemorySubscriptionBus()
        self._session_capture = _SessionCapture()
        self.mcp = MCPServer(
            "grep-mcp-tools",
            middleware=[self._session_capture],
            subscriptions=self.bus,
        )
        _advertise_tools_list_changed(self.mcp)
        self._register_tools()

    # ---------- 工具注册 ----------

    def _register_tools(self) -> None:
        mcp = self.mcp
        self._register_search_tool(self._render_search_description())

        @mcp.tool(description=INVOKE_DESCRIPTION)
        async def invoke_this_tool(target: str, params: dict | None = None):
            # 无返回注解：不发布 output schema，让 CallToolResult 原样透传
            # （spike A 实证：convert_result 对 CallToolResult 直接 return）。
            return await self.invoke(target, params)

        @mcp.tool(description=STATUS_DESCRIPTION)
        async def check_mcp_status() -> str:
            return status_text(self.registry)

    def _register_search_tool(self, description: str) -> None:
        """注册 search_mcp_tool（构造时与签名变化重注册共用一个入口）。"""

        @self.mcp.tool(description=description)
        async def search_mcp_tool(keywords: list[str]) -> str:
            return await self.search(keywords)

    # ---------- description 注入面 ----------

    def _render_search_description(self) -> str:
        return render_search_description(
            SEARCH_TRIGGER_TEXT,
            self.registry.live_servers(),
            self.registry.unavailable_count(),
            self.config.settings.description_max_servers,
        )

    def _current_roster_signature(self) -> tuple:
        """description 文本的决定因素（重注册与通知的触发判据）。"""
        return roster_signature(
            self.registry.live_servers(),
            self.registry.unavailable_count(),
            self.config.settings.description_max_servers,
        )

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

        # config 镜像：路由表恒含全部配置服务（不因启动失败缺席）；
        # 判死单条件 client is None，原因自健康注册表（自包含）。
        connection = self.connections[server_name]
        if connection.client is None:
            return self._unreachable_error(server_name)

        if tool_name not in self._known_tools.get(server_name, set()):
            return _proxy_error(
                f"Proxy error: tool {tool_name!r} does not exist on server "
                f"{server_name!r}. Run search_mcp_tool to find the right target."
            )

        arguments = params if params is not None else {}
        try:
            async with connection.lock:  # 同一上游串行化，跨上游并发
                # 复检：排队等锁期间刷新轮可能已把该上游停成 None
                # （重握手窗口），此时同样报"不可达"而非 AttributeError。
                if connection.client is None:
                    return self._unreachable_error(server_name)
                return await connection.client.call_tool(tool_name, arguments)
        except MCPError as exc:
            if str(exc.code) == str(CONNECTION_CLOSED):
                # 连接已死（进程退出/传输关闭）不是上游业务错误——
                # 注册表翻转前的窗口里报代理级错误自包含原因（下轮自愈）。
                return _proxy_error(
                    f"Proxy error: call to {target!r} failed: {describe_error(exc)}. "
                    "Run check_mcp_status if the service looks unhealthy."
                )
            raise  # 其余上游 JSON-RPC 错误原样透传
        except Exception as exc:  # 传输层故障 → 自包含的代理级错误
            reason = describe_error(exc)
            return _proxy_error(
                f"Proxy error: call to {target!r} failed: {reason}. "
                "Run check_mcp_status if the service looks unhealthy."
            )

    def _unreachable_error(self, server_name: str) -> CallToolResult:
        """config 内但不可达：原因自注册表（自包含），与其他错误可区分。"""
        health = self.registry.get(server_name)
        reason = health.reason if health is not None and health.reason else "not connected"
        return _proxy_error(
            f"Proxy error: server {server_name!r} is configured but unreachable "
            f"({reason}). Run check_mcp_status for details."
        )

    # ---------- 刷新轮尾同步 ----------

    async def sync_after_refresh(self) -> None:
        """轮尾同步块（由刷新循环调用）：校验表重建 + roster 签名 diff。

        ``_known_tools`` 每轮无条件重建（幂等读盘，换"校验表与索引永不
        脱节"的不变式）；description 仅在签名变化时重注册（remove 与
        重注册之间无 await——并发 tools/list 不得看到窗口期缺工具）；
        变化后经双线尽力通知（D-25）。

        签名在重注册**成功之后**才提交：重注册中途异常时签名保持旧值，
        下一轮强制重试，不让 ``search_mcp_tool`` 永久缺席。remove 对
        "工具不存在"容错（上次异常中断后重试的合法态）。
        """
        self._known_tools = self._load_known_tools()
        signature = self._current_roster_signature()
        if signature != self._roster_signature:
            # 原子段：remove → 重注册之间无 await
            try:
                self.mcp.remove_tool("search_mcp_tool")
            except Exception:
                pass  # 上次重试中断后的缺席态：继续注册即可
            self._register_search_tool(self._render_search_description())
            self._roster_signature = signature  # 成功后才提交
            await self._notify_list_changed()

    async def _notify_list_changed(self) -> None:
        """双线尽力通知：新代 bus publish；旧代 session 推送。两线各自隔离。"""
        try:
            await self.bus.publish(ToolsListChanged())  # 无 listener 时静默
        except Exception:
            pass  # 换自定义 bus（网络实现）时也不得阻断另一线
        session = self._session_capture.session
        if session is not None:
            try:
                await session.send_tool_list_changed()
            except Exception:
                pass  # 尽力而为：宿主不响应/会话已死不影响代理

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
    """完整启动编排：急切握手 → 健康快照/摘要 → 下游 stdio 服务 → 逆序回收。

    启动握手轮同步 await（急切契约：应答 initialize 前索引落盘）；随后起
    刷新循环任务。退出时序（黄金纪律）：先 cancel 刷新任务并等待结束，
    再 close_all——防刷新重握手与 teardown 竞态留下孤儿 stdio 进程。
    """
    registry = HealthRegistry()
    connections = await handshake_all(config, registry)
    refresh_task: asyncio.Task | None = None
    try:
        write_status_json(registry, config.index_root())
        emit_startup_summary(registry)
        proxy = ProxyServer(config, registry, connections)
        refresh_task = asyncio.create_task(
            run_refresh_loop(proxy, config, registry, connections)
        )
        await proxy.mcp.run_stdio_async()
    finally:
        await _shutdown(refresh_task, connections.values())


async def _shutdown(refresh_task: asyncio.Task | None, connections) -> None:
    """退出时序：cancel 刷新 → 等待其结束 → 逆序回收全部上游连接。"""
    if refresh_task is not None:
        refresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresh_task
    await close_all(connections)


async def _refresh_round(proxy: "ProxyServer", config: Config, registry, connections) -> None:
    """一轮刷新：并发刷新全部上游 + 轮尾同步块（顺序固定）。

    轮内单上游异常不冒泡（每上游 refresh 自身 fail-soft）；轮尾三件事
    由轮级兜底包裹（见 run_refresh_loop）——写盘失败只记日志不杀循环。
    """
    index_root = config.index_root()
    timeout = config.settings.handshake_timeout
    await asyncio.gather(
        *(conn.refresh(timeout, registry, index_root) for conn in connections.values())
    )
    # 轮尾同步块：盘上状态 = 内存状态；校验表与索引永不脱节。
    # 两阶段各自兜底：一处失败（如 Windows 上 _status.json 被占用）只
    # 记一行 stderr，不连带跳过另一处——否则持续写盘失败会把
    # description 清单与 target 校验冻结在旧值，与 search/status 矛盾。
    try:
        write_status_json(registry, index_root)
    except Exception as exc:
        print(
            f"grep-mcp-tools: status snapshot write failed: {describe_error(exc)}",
            file=sys.stderr,
        )
    try:
        await proxy.sync_after_refresh()
    except Exception as exc:
        print(
            f"grep-mcp-tools: post-refresh sync failed: {describe_error(exc)}",
            file=sys.stderr,
        )


async def run_refresh_loop(proxy: "ProxyServer", config: Config, registry, connections) -> None:
    """定时刷新循环：轮与轮串行不重叠，慢轮顺延（固定频率语义）。

    实际轮间隔 = max(refresh_interval, 单轮耗时)——每轮按"开始时刻 +
    interval"对齐下一轮；轮级异常兜底：单轮任何异常（含轮尾写盘失败）
    只记一行 stderr，循环不死。
    """
    interval = config.settings.refresh_interval
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        try:
            await _refresh_round(proxy, config, registry, connections)
        except Exception as exc:  # 轮级兜底：刷新循环必须比单轮故障活得久
            print(
                f"grep-mcp-tools: refresh round failed: {describe_error(exc)}",
                file=sys.stderr,
            )
        await asyncio.sleep(max(0.0, interval - (loop.time() - started)))
