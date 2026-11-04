"""B01 交互探针：真实启动路径（console script + --config）的人工/管道验收工具。

用法（仓库根目录）:
    uv run python tests/e2e/probe.py            # 交互模式
    type input.txt | uv run python tests/e2e/probe.py   # 管道驱动

输入:
    leave                      关键词搜索（空白分隔 = 多个关键词）
    invoke hr__get_employee {"employee_id": "E-1"}   透传调用
    status                     健康快照

每次搜索后打印上下文账单：本次响应 chars/4 vs 直挂全部索引 chars/4。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import CallToolResult

DEMO_DIR = Path(__file__).resolve().parent / "demo_servers"


def free_port() -> int:
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


def console_script() -> list[str]:
    found = shutil.which("grep-mcp-tools")
    if found:
        return [found]
    return [sys.executable, "-m", "grep_mcp_tools"]


def wait_port_accepts(port: int, timeout: float = 20.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"demo server did not accept connections on port {port}")


def start_demo(script_name: str, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(DEMO_DIR / script_name), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def write_demo_config(root: Path, itops_port: int, audit_port: int, dead_port: int) -> Path:
    config = {
        "mcpServers": {
            "hr": {
                "command": sys.executable,
                "args": [str(DEMO_DIR / "hr.py")],
            },
            "itops": {"url": f"http://127.0.0.1:{itops_port}/mcp"},
            "audit": {"url": f"http://127.0.0.1:{audit_port}/sse", "type": "sse"},
            "dead_http": {"url": f"http://127.0.0.1:{dead_port}/mcp"},
        },
        "settings": {
            "threshold": 5,  # 演示值：region 植入 6 个工具即触发超量路径
            "handshake_timeout": 15,
            "index_dir": str(root / "tools_index"),
        },
    }
    path = root / "config.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def text_of(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if hasattr(block, "text"))


def index_chars(index_dir: Path) -> int:
    total = 0
    for path in sorted(index_dir.glob("*.jsonl")):
        total += sum(len(line) for line in path.read_text(encoding="utf-8").splitlines())
    return total


def print_bill(text: str, index_dir: Path) -> None:
    resident = index_chars(index_dir)
    print(
        f"[bill] this response ~{len(text) // 4} tokens "
        f"vs ~{resident // 4} tokens resident if all upstream tools were mounted directly; "
        f"index: {index_dir}",
        file=sys.stderr,
    )


async def run_probe() -> None:
    root = Path(tempfile.mkdtemp(prefix="grep-mcp-probe-"))
    itops_port, audit_port, dead_port = pick_ports(3)
    procs = [start_demo("itops.py", itops_port), start_demo("audit.py", audit_port)]
    try:
        wait_port_accepts(itops_port)
        wait_port_accepts(audit_port)
        config_path = write_demo_config(root, itops_port, audit_port, dead_port)
        index_dir = root / "tools_index"
        command = console_script()

        # 现场取证：dead 端口在握手前必须无人应答（防端口竞态）
        try:
            with socket.create_connection(("127.0.0.1", dead_port), timeout=1):
                print(
                    f"probe: WARNING dead port {dead_port} is unexpectedly listening",
                    file=sys.stderr,
                )
        except OSError:
            pass

        print(
            f"probe: starting {command[0]} --config {config_path}",
            file=sys.stderr,
        )
        print(
            f"probe: ports itops={itops_port} audit={audit_port} dead={dead_port}",
            file=sys.stderr,
        )
        print(
            "probe: type keywords, 'invoke <target> <json>', or 'status'; EOF exits.",
            file=sys.stderr,
        )

        params = StdioServerParameters(command=command[0], args=[*command[1:], "--config", str(config_path)])
        async with Client(params) as client:
            while True:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                if line == "status":
                    result = await client.call_tool("check_mcp_status", {})
                    print(text_of(result))
                    continue

                if line.startswith("invoke "):
                    parts = line.split(maxsplit=2)
                    if len(parts) < 2:
                        print("usage: invoke <target> <json>")
                        continue
                    target = parts[1]
                    payload = json.loads(parts[2]) if len(parts) > 2 else {}
                    result = await client.call_tool(
                        "invoke_this_tool", {"target": target, "params": payload}
                    )
                    print(text_of(result))
                    if result.is_error:
                        print("[is_error]", file=sys.stderr)
                    continue

                keywords = line.split()
                result = await client.call_tool("search_mcp_tool", {"keywords": keywords})
                text = text_of(result)
                print(text)
                print_bill(text, index_dir)
    finally:
        for proc in procs:
            with contextlib.suppress(Exception):
                proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)


def main() -> int:
    asyncio.run(run_probe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
