"""B03 e2e fixture 编成。

- session 级 `std`：hr（stdio）+ itops（HTTP）+ audit（SSE）+ dead_http，
  一次拉起承载多数 A/B/C/D 用例，共用一条裸行 JSON-RPC 会话。
- function 级 `launch`：变体工厂，每次调用独享目录/配置/代理进程。

fixture 编成依据：`docs/design/batches/B03-e2e-matrix.md`『执行结构』。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from tests.e2e.harness import (
    DEMO_DIR,
    DemoProcess,
    ProxyProcess,
    RawSession,
    pick_ports,
    spawn_demo,
    write_config,
)

__all__ = ["StdStack", "Variant", "launch", "std"]


@dataclass
class StdStack:
    """session 级标准代理：进程、会话、配置与索引目录。"""

    proxy: ProxyProcess
    session: RawSession
    config_path: Path
    index_dir: Path
    root: Path
    itops_port: int
    audit_port: int


@pytest.fixture(scope="session")
def std(tmp_path_factory: pytest.TempPathFactory) -> StdStack:
    """标准共享代理（hr + itops + audit + dead_http）；teardown 逆序清理。"""
    root = tmp_path_factory.mktemp("std")
    itops_port, audit_port, dead_port = pick_ports(3)
    demos: list[DemoProcess] = []
    proxy: ProxyProcess | None = None
    session: RawSession | None = None
    try:
        demos.append(spawn_demo("itops.py", itops_port))
        demos.append(spawn_demo("audit.py", audit_port))
        config_path = write_config(
            root,
            {
                "hr": {"command": sys.executable, "args": [str(DEMO_DIR / "hr.py")]},
                "itops": {"url": f"http://127.0.0.1:{itops_port}/mcp"},
                "audit": {"url": f"http://127.0.0.1:{audit_port}/sse", "type": "sse"},
                "dead_http": {"url": f"http://127.0.0.1:{dead_port}/mcp"},
            },
            settings={"handshake_timeout": 10, "refresh_interval": 600},
        )
        proxy = ProxyProcess(config_path).start()
        session = RawSession(proxy).open()
    except BaseException:
        # 启动失败也清理已创建资源
        if session is not None:
            session.close()
        if proxy is not None:
            proxy.stop()
        for demo in reversed(demos):
            demo.stop()
        raise

    stack = StdStack(
        proxy=proxy,
        session=session,
        config_path=config_path,
        index_dir=root / "tools_index",
        root=root,
        itops_port=itops_port,
        audit_port=audit_port,
    )
    try:
        yield stack
    finally:
        session.close()
        proxy.stop()
        for demo in reversed(demos):
            demo.stop()


@dataclass
class Variant:
    """function 级变体：独享目录与代理进程。"""

    root: Path
    config_path: Path
    index_dir: Path
    proxy: ProxyProcess
    session: RawSession | None


@pytest.fixture
def launch(tmp_path: Path) -> Callable[..., Variant]:
    """变体工厂：`launch(servers, settings=None, cwd=None, env=None, open_session=True)`。

    每次调用用 `tmp_path/v{i}` 子目录；teardown 统一 stop 全部代理与
    （经 `launch.track_demo` 登记的）假上游。
    """
    proxies: list[ProxyProcess] = []
    demos: list[DemoProcess] = []
    counter = iter(range(10_000))

    def _launch(
        servers: dict,
        settings: dict | None = None,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        open_session: bool = True,
    ) -> Variant:
        root = tmp_path / f"v{next(counter)}"
        root.mkdir(parents=True, exist_ok=True)
        config_path = write_config(root, servers, settings)
        configured_index = settings.get("index_dir") if settings else None
        index_dir = Path(configured_index) if configured_index else root / "tools_index"
        proxy = ProxyProcess(config_path, cwd=cwd, env=env).start()
        proxies.append(proxy)
        session = RawSession(proxy).open() if open_session else None
        return Variant(
            root=root,
            config_path=config_path,
            index_dir=index_dir,
            proxy=proxy,
            session=session,
        )

    def track_demo(demo: DemoProcess) -> DemoProcess:
        """登记测试自拉的假上游，由 fixture teardown 兜底回收。"""
        demos.append(demo)
        return demo

    _launch.track_demo = track_demo  # type: ignore[attr-defined]
    try:
        yield _launch
    finally:
        for proxy in reversed(proxies):
            proxy.stop()
        for demo in reversed(demos):
            demo.stop()
