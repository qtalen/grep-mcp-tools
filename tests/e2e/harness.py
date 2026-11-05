"""B03 e2e harness：subprocess 黑盒的进程、配置与协议夹具。

设计来源：`openspec/changes/b03-e2e-matrix/design.md`『e2e 唯一形态 =
subprocess 黑盒』与批次文档『执行结构』。本模块只做机械工具：
- 端口/等待：从 `tests/shared/proc.py` re-export（`free_port` / `pick_ports` /
  `wait_port_accepts` / `wait_until` / `wait_until_async`），e2e 与 agent 两树共用；
- 配置：`write_config` / `read_index_lines`；
- 代理进程：`ProxyProcess`（stderr 行缓冲 + 退出时序）；
- 裸行 JSON-RPC 客户端：`RawSession`（共享同一代理进程，SDK Client 会自己
  spawn 进程，不能用于 session 共享）；
- 假上游进程：`spawn_demo` / `DemoProcess`；
- SDK 客户端：`sdk_client`（等价 probe.py 的真实下游形态）。

等待纪律：一切"等刷新生效"的断言走 `wait_until`，禁止固定 sleep 后断言。
"""

from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from tests.shared.proc import (
    free_port,
    pick_ports,
    wait_port_accepts,
    wait_until,
    wait_until_async,
)

__all__ = [
    "DEMO_DIR",
    "ProxyProcess",
    "RawSession",
    "DemoProcess",
    "free_port",
    "pick_ports",
    "wait_port_accepts",
    "write_config",
    "read_index_lines",
    "spawn_demo",
    "sdk_client",
    "wait_until",
    "wait_until_async",
]

DEMO_DIR = Path(__file__).resolve().parent / "demo_servers"

PROTOCOL_VERSION = "2025-11-25"


# ---------- 配置与索引 ----------


def write_config(
    directory: Path, servers: dict, settings: dict | None = None
) -> Path:
    """写 `directory/config.json`：servers 原样进 mcpServers，settings 合并默认 index_dir。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    merged_settings: dict = {"index_dir": str(directory / "tools_index")}
    if settings:
        merged_settings.update(settings)
    config = {"mcpServers": servers, "settings": merged_settings}
    path = directory / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_index_lines(index_dir: Path, server: str) -> list[str]:
    """读 `<index_dir>/<server>.jsonl` 的全部行。"""
    return (Path(index_dir) / f"{server}.jsonl").read_text(encoding="utf-8").splitlines()


# ---------- 代理进程 ----------


class ProxyProcess:
    """`python -m grep_mcp_tools --config <path>` 子进程（binary 管道）。"""

    def __init__(
        self,
        config_path: Path,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.cwd = cwd
        self.env = env
        self.proc: subprocess.Popen | None = None
        self.stderr_lines: list[str] = []
        self._threads: list[threading.Thread] = []

    def start(self) -> "ProxyProcess":
        """spawn 代理进程并起 stderr 行读者线程。"""
        if self.proc is not None and self.proc.poll() is None:
            return self
        import os

        child_env = None
        if self.env is not None:
            child_env = {**os.environ, **self.env}
        self.stderr_lines = []
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "grep_mcp_tools",
                "--config",
                str(self.config_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.cwd) if self.cwd is not None else None,
            env=child_env,
        )
        reader = threading.Thread(target=self._read_stderr, daemon=True)
        reader.start()
        self.register_thread(reader)
        return self

    def register_thread(self, thread: threading.Thread) -> None:
        """登记读者线程，stop() 时统一 join（含 RawSession 的 stdout 读者）。"""
        self._threads.append(thread)

    def _read_stderr(self) -> None:
        stderr = self.proc.stderr if self.proc is not None else None
        if stderr is None:
            return
        while True:
            raw = stderr.readline()
            if not raw:
                break
            self.stderr_lines.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))

    def stderr_text(self) -> str:
        return "\n".join(self.stderr_lines)

    def wait_stderr(self, pattern: str, deadline: float = 20.0, start: int = 0) -> str:
        """轮询 stderr_lines[start:]，返回首个含 pattern 的行；超时 raise TimeoutError。"""
        end = time.monotonic() + deadline
        while True:
            for line in list(self.stderr_lines[start:]):
                if pattern in line:
                    return line
            if time.monotonic() >= end:
                raise TimeoutError(
                    f"stderr pattern {pattern!r} not seen within {deadline}s; "
                    f"tail={self.stderr_lines[-10:]}"
                )
            time.sleep(0.05)

    def wait_exit(self, timeout: float = 10.0) -> int | None:
        """等进程退出；返回 returncode，超时返回 None。"""
        if self.proc is None:
            return None
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def close_stdin(self) -> None:
        """关下游 stdin（优雅退出路径的触发点）。"""
        if self.proc is None:
            return
        stdin = self.proc.stdin
        if stdin is not None:
            with contextlib.suppress(Exception):
                stdin.close()

    def stop(self, timeout: float = 10.0) -> None:
        """优雅关 stdin → 等退出 → 必要时 terminate/kill → join 读者线程。"""
        if self.proc is None:
            return
        self.close_stdin()
        code = self.wait_exit(timeout)
        if code is None:
            with contextlib.suppress(Exception):
                self.proc.terminate()
            code = self.wait_exit(5.0)
        if code is None:
            with contextlib.suppress(Exception):
                self.proc.kill()
            self.wait_exit(5.0)
        for thread in list(self._threads):
            thread.join(timeout=5.0)
        self._threads.clear()

    def __enter__(self) -> "ProxyProcess":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


# ---------- 裸行 JSON-RPC 客户端 ----------


class RawSession:
    """手写 JSON-RPC 行客户端，绑定一个 ProxyProcess 的管道。

    用于共享同一代理进程（SDK ``Client`` 会自己 spawn 进程，不能用于
    session 共享），也是 stdout 零污染断言的观察口。
    """

    def __init__(self, proxy: ProxyProcess) -> None:
        self.proxy = proxy
        self.handshake_result: dict | None = None
        self.unparsed_lines: list[str] = []
        self._next_id = 0
        self._pending: dict[int, queue.Queue] = {}
        self._notifications: list[dict] = []
        self._notification_queue: queue.Queue = queue.Queue()
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._id_lock = threading.Lock()

    # ---- 生命周期 ----

    def open(self) -> "RawSession":
        """起 stdout 读者线程 → initialize → notifications/initialized。"""
        if self.proxy.proc is None:
            raise RuntimeError("ProxyProcess is not started")
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self.proxy.register_thread(self._reader)

        self.handshake_result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "e2e", "version": "0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def close(self) -> None:
        """只标记关闭并放开挂起请求；进程回收由 ProxyProcess.stop() 负责。"""
        self._pending.clear()
        if self._reader is not None:
            self._reader.join(timeout=0)

    def __enter__(self) -> "RawSession":
        return self.open()

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---- 收 ----

    def _read_stdout(self) -> None:
        stdout = self.proxy.proc.stdout if self.proxy.proc is not None else None
        if stdout is None:
            return
        while True:
            raw = stdout.readline()
            if not raw:
                break
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                self.unparsed_lines.append(text)
                continue
            if not isinstance(message, dict):
                self.unparsed_lines.append(text)
                continue
            if "method" in message and "id" in message:
                self._reply_method_not_found(message)
            elif "method" in message:
                self._notifications.append(message)
                self._notification_queue.put(message)
            elif "id" in message:
                box = self._pending.get(message["id"])
                if box is not None:
                    box.put(message)
            else:
                self.unparsed_lines.append(text)

    def _reply_method_not_found(self, message: dict) -> None:
        """本 harness 不声明任何客户端能力；服务端请求一律回复方法不存在。"""
        with contextlib.suppress(Exception):
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {
                        "code": -32601,
                        "message": f"method not found: {message.get('method')}",
                    },
                }
            )

    # ---- 发 ----

    def _send(self, message: dict) -> None:
        if self.proxy.proc is None or self.proxy.proc.stdin is None:
            raise RuntimeError("ProxyProcess stdio is not available")
        payload = json.dumps(message, ensure_ascii=False) + "\n"
        with self._write_lock:
            self.proxy.proc.stdin.write(payload.encode("utf-8"))
            self.proxy.proc.stdin.flush()

    def _next_request_id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    # ---- 请求/响应 ----

    def request(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        """发请求并返回 result；JSON-RPC error 抛 AssertionError（带 error 内容）。"""
        request_id = self._next_request_id()
        message: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        box: queue.Queue = queue.Queue()
        self._pending[request_id] = box
        try:
            self._send(message)
            try:
                response = box.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError(
                    f"no response to {method!r} (id={request_id}) within {timeout}s; "
                    f"stderr tail={self.proxy.stderr_lines[-10:]}"
                ) from None
        finally:
            self._pending.pop(request_id, None)
        if "error" in response:
            raise AssertionError(
                f"{method!r} returned JSON-RPC error: "
                f"{json.dumps(response['error'], ensure_ascii=False)}"
            )
        return response.get("result", {})

    def call_tool(
        self, name: str, arguments: dict | None = None, timeout: float = 30.0
    ) -> dict:
        """tools/call → result（含 content/isError）。"""
        return self.request(
            "tools/call",
            {"name": name, "arguments": arguments if arguments is not None else {}},
            timeout=timeout,
        )

    def call_tool_text(
        self, name: str, arguments: dict | None = None, timeout: float = 30.0
    ) -> str:
        """拼接 tools/call 结果中每块的 text。"""
        result = self.call_tool(name, arguments, timeout=timeout)
        return "".join(
            block.get("text", "")
            for block in result.get("content", [])
            if isinstance(block, dict)
        )

    def list_tools(self, timeout: float = 30.0) -> list[dict]:
        result = self.request("tools/list", {}, timeout=timeout)
        return list(result.get("tools", []))

    # ---- 通知 ----

    def read_notification(self, timeout: float = 10.0) -> dict | None:
        """取下一 条 notifications/* 消息；超时返回 None。"""
        try:
            return self._notification_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def notifications(self) -> list[dict]:
        return list(self._notifications)


# ---------- 假上游进程 ----------


@dataclass
class DemoProcess:
    """测试自己拉起的假上游（hr/broken 由代理 spawn，不在此列）。"""

    script: str
    port: int | None
    proc: subprocess.Popen

    def kill(self) -> None:
        if self.proc.poll() is None:
            with contextlib.suppress(Exception):
                self.proc.terminate()
        with contextlib.suppress(Exception):
            self.proc.wait(timeout=10)

    def stop(self) -> None:
        self.kill()

    def __enter__(self) -> "DemoProcess":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def spawn_demo(
    script_name: str, port: int | None = None, args: list[str] | None = None
) -> DemoProcess:
    """拉起 `DEMO_DIR/<script_name>`；给 port 时追加 `--port` 并等端口可连。"""
    command = [sys.executable, str(DEMO_DIR / script_name)]
    if port is not None:
        command += ["--port", str(port)]
    if args:
        command += args
    proc = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if port is not None:
        wait_port_accepts(port)
    return DemoProcess(script=script_name, port=port, proc=proc)


# ---------- SDK 客户端 ----------


@asynccontextmanager
async def sdk_client(
    config_path: Path, **client_kwargs: Any
) -> AsyncIterator[Client]:
    """真实下游形态：SDK Client（stdio）拉起代理。参考 probe.py:205-206。"""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "grep_mcp_tools", "--config", str(config_path)],
    )
    async with Client(params, **client_kwargs) as client:
        yield client
