"""6.2 握手循环集成测试（TDD：先于实现）。

真实三传输：stdio 子进程、Streamable HTTP、SSE 服务（127.0.0.1 随机端口）。
覆盖：三传输混合、死条目 fail-soft、握手超时有原因、0 工具上游、
并行不线性阻塞。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
import time
from pathlib import Path

from mcp.server import MCPServer

from grep_mcp_tools.config import Config, ServerEntry, Settings
from grep_mcp_tools.health import HealthRegistry
from grep_mcp_tools.index import close_all, handshake_all, load_live_tools

STDIO_SERVER = """
from mcp.server import MCPServer

mcp = MCPServer("tiny-stdio")

@mcp.tool()
def echo(text: str) -> str:
    \"\"\"Echo text.\"\"\"
    return text

mcp.run(transport="stdio")
"""

EMPTY_SERVER = """
from mcp.server import MCPServer

mcp = MCPServer("empty-server")
mcp.run(transport="stdio")
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_script(tmp_path: Path, name: str, source: str) -> Path:
    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    return script


def stdio_entry(name: str, script: Path) -> ServerEntry:
    return ServerEntry(name=name, transport="stdio", command=sys.executable, args=[str(script)])


def make_tool_server(label: str) -> MCPServer:
    mcp = MCPServer(label)

    @mcp.tool()
    def echo(text: str) -> str:
        """Echo text."""
        return text

    return mcp


async def wait_port_accepts(port: int, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.05)
    raise TimeoutError(f"server did not accept connections on port {port}")


async def stop_task(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(BaseException):
        await task


async def _hold_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await reader.read()
    finally:
        writer.close()


async def test_three_transports_handshake_together(tmp_path: Path) -> None:
    script = write_script(tmp_path, "tiny_stdio.py", STDIO_SERVER)
    http_port = free_port()
    sse_port = free_port()
    http_task = asyncio.create_task(
        make_tool_server("tiny-http").run_streamable_http_async(
            host="127.0.0.1", port=http_port
        )
    )
    sse_task = asyncio.create_task(
        make_tool_server("tiny-sse").run_sse_async(host="127.0.0.1", port=sse_port)
    )
    try:
        await wait_port_accepts(http_port)
        await wait_port_accepts(sse_port)
        index_dir = tmp_path / "idx"
        config = Config(
            servers={
                "hr": stdio_entry("hr", script),
                "itops": ServerEntry(
                    name="itops",
                    transport="http",
                    url=f"http://127.0.0.1:{http_port}/mcp",
                ),
                "audit": ServerEntry(
                    name="audit",
                    transport="sse",
                    url=f"http://127.0.0.1:{sse_port}/sse",
                ),
            },
            settings=Settings(handshake_timeout=15, index_dir=str(index_dir)),
            config_path=tmp_path / "config.json",
        )
        registry = HealthRegistry()
        connections = await handshake_all(config, registry)
        try:
            assert set(connections) == {"hr", "itops", "audit"}
            assert registry.unavailable_count() == 0

            for name in ("hr", "itops", "audit"):
                entry = registry.get(name)
                assert entry is not None
                assert entry.state == "live"
                assert entry.tools == 1
                assert (index_dir / f"{name}.jsonl").is_file()

            assert {t.server for t in load_live_tools(["hr", "itops", "audit"], index_dir)} == {
                "hr",
                "itops",
                "audit",
            }
        finally:
            await close_all(connections.values())
    finally:
        await stop_task(http_task)
        await stop_task(sse_task)


async def test_dead_entry_is_fail_soft_and_does_not_block(tmp_path: Path) -> None:
    script = write_script(tmp_path, "tiny_stdio.py", STDIO_SERVER)
    dead_port = free_port()  # 无人监听
    config = Config(
        servers={
            "hr": stdio_entry("hr", script),
            "dead_http": ServerEntry(
                name="dead_http",
                transport="http",
                url=f"http://127.0.0.1:{dead_port}/mcp",
            ),
        },
        settings=Settings(handshake_timeout=10, index_dir=str(tmp_path / "idx")),
        config_path=tmp_path / "config.json",
    )
    registry = HealthRegistry()
    started = time.perf_counter()
    connections = await handshake_all(config, registry)
    elapsed = time.perf_counter() - started
    try:
        assert set(connections) == {"hr", "dead_http"}  # config 镜像：死者也在场
        assert connections["dead_http"].client is None  # 判死单条件
        live = registry.get("hr")
        assert live is not None and live.state == "live"
        dead = registry.get("dead_http")
        assert dead is not None
        assert dead.state == "unreachable"
        assert dead.reason
        assert elapsed < 10.0
    finally:
        await close_all(connections.values())


async def test_handshake_timeout_recorded_with_reason(tmp_path: Path) -> None:
    port = free_port()
    server = await asyncio.start_server(_hold_connection, "127.0.0.1", port)
    try:
        config = Config(
            servers={
                "hang": ServerEntry(
                    name="hang",
                    transport="http",
                    url=f"http://127.0.0.1:{port}/mcp",
                ),
            },
            settings=Settings(handshake_timeout=1, index_dir=str(tmp_path / "idx")),
            config_path=tmp_path / "config.json",
        )
        registry = HealthRegistry()
        started = time.perf_counter()
        connections = await handshake_all(config, registry)
        elapsed = time.perf_counter() - started

        assert set(connections) == {"hang"}  # config 镜像：超时者也在场
        assert connections["hang"].client is None
        entry = registry.get("hang")
        assert entry is not None
        assert entry.state == "unreachable"
        assert "timed out" in (entry.reason or "").lower()
        assert elapsed < 5.0
    finally:
        server.close()
        await server.wait_closed()


async def test_zero_tool_upstream_live_zero_and_no_index_file(tmp_path: Path) -> None:
    script = write_script(tmp_path, "empty_server.py", EMPTY_SERVER)
    index_dir = tmp_path / "idx"
    config = Config(
        servers={"empty_srv": stdio_entry("empty_srv", script)},
        settings=Settings(handshake_timeout=15, index_dir=str(index_dir)),
        config_path=tmp_path / "config.json",
    )
    registry = HealthRegistry()
    connections = await handshake_all(config, registry)
    try:
        assert set(connections) == {"empty_srv"}
        entry = registry.get("empty_srv")
        assert entry is not None
        assert entry.state == "live"
        assert entry.tools == 0
        assert not (index_dir / "empty_srv.jsonl").exists()
    finally:
        await close_all(connections.values())


async def test_zero_tool_upstream_removes_stale_index_file(tmp_path: Path) -> None:
    """上一轮有工具、本轮 0 工具：旧索引文件必须被删除（否则幽灵工具）。"""
    script = write_script(tmp_path, "empty_server.py", EMPTY_SERVER)
    index_dir = tmp_path / "idx"
    index_dir.mkdir(parents=True)
    stale = index_dir / "empty_srv.jsonl"
    stale.write_text(
        '{"server":"empty_srv","name":"old_tool","title":null,'
        '"description":"stale","inputSchema":{"type":"object","properties":{}},'
        '"annotations":null}\n',
        encoding="utf-8",
    )
    config = Config(
        servers={"empty_srv": stdio_entry("empty_srv", script)},
        settings=Settings(handshake_timeout=15, index_dir=str(index_dir)),
        config_path=tmp_path / "config.json",
    )
    registry = HealthRegistry()

    connections = await handshake_all(config, registry)
    try:
        assert set(connections) == {"empty_srv"}
        assert not stale.exists()
    finally:
        await close_all(connections.values())


async def test_index_write_failure_is_fail_soft_not_false_live(
    tmp_path: Path, monkeypatch
) -> None:
    """写盘失败（磁盘满/权限/文件被占用）不得谎报 live。"""
    import grep_mcp_tools.index as index_module

    script = write_script(tmp_path, "tiny_stdio.py", STDIO_SERVER)

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(index_module, "write_tools_index", boom)

    config = Config(
        servers={"hr": stdio_entry("hr", script)},
        settings=Settings(handshake_timeout=15, index_dir=str(tmp_path / "idx")),
        config_path=tmp_path / "config.json",
    )
    registry = HealthRegistry()

    connections = await handshake_all(config, registry)
    try:
        assert set(connections) == {"hr"}  # config 镜像：写盘失败者也在场
        assert connections["hr"].client is None
        entry = registry.get("hr")
        assert entry is not None
        assert entry.state == "unreachable"
        assert "index write failed" in (entry.reason or "")
    finally:
        await close_all(connections.values())
