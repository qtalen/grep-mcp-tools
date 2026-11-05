"""进程/端口/等待的共享工具。

从 `tests/e2e/harness.py` 抽出，供 e2e 与 agent 两棵测试树共用；行为与
docstring 保持原样。harness 仍然 re-export 这些名字，现有 import 路径不变。
"""

from __future__ import annotations

import asyncio
import inspect
import socket
import time
from typing import Any, Callable

__all__ = [
    "free_port",
    "pick_ports",
    "wait_port_accepts",
    "wait_until",
    "wait_until_async",
]


def free_port() -> int:
    """向系统要一个空闲的 127.0.0.1 端口。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def pick_ports(count: int) -> list[int]:
    """取互不相同的空闲端口（连续 free_port() 可能重复——死条目会撞上活服务）。"""
    ports: list[int] = []
    while len(ports) < count:
        port = free_port()
        if port not in ports:
            ports.append(port)
    return ports


def wait_port_accepts(port: int, timeout: float = 20.0) -> None:
    """同步等 127.0.0.1:port 能建立 TCP 连接。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"demo server did not accept connections on port {port}")


def wait_until(
    predicate: Callable[[], Any], deadline: float = 10.0, interval: float = 0.1
) -> Any:
    """同步轮询直到 predicate 返回真值；超时 raise TimeoutError。"""
    end = time.monotonic() + deadline
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= end:
            raise TimeoutError(f"condition not met within {deadline}s: {predicate!r}")
        time.sleep(interval)


async def wait_until_async(
    predicate: Callable[[], Any], deadline: float = 10.0, interval: float = 0.1
) -> Any:
    """异步轮询版（predicate 可为协程函数）。"""
    end = time.monotonic() + deadline
    while True:
        value = predicate()
        if inspect.isawaitable(value):
            value = await value
        if value:
            return value
        if time.monotonic() >= end:
            raise TimeoutError(f"condition not met within {deadline}s: {predicate!r}")
        await asyncio.sleep(interval)
