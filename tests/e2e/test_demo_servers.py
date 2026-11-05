"""8.1-8.3 假上游握手验证（真实子进程与传输）。"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
from pathlib import Path

from mcp import Client

from grep_mcp_tools.config import Config, ServerEntry, Settings
from grep_mcp_tools.health import HealthRegistry
from grep_mcp_tools.index import close_all, handshake_all
from grep_mcp_tools.server import ProxyServer

DEMO_DIR = Path(__file__).resolve().parent / "demo_servers"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def wait_port_accepts(port: int, timeout: float = 20.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.1)
    raise TimeoutError(f"demo server did not accept connections on port {port}")


async def start_remote(script_name: str, port: int) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(DEMO_DIR / script_name),
        "--port",
        str(port),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


async def stop_process(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=10)


def read_index_lines(index_dir: Path, server: str) -> list[str]:
    return (index_dir / f"{server}.jsonl").read_text(encoding="utf-8").splitlines()


async def test_hr_stdio_handshake_indexes_eight_tools(tmp_path: Path) -> None:
    index_dir = tmp_path / "idx"
    config = Config(
        servers={
            "hr": ServerEntry(
                name="hr",
                transport="stdio",
                command=sys.executable,
                args=[str(DEMO_DIR / "hr.py")],
            )
        },
        settings=Settings(handshake_timeout=20, index_dir=str(index_dir)),
        config_path=tmp_path / "config.json",
    )
    registry = HealthRegistry()
    connections = await handshake_all(config, registry)
    try:
        assert set(connections) == {"hr"}
        entry = registry.get("hr")
        assert entry is not None and entry.state == "live"
        assert entry.tools == 8
        lines = read_index_lines(index_dir, "hr")
        assert len(lines) == 8
    finally:
        await close_all(connections.values())


async def test_itops_http_handshake_indexes_twelve_tools(tmp_path: Path) -> None:
    port = free_port()
    proc = await start_remote("itops.py", port)
    try:
        await wait_port_accepts(port)
        index_dir = tmp_path / "idx"
        config = Config(
            servers={
                "itops": ServerEntry(
                    name="itops",
                    transport="http",
                    url=f"http://127.0.0.1:{port}/mcp",
                )
            },
            settings=Settings(handshake_timeout=20, index_dir=str(index_dir)),
            config_path=tmp_path / "config.json",
        )
        registry = HealthRegistry()
        connections = await handshake_all(config, registry)
        try:
            assert set(connections) == {"itops"}
            entry = registry.get("itops")
            assert entry is not None and entry.tools == 12
            lines = read_index_lines(index_dir, "itops")
            assert len(lines) == 12
            joined = "\n".join(lines)
            for name in ("firehose", "boom", "search", "region_deploy_status"):
                assert f'"name":"{name}"' in joined
        finally:
            await close_all(connections.values())
    finally:
        await stop_process(proc)


async def test_audit_sse_handshake_indexes_three_tools(tmp_path: Path) -> None:
    port = free_port()
    proc = await start_remote("audit.py", port)
    try:
        await wait_port_accepts(port)
        index_dir = tmp_path / "idx"
        config = Config(
            servers={
                "audit": ServerEntry(
                    name="audit",
                    transport="sse",
                    url=f"http://127.0.0.1:{port}/sse",
                )
            },
            settings=Settings(handshake_timeout=20, index_dir=str(index_dir)),
            config_path=tmp_path / "config.json",
        )
        registry = HealthRegistry()
        connections = await handshake_all(config, registry)
        try:
            assert set(connections) == {"audit"}
            entry = registry.get("audit")
            assert entry is not None and entry.tools == 3
            lines = read_index_lines(index_dir, "audit")
            assert len(lines) == 3
            assert any("audit_trail_retention" in line for line in lines)
        finally:
            await close_all(connections.values())
    finally:
        await stop_process(proc)


async def test_full_chain_search_invoke_passthrough_and_destructive_marker(
    tmp_path: Path,
) -> None:
    """真实链路：下游 Client → MCPServer → 代理 → 真实上游。

    覆盖 tasks 7.2/8.1 自报的三件事：100KB 原样透传、上游错误原样、
    搜索结果 [destructive] 标记（来自 demo 工具的 ToolAnnotations）。
    """
    port = free_port()
    proc = await start_remote("itops.py", port)
    try:
        await wait_port_accepts(port)
        index_dir = tmp_path / "idx"
        config = Config(
            servers={
                "hr": ServerEntry(
                    name="hr",
                    transport="stdio",
                    command=sys.executable,
                    args=[str(DEMO_DIR / "hr.py")],
                ),
                "itops": ServerEntry(
                    name="itops",
                    transport="http",
                    url=f"http://127.0.0.1:{port}/mcp",
                ),
            },
            settings=Settings(handshake_timeout=20, index_dir=str(index_dir)),
            config_path=tmp_path / "config.json",
        )
        registry = HealthRegistry()
        connections = await handshake_all(config, registry)
        try:
            assert set(connections) == {"hr", "itops"}
            proxy = ProxyServer(config, registry, connections)
            async with Client(proxy.mcp) as client:
                search = await client.call_tool(
                    "search_mcp_tool", {"keywords": ["leave"]}
                )
                search_text = "".join(
                    block.text for block in search.content if hasattr(block, "text")
                )
                assert "hr__approve_leave [destructive]" in search_text

                fire = await client.call_tool(
                    "invoke_this_tool",
                    {"target": "itops__firehose", "params": {}},
                )
                assert fire.is_error is False
                assert len(fire.content[0].text) == 102400

                boom = await client.call_tool(
                    "invoke_this_tool", {"target": "itops__boom", "params": {}}
                )
                assert boom.is_error is True
                assert "boom" in boom.content[0].text.lower()

                ok = await client.call_tool(
                    "invoke_this_tool",
                    {"target": "hr__get_employee", "params": {"employee_id": "E-1"}},
                )
                assert ok.is_error is False
                assert "Ada Lovelace" in ok.content[0].text
        finally:
            await close_all(connections.values())
    finally:
        await stop_process(proc)


async def test_fleet_gen_http_handshake_indexes_42_tools(tmp_path: Path) -> None:
    port = free_port()
    proc = await start_remote("fleet_gen.py", port)
    try:
        await wait_port_accepts(port)
        index_dir = tmp_path / "idx"
        config = Config(
            servers={
                "fleet_gen": ServerEntry(
                    name="fleet_gen",
                    transport="http",
                    url=f"http://127.0.0.1:{port}/mcp",
                )
            },
            settings=Settings(handshake_timeout=20, index_dir=str(index_dir)),
            config_path=tmp_path / "config.json",
        )
        registry = HealthRegistry()
        connections = await handshake_all(config, registry)
        try:
            entry = registry.get("fleet_gen")
            assert entry is not None and entry.state == "live"
            assert entry.tools == 42
            lines = read_index_lines(index_dir, "fleet_gen")
            assert len(lines) == 42
            assert all('"name":"region_' in line for line in lines)
        finally:
            await close_all(connections.values())
    finally:
        await stop_process(proc)


async def test_docs_sse_handshake_indexes_six_tools(tmp_path: Path) -> None:
    port = free_port()
    proc = await start_remote("docs.py", port)
    try:
        await wait_port_accepts(port)
        index_dir = tmp_path / "idx"
        config = Config(
            servers={
                "docs": ServerEntry(
                    name="docs",
                    transport="sse",
                    url=f"http://127.0.0.1:{port}/sse",
                )
            },
            settings=Settings(handshake_timeout=20, index_dir=str(index_dir)),
            config_path=tmp_path / "config.json",
        )
        registry = HealthRegistry()
        connections = await handshake_all(config, registry)
        try:
            entry = registry.get("docs")
            assert entry is not None and entry.state == "live"
            assert entry.tools == 6
            lines = read_index_lines(index_dir, "docs")
            assert len(lines) == 6
            joined = "\n".join(lines)
            assert '"name":"delete_document"' in joined
            assert any("region" in line for line in lines)
        finally:
            await close_all(connections.values())
    finally:
        await stop_process(proc)


async def test_broken_stdio_fails_soft(tmp_path: Path, capsys) -> None:
    index_dir = tmp_path / "idx"
    config = Config(
        servers={
            "broken": ServerEntry(
                name="broken",
                transport="stdio",
                command=sys.executable,
                args=[str(DEMO_DIR / "broken.py")],
            ),
            "hr": ServerEntry(
                name="hr",
                transport="stdio",
                command=sys.executable,
                args=[str(DEMO_DIR / "hr.py")],
            ),
        },
        settings=Settings(handshake_timeout=20, index_dir=str(index_dir)),
        config_path=tmp_path / "config.json",
    )
    registry = HealthRegistry()
    connections = await handshake_all(config, registry)
    try:
        broken = registry.get("broken")
        assert broken is not None and broken.state == "unreachable"
        hr = registry.get("hr")
        assert hr is not None and hr.state == "live"
        assert not (index_dir / "broken.jsonl").exists()
        assert (index_dir / "hr.jsonl").exists()
        # 失败轮出声：stderr 一行含服务名与原因（日志纪律）
        err = capsys.readouterr().err
        assert "broken handshake failed" in err
    finally:
        await close_all(connections.values())
