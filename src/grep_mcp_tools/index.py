"""索引写路径（握手 → JSONL）与读侧加载。

契约唯一来源：docs/design/architecture.md『写路径』与『稳定契约』。
索引 = 一服务一文件、一行一工具、tmp+rename 原子替换、强制 UTF-8。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, Iterable

import httpx2
from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from urllib.parse import urlparse

from grep_mcp_tools.config import Config, ServerEntry
from grep_mcp_tools.health import HealthRegistry
from grep_mcp_tools.search import IndexedTool

__all__ = [
    "UpstreamConnection",
    "client_for",
    "close_all",
    "describe_error",
    "handshake_all",
    "http_client_factory",
    "is_loopback_url",
    "load_live_tools",
    "write_tools_index",
]


def build_record(server: str, tool: Any) -> dict:
    """mcp Tool → 六字段 JSONL 记录（schema/annotations 用线格式别名）。"""
    annotations = getattr(tool, "annotations", None)
    if annotations is not None:
        annotations = annotations.model_dump(by_alias=True, exclude_none=True) or None
    return {
        "server": server,
        "name": tool.name,
        "title": getattr(tool, "title", None),
        "description": getattr(tool, "description", None),
        "inputSchema": tool.input_schema,
        "annotations": annotations,
    }


def write_tools_index(server: str, tools: list, index_root: Path) -> Path | None:
    """原子写 ``<index_root>/<server>.jsonl``。

    0 工具返回 None 且不落文件；若存在上一轮的同名索引文件则删除
    （契约：0 工具上游不生成索引文件——残留文件会被陈旧索引隔离与
    target 校验误当作本轮工具）。
    """
    index_root = Path(index_root)
    target = index_root / f"{server}.jsonl"
    if not tools:
        target.unlink(missing_ok=True)
        return None

    index_root.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=index_root, prefix=f".{server}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for tool in tools:
                record = build_record(server, tool)
                handle.write(
                    json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                )
                handle.write("\n")
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


def load_live_tools(live_server_names: Iterable[str], index_root: Path) -> list[IndexedTool]:
    """只加载本轮 live 服务的索引文件（陈旧索引隔离）。"""
    index_root = Path(index_root)
    loaded: list[IndexedTool] = []
    for server in sorted(set(live_server_names)):
        path = index_root / f"{server}.jsonl"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                raw_line = line.rstrip("\n")
                if not raw_line:
                    continue
                record = json.loads(raw_line)
                loaded.append(
                    IndexedTool(
                        server=record["server"],
                        name=record["name"],
                        title=record.get("title"),
                        description=record.get("description"),
                        input_schema=record.get("inputSchema"),
                        annotations=record.get("annotations"),
                        raw_line=raw_line,
                        line_no=line_no,
                    )
                )
    return loaded


# ---------- 握手循环 ----------

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
# 读超时无限：黄金律 1 / D-06 要求 invoke 无代理级超时（上游可以长时间无响应
# 字节）。握手阶段的限时由 asyncio.timeout(handshake_timeout) 负责，取消会
# 传播进 httpx2 请求，不需要传输层读超时兜底；连接/写/池超时保留防挂死。
_DEFAULT_TIMEOUT = httpx2.Timeout(30.0, read=None)


def is_loopback_url(url: str) -> bool:
    """URL 是否指向本机回环（127.0.0.1 / localhost / ::1）。"""
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    return bool(host) and host.lower() in _LOOPBACK_HOSTS


def _no_read_timeout(timeout: httpx2.Timeout | None) -> httpx2.Timeout:
    source = timeout if timeout is not None else _DEFAULT_TIMEOUT
    return httpx2.Timeout(
        connect=source.connect,
        read=None,
        write=source.write,
        pool=source.pool,
    )


def http_client_factory(url: str):
    """httpx2 客户端工厂。

    loopback 上游禁用环境/系统代理：下游宿主拉起本代理时环境变量可能被
    裁剪（MCP stdio 默认 env 白名单不含 NO_PROXY），httpx2 会回落到
    Windows 系统代理，把 127.0.0.1 请求劫持到代理上——实测死上游会变成
    "Server returned an error response" 而非连接失败。
    读超时一律置无限（D-06），SDK 传入的 read（如 SSE 的 sse_read_timeout）
    同样被覆盖。
    """
    trust_env = not is_loopback_url(url)

    def factory(headers=None, timeout=None, auth=None):
        kwargs: dict = {
            "headers": headers or {},
            "timeout": _no_read_timeout(timeout),
            "trust_env": trust_env,
        }
        if auth is not None:
            kwargs["auth"] = auth
        return httpx2.AsyncClient(**kwargs)

    return factory


@asynccontextmanager
async def _http_transport(entry: ServerEntry):
    """Streamable HTTP 传输；loopback 绕过代理，headers 经客户端注入。"""
    url = entry.url or ""
    factory = http_client_factory(url)
    async with factory(headers=entry.headers or None) as http_client:
        async with streamable_http_client(url, http_client=http_client) as streams:
            yield streams


def client_for(entry: ServerEntry) -> Client:
    """按 config 条目构造上游 Client（握手与 invoke 共用）。"""
    if entry.transport == "stdio":
        params = StdioServerParameters(
            command=entry.command or "",
            args=entry.args,
            env=entry.env or None,
        )
        return Client(params)
    if entry.transport == "sse":
        url = entry.url or ""
        return Client(
            sse_client(
                url,
                headers=entry.headers or None,
                httpx_client_factory=http_client_factory(url),
            )
        )
    return Client(_http_transport(entry))


def describe_error(exc: BaseException) -> str:
    """把异常（含 ExceptionGroup）压成单行可读原因。"""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    text = " ".join(str(exc).split())
    return text or type(exc).__name__


async def handshake_all(
    config: Config, registry: HealthRegistry
) -> dict[str, "UpstreamConnection"]:
    """跨上游并发握手；急切、有限等待、fail-soft。

    返回本轮握手成功的连接（名字 → UpstreamConnection），由调用方在
    退出时用 :func:`close_all` 逆序回收。
    """
    index_root = config.index_root()
    timeout = config.settings.handshake_timeout
    connections = [UpstreamConnection(entry) for entry in config.servers.values()]
    await asyncio.gather(
        *(connection.start(timeout, registry, index_root) for connection in connections)
    )
    return {
        connection.entry.name: connection
        for connection in connections
        if connection.client is not None
    }


async def close_all(connections: Iterable["UpstreamConnection"]) -> None:
    """逆序关闭全部上游连接。"""
    for connection in reversed(list(connections)):
        await connection.stop()


def _log_failure(server: str, reason: str) -> None:
    print(f"grep-mcp-tools: {server} handshake failed: {reason}", file=sys.stderr)


# ---------- 上游常驻连接 ----------


class UpstreamConnection:
    """一个上游的常驻连接。

    anyio 的 cancel scope 要求 enter/exit 发生在同一任务（v2 SDK 的三种
    transport 都基于 anyio TaskGroup），因此每个连接由一条专用任务持有
    自己的 ``AsyncExitStack``：任务内完成 enter → 握手 → 等待关闭；
    其他任务只通过 ``client`` 发请求（SDK 的请求通道跨任务安全）。
    """

    def __init__(self, entry: ServerEntry) -> None:
        self.entry = entry
        self.client: Client | None = None
        self.lock = asyncio.Lock()  # 同一上游 invoke 串行化
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Future | None = None
        self._shutdown: asyncio.Event = asyncio.Event()

    async def start(
        self,
        timeout: int,
        registry: HealthRegistry,
        index_root: Path,
    ) -> None:
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._shutdown = asyncio.Event()
        self._task = asyncio.create_task(self._run(timeout, registry, index_root))
        await self._ready

    async def _run(
        self,
        timeout: int,
        registry: HealthRegistry,
        index_root: Path,
    ) -> None:
        assert self._ready is not None
        try:
            async with AsyncExitStack() as stack:
                try:
                    async with asyncio.timeout(timeout):
                        client = await stack.enter_async_context(client_for(self.entry))
                        listing = await client.list_tools()
                    tools = list(listing.tools)
                except TimeoutError:
                    reason = f"handshake timed out after {timeout}s"
                    registry.record_unreachable(self.entry.name, reason=reason)
                    _log_failure(self.entry.name, reason)
                    return
                except Exception as exc:  # 上游故障一律 fail-soft（黄金律 4）
                    reason = describe_error(exc)
                    registry.record_unreachable(self.entry.name, reason=reason)
                    _log_failure(self.entry.name, reason)
                    return

                self.client = client
                try:
                    write_tools_index(self.entry.name, tools, index_root)
                except Exception as exc:  # 写盘失败（磁盘满/权限/文件被占用）→ 不得谎报 live
                    reason = f"index write failed: {describe_error(exc)}"
                    registry.record_unreachable(self.entry.name, reason=reason)
                    _log_failure(self.entry.name, reason)
                    self.client = None
                    return
                registry.record_live(self.entry.name, tool_count=len(tools))
                self._ready.set_result(None)
                await self._shutdown.wait()
        finally:
            if not self._ready.done():
                self._ready.set_result(None)

    async def stop(self) -> None:
        self._shutdown.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:
                # 关闭路径上的清理噪音不阻塞整体退出
                pass
