"""UpstreamConnection.refresh 刷新状态机（假 client 驱动，无真实传输）。

覆盖 tool-index delta：锁纪律（忙则跳过）、活连接重拉（成功/失败/超时）、
同回合自愈（死连接 stop+start）、client=None 不变式（非 live 态必置 None）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from grep_mcp_tools.config import ServerEntry
from grep_mcp_tools.health import HealthRegistry
from grep_mcp_tools.index import UpstreamConnection


def make_entry(name: str = "hr") -> ServerEntry:
    return ServerEntry(
        name=name, transport="stdio", command="python", args=["-c", "pass"]
    )


def make_tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name, title=None, description=f"demo {name}", input_schema={}, annotations=None
    )


class FakeClient:
    """可控的假上游 client：list_tools 按脚本返回 / 抛错 / 挂起。"""

    def __init__(
        self,
        tools: list | None = None,
        error: Exception | None = None,
        hang: float = 0.0,
    ) -> None:
        self._tools = tools if tools is not None else [make_tool("search")]
        self._error = error
        self._hang = hang

    async def list_tools(self) -> Any:
        if self._hang:
            await asyncio.sleep(self._hang)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(tools=list(self._tools))


def live_connection(client: FakeClient) -> UpstreamConnection:
    """构造一个"看起来 live"的连接：client 在、任务活着（模拟 _run 等 shutdown）。"""
    conn = UpstreamConnection(make_entry())
    conn.client = client

    async def fake_run() -> None:
        await conn._shutdown.wait()  # 与真 _run 同款等待，stop() 能将其唤醒

    conn._task = asyncio.create_task(fake_run())
    return conn


async def idle_task() -> asyncio.Task:
    """构造一个已完成的死任务。"""
    task = asyncio.create_task(asyncio.sleep(0))
    await task
    return task


async def test_refresh_skips_when_invoke_lock_held(tmp_path: Path) -> None:
    """锁纪律：长 invoke 进行中 → 跳过该上游本轮，不等待不中断。"""
    conn = live_connection(FakeClient(tools=[make_tool("a"), make_tool("b")]))
    registry = HealthRegistry()
    async with conn.lock:  # 模拟长 invoke 持锁
        task = asyncio.create_task(conn.refresh(1, registry, tmp_path))
        await asyncio.wait_for(task, timeout=2.0)
    # 未刷新：无索引文件、注册表无记录、client 原样
    assert not (tmp_path / "hr.jsonl").exists()
    assert registry.get("hr") is None
    assert conn.client is not None
    await conn.stop()


async def test_refresh_live_client_relists_and_reindexes(tmp_path: Path) -> None:
    """活连接重拉：复用 client 拉 list_tools、原子重写 jsonl、翻注册表。"""
    conn = live_connection(FakeClient(tools=[make_tool("a"), make_tool("b")]))
    registry = HealthRegistry()
    await conn.refresh(2, registry, tmp_path)

    entry = registry.get("hr")
    assert entry is not None and entry.state == "live" and entry.tools == 2
    lines = (tmp_path / "hr.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert conn.client is not None  # 活连接不被无谓重建
    await conn.stop()


async def test_refresh_relist_failure_restarts_same_round(tmp_path: Path) -> None:
    """重拉失败 → 同回合 stop+start（一次重试，终态即本轮结果）。"""
    conn = live_connection(FakeClient(error=RuntimeError("half-dead upstream")))
    calls: list[str] = []
    stops: list[str] = []

    async def fake_start(timeout, registry, index_root):
        calls.append("start")
        registry.record_live("hr", tool_count=3)

    async def fake_stop():
        stops.append("stop")
        await UpstreamConnection.stop(conn)  # 真实 stop：清 client 不变式

    conn.start = fake_start  # type: ignore[method-assign]
    conn.stop = fake_stop  # type: ignore[method-assign]
    registry = HealthRegistry()
    await conn.refresh(2, registry, tmp_path)

    assert calls == ["start"]
    assert stops == ["stop"]  # 同回合先 stop（含 client=None 不变式）
    assert conn.client is None  # stop 已把旧 client 置 None；假 start 不设 client
    entry = registry.get("hr")
    assert entry is not None and entry.state == "live" and entry.tools == 3
    assert not (tmp_path / "hr.jsonl").exists()  # 假 start 不落盘


async def test_refresh_relist_timeout_restarts_same_round(tmp_path: Path) -> None:
    """重拉超时（超过 handshake_timeout 上界）→ 按失败处理走同回合自愈。"""
    conn = live_connection(FakeClient(hang=5.0))
    calls: list[str] = []

    async def fake_start(timeout, registry, index_root):
        calls.append("start")

    conn.start = fake_start  # type: ignore[method-assign]
    registry = HealthRegistry()
    started = asyncio.get_running_loop().time()
    await conn.refresh(0.05, registry, tmp_path)  # timeout 上界 0.05s

    assert calls == ["start"]
    assert asyncio.get_running_loop().time() - started < 4.0  # 没等 hang 的 5s
    assert conn.client is None


async def test_refresh_dead_task_restarts_same_round(tmp_path: Path) -> None:
    """连接任务已死 → 同回合 stop+start（client=None 或任务 done 都算死）。"""
    conn = UpstreamConnection(make_entry())
    conn.client = None
    conn._task = await idle_task()
    calls: list[str] = []

    async def fake_start(timeout, registry, index_root):
        calls.append("start")
        registry.record_unreachable("hr", reason="process exited")

    conn.start = fake_start  # type: ignore[method-assign]
    registry = HealthRegistry()
    await conn.refresh(2, registry, tmp_path)

    assert calls == ["start"]
    assert conn.client is None
    entry = registry.get("hr")
    assert entry is not None and entry.state == "unreachable"


async def test_stop_clears_client_invariant(tmp_path: Path) -> None:
    """不变式：stop() 后 client 必为 None（防 invoke 单条件判死失真）。"""
    conn = live_connection(FakeClient())
    assert conn.client is not None
    await conn.stop()
    assert conn.client is None


async def test_refresh_index_write_failure_records_unreachable(tmp_path: Path) -> None:
    """刷新写盘失败 → 记 unreachable 原因自包含，不谎报 live。"""
    conn = live_connection(FakeClient(tools=[make_tool("a")]))
    registry = HealthRegistry()
    # 索引目录路径被一个普通文件占位 → mkdir 抛 FileExistsError（Windows 上
    # chmod 只读不可靠，文件占位跨平台稳定）
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    await conn.refresh(2, registry, blocker)
    await conn.stop()

    entry = registry.get("hr")
    assert entry is not None
    assert entry.state == "unreachable"
    assert "index write failed" in (entry.reason or "")
    assert conn.client is None  # 不谎报：非 live 态 client 必 None


@pytest.mark.parametrize("tools", [[], [make_tool("only")]])
async def test_refresh_zero_tools_deletes_stale_index(tmp_path: Path, tools: list) -> None:
    """0 工具上游：不生成（或删除残留）索引文件，注册表 live 0 tools。"""
    stale = tmp_path / "hr.jsonl"
    stale.write_text('{"server":"hr","name":"ghost"}\n', encoding="utf-8")
    conn = live_connection(FakeClient(tools=tools))
    registry = HealthRegistry()
    await conn.refresh(2, registry, tmp_path)

    entry = registry.get("hr")
    assert entry is not None and entry.state == "live"
    assert entry.tools == len(tools)
    if tools:
        assert stale.exists()
        assert len(stale.read_text(encoding="utf-8").splitlines()) == 1
    else:
        assert not stale.exists()
    await conn.stop()
