"""1.4 真实 UpstreamConnection 自愈往返（假 client 测不到的类）。

a) 曾 live → 重握手失败 → client=None 且 invoke 得"不可达"（非"工具不存在"）
b) tiny stdio 子进程死亡 → refresh 同回合自愈恢复（stdio respawn，无传输特判）
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from grep_mcp_tools.config import Config, ServerEntry, Settings
from grep_mcp_tools.health import HealthRegistry
from grep_mcp_tools.index import close_all, handshake_all
from grep_mcp_tools.server import ProxyServer

TINY_CRASHABLE = """
import os
from mcp.server import MCPServer

mcp = MCPServer("tiny")

@mcp.tool()
def echo(text: str) -> str:
    \"\"\"Echo text.\"\"\"
    return text

@mcp.tool()
def crash() -> str:
    \"\"\"Kill this server process immediately.\"\"\"
    os._exit(1)

mcp.run(transport="stdio")
"""


def make_config(tmp_path: Path, script: Path) -> Config:
    return Config(
        servers={
            "tiny": ServerEntry(
                name="tiny", transport="stdio", command=sys.executable, args=[str(script)]
            )
        },
        settings=Settings(handshake_timeout=20, index_dir=str(tmp_path / "idx")),
        config_path=tmp_path / "config.json",
    )


async def test_restart_failure_keeps_unreachable_invoke_error(tmp_path: Path) -> None:
    """曾 live → 死亡 → 重握手失败：invoke 必须报"不可达"而非"工具不存在"。"""
    script = tmp_path / "tiny_crashable.py"
    script.write_text(TINY_CRASHABLE, encoding="utf-8")
    config = make_config(tmp_path, script)
    registry = HealthRegistry()
    connections = await handshake_all(config, registry)
    try:
        conn = connections["tiny"]
        assert conn.client is not None
        proxy = ProxyServer(config, registry, connections)

        # 杀死子进程并让 respawn 注定失败（脚本消失 → spawn 秒退）；
        # crash 调用本身因进程死亡抛 Connection closed，属预期。
        with contextlib.suppress(Exception):
            await conn.client.call_tool("crash", {})
        script.unlink()
        await conn.refresh(20, registry, config.index_root())

        # 不变式与文案
        assert conn.client is None
        entry = registry.get("tiny")
        assert entry is not None and entry.state == "unreachable"
        result = await proxy.invoke("tiny__echo", {"text": "hi"})
        assert result.is_error is True
        text = result.content[0].text
        assert "unreachable" in text
        assert "does not exist" not in text
    finally:
        await close_all(connections.values())


async def test_stdio_death_refresh_heals_same_round(tmp_path: Path) -> None:
    """stdio 子进程死亡 → refresh 同回合 stop+start（respawn）恢复。"""
    script = tmp_path / "tiny_crashable.py"
    script.write_text(TINY_CRASHABLE, encoding="utf-8")
    config = make_config(tmp_path, script)
    registry = HealthRegistry()
    connections = await handshake_all(config, registry)
    try:
        conn = connections["tiny"]
        proxy = ProxyServer(config, registry, connections)

        # 死亡：invoke 报代理级错误（传输层故障自包含原因）；
        # crash 调用本身因进程死亡抛 Connection closed，属预期。
        with contextlib.suppress(Exception):
            await conn.client.call_tool("crash", {})
        broken = await proxy.invoke("tiny__echo", {"text": "hi"})
        assert broken.is_error is True
        assert "Proxy error" in broken.content[0].text

        # 同回合自愈：respawn 同一脚本 → live 恢复、invoke 可用
        await conn.refresh(20, registry, config.index_root())
        entry = registry.get("tiny")
        assert entry is not None and entry.state == "live" and entry.tools == 2
        assert conn.client is not None
        healed = await proxy.invoke("tiny__echo", {"text": "hi"})
        assert healed.is_error is False
        assert "hi" in healed.content[0].text
    finally:
        await close_all(connections.values())


async def test_startup_death_revived_by_refresh(tmp_path: Path) -> None:
    """真实链路：启动时上游不存在（握手失败）→ 服务出现 → refresh 复活。

    覆盖"启动时死→刷新中恢复自动补进索引"与 stop() 对从未握手成功的
    连接安全空跑后重握手的真实路径（假 revive 测不到）。
    """
    script = tmp_path / "late_server.py"  # 启动时还不存在
    config = make_config(tmp_path, script)
    registry = HealthRegistry()
    connections = await handshake_all(config, registry)
    try:
        conn = connections["tiny"]
        assert conn.client is None
        assert registry.get("tiny").state == "unreachable"

        # 服务出现（脚本落盘）→ 刷新自愈
        script.write_text(TINY_CRASHABLE, encoding="utf-8")
        await conn.refresh(20, registry, config.index_root())

        entry = registry.get("tiny")
        assert entry is not None and entry.state == "live" and entry.tools == 2
        assert conn.client is not None
        assert (config.index_root() / "tiny.jsonl").is_file()  # 索引补齐
    finally:
        await close_all(connections.values())


async def test_refresh_relist_timeout_cuts_through_real_transport(tmp_path: Path) -> None:
    """真实链路：半死 HTTP 上游（接受连接但不应答）重拉必须被超时上界打断。

    asyncio.timeout 需真正穿透 SDK 请求通道（假 client 只证明了机制存在）。
    """
    import asyncio as _asyncio
    import socket as _socket

    server = await _asyncio.start_server(
        lambda r, w: None,  # 收连接后挂住：TCP 通、MCP 不应答
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]
    try:
        config = Config(
            servers={
                "halfdead": ServerEntry(
                    name="halfdead",
                    transport="http",
                    url=f"http://127.0.0.1:{port}/mcp",
                )
            },
            settings=Settings(
                handshake_timeout=20, index_dir=str(tmp_path / "idx")
            ),
            config_path=tmp_path / "config.json",
        )
        registry = HealthRegistry()
        connections = await handshake_all(config, registry)
        conn = connections["halfdead"]
        try:
            assert registry.get("halfdead").state == "unreachable"  # 启动即握手超时
            started = _asyncio.get_running_loop().time()
            await conn.refresh(1, registry, config.index_root())  # 上界 1s
            elapsed = _asyncio.get_running_loop().time() - started

            assert elapsed < 5.0  # 超时穿透了真实传输，没有挂到 20s
            assert conn.client is None
            entry = registry.get("halfdead")
            assert entry is not None and entry.state == "unreachable"
        finally:
            await close_all(connections.values())
    finally:
        server.close()
        await server.wait_closed()
