"""2.1/2.2/2.5/2.6 刷新循环：轮语义、轮尾同步块、异常兜底、退出时序（TDD 先红）。

FakeConnection 为 duck-type 假对象（refresh 记录调用/并发/行为）；
ProxyServer 用真实实例（轮尾 known_tools 重建与签名 diff 是真实逻辑）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from grep_mcp_tools.config import Config, Settings
from grep_mcp_tools.health import HealthRegistry
from grep_mcp_tools.server import ProxyServer, _refresh_round, _shutdown, run_refresh_loop


class FakeConnection:
    def __init__(self, name: str, behavior=None) -> None:
        self.entry = SimpleNamespace(name=name)
        self.lock = asyncio.Lock()
        self.client = object()
        self.refresh_calls = 0
        self.behavior = behavior
        self.in_flight = 0
        self.max_in_flight = 0

    async def refresh(self, timeout, registry, index_root) -> None:
        if self.lock.locked():  # 与真实 UpstreamConnection 锁纪律一致：忙则本轮跳过
            return
        self.refresh_calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            if self.behavior is not None:
                self.behavior(self, registry)
        finally:
            self.in_flight -= 1


def make_config(tmp_path: Path, interval: int = 1) -> Config:
    return Config(
        servers={},
        settings=Settings(refresh_interval=interval, index_dir=str(tmp_path / "idx")),
        config_path=tmp_path / "config.json",
    )


def seed_index(tmp_path: Path, server: str, tools: list[str]) -> Path:
    idx = tmp_path / "idx"
    idx.mkdir(parents=True, exist_ok=True)
    path = idx / f"{server}.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {
                    "server": server,
                    "name": name,
                    "title": None,
                    "description": f"demo {name}",
                    "inputSchema": {},
                    "annotations": None,
                },
                ensure_ascii=False,
            )
            + "\n"
            for name in tools
        ),
        encoding="utf-8",
    )
    return path


def make_proxy(config: Config, registry: HealthRegistry) -> ProxyServer:
    return ProxyServer(config, registry, {}, index_root=config.index_root())


async def test_refresh_round_gathers_all_and_syncs_tail(tmp_path: Path) -> None:
    """一轮 = 并发 refresh 全部上游 + 轮尾三件事（status.json/known_tools/签名）。"""
    config = make_config(tmp_path)
    registry = HealthRegistry()
    registry.record_live("a", tool_count=2)
    registry.record_live("b", tool_count=1)
    seed_index(tmp_path, "a", ["t1", "t2"])
    seed_index(tmp_path, "b", ["u1"])
    proxy = make_proxy(config, registry)
    assert proxy._known_tools == {"a": {"t1", "t2"}, "b": {"u1"}}

    # 本轮 b 死亡：registry 翻转（模拟 refresh 内部已完成）
    def b_dies(conn, reg):
        reg.record_unreachable("b", reason="killed")

    conns = {"a": FakeConnection("a"), "b": FakeConnection("b", behavior=b_dies)}
    await _refresh_round(proxy, config, registry, conns)

    assert conns["a"].refresh_calls == 1
    assert conns["b"].refresh_calls == 1
    # 轮尾：_status.json 重写且与注册表一致
    status = json.loads((config.index_root() / "_status.json").read_text(encoding="utf-8"))
    assert status["servers"]["b"]["state"] == "unreachable"
    assert status["servers"]["a"]["state"] == "live"
    # 轮尾：known_tools 重建（b 不再 live → 其工具不再可校验）
    assert proxy._known_tools == {"a": {"t1", "t2"}}


async def test_refresh_round_skips_locked_upstream_only(tmp_path: Path) -> None:
    """锁纪律循环面：被锁上游跳过，不阻塞同轮其他上游（并发 gather）。"""
    config = make_config(tmp_path)
    registry = HealthRegistry()
    proxy = make_proxy(config, registry)
    conns = {"busy": FakeConnection("busy"), "free": FakeConnection("free")}
    async with conns["busy"].lock:  # 模拟长 invoke 持锁
        await asyncio.wait_for(_refresh_round(proxy, config, registry, conns), timeout=2.0)
    assert conns["free"].refresh_calls == 1  # 同轮其他上游照常
    assert conns["busy"].refresh_calls == 0  # 被锁上游本轮跳过


async def test_refresh_loop_survives_round_exception(
    tmp_path: Path, capsys
) -> None:
    """轮级异常兜底：失败轮一行 stderr、循环不死、下一轮照跑。"""
    config = make_config(tmp_path, interval=1)
    registry = HealthRegistry()
    proxy = make_proxy(config, registry)

    def boom(conn, reg):
        raise RuntimeError("round blew up")

    conn = FakeConnection("a", behavior=boom)
    task = asyncio.create_task(run_refresh_loop(proxy, config, registry, {"a": conn}))
    try:
        await asyncio.sleep(1.3)  # interval=1 → 至少两轮
        assert conn.refresh_calls >= 2  # 循环未死
        err = capsys.readouterr().err
        assert "refresh" in err.lower()  # 每个失败轮一行
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_refresh_loop_quiet_on_healthy_rounds(tmp_path: Path, capsys) -> None:
    """日志纪律：正常轮 stderr 零新增。"""
    config = make_config(tmp_path, interval=1)
    registry = HealthRegistry()
    proxy = make_proxy(config, registry)
    conn = FakeConnection("a")
    task = asyncio.create_task(run_refresh_loop(proxy, config, registry, {"a": conn}))
    try:
        await asyncio.sleep(0.2)
        assert conn.refresh_calls >= 1
        assert capsys.readouterr().err == ""
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_shutdown_cancels_refresh_before_closing_connections() -> None:
    """退出时序：先 cancel 刷新任务并等待结束，再 close_all（防孤儿进程竞态）。"""
    events: list[str] = []

    async def endless_refresh() -> None:
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            events.append("refresh-cancelled")
            raise

    class FakeConn:
        async def stop(self) -> None:
            events.append("stopped")

    refresh_task = asyncio.create_task(endless_refresh())
    await asyncio.sleep(0.02)
    await _shutdown(refresh_task, [FakeConn(), FakeConn()])

    assert events[0] == "refresh-cancelled"
    assert events[1:] == ["stopped", "stopped"]  # cancel 先于连接回收
