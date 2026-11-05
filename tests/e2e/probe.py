"""B02 交互探针：真实启动路径（console script + --config）的人工/管道验收工具。

用法（仓库根目录）:
    uv run python tests/e2e/probe.py            # 交互模式
    type input.txt | uv run python tests/e2e/probe.py   # 管道驱动

输入:
    leave                      关键词搜索（空白分隔 = 多个关键词）
    invoke hr__get_employee {"employee_id": "E-1"}   透传调用
    status                     健康快照
    tools                      拉取 tools/list，打印 search_mcp_tool 的
                               description（验证服务清单动态注入）
    kill itops                 停掉 probe 自己拉起的 demo server（itops/
                               fleet_gen/docs；hr 与 broken 由代理 spawn，
                               探针无句柄）
    start itops                重新拉起（驱动刷新自愈演示）

config 含 5 上游（hr stdio / itops+fleet_gen Streamable HTTP / docs SSE /
broken 启动即崩）+ refresh_interval=30——死亡→翻转→恢复→list_changed 的
完整自愈环演示节奏。收到的 tools/list_changed 通知打印到 stderr。
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

# probe 自己 Popen 的远程型 demo（kill/start 演示目标）与端口键位
REMOTE_DEMOS: dict[str, tuple[str, int]] = {}  # name -> (script, port)，运行期填充
_live_procs: dict[str, subprocess.Popen] = {}  # name -> Popen，运行期填充


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


def write_demo_config(root: Path, fleet_port: int, itops_port: int, docs_port: int) -> Path:
    config = {
        "mcpServers": {
            "hr": {
                "command": sys.executable,
                "args": [str(DEMO_DIR / "hr.py")],
            },
            "itops": {"url": f"http://127.0.0.1:{itops_port}/mcp"},
            "fleet_gen": {"url": f"http://127.0.0.1:{fleet_port}/mcp"},
            "docs": {"url": f"http://127.0.0.1:{docs_port}/sse", "type": "sse"},
            "broken": {
                "command": sys.executable,
                "args": [str(DEMO_DIR / "broken.py")],
            },
        },
        "settings": {
            "threshold": 5,  # 演示值：region 植入 6+ 工具即触发超量路径
            "handshake_timeout": 15,
            "refresh_interval": 30,  # 演示节奏：一轮刷新 30s（开发/测试期统一值）
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


async def on_message(message) -> None:
    """打印代理发来的 list_changed 通知（旧代 initialize 推送观察点）。

    注：SDK 2.3.0 Client 默认协商 2026-07-28 新代协议，此模式下变更通知
    只经 subscriptions/listen 流——旧代推送对它无效，本观察点留作
    旧代客户端形态的占位；新代事件观察见 listen_printer。
    """
    method = getattr(message, "method", None) or (
        message.get("method") if isinstance(message, dict) else None
    )
    if method and "list_changed" in str(method):
        print(f"[probe] received notification: {method}", file=sys.stderr)


async def listen_printer(client: Client) -> None:
    """订阅 subscriptions/listen 并打印收到的变更事件（新代观察点）。"""
    try:
        async with client.listen(tools_list_changed=True) as sub:
            async for event in sub:
                print(
                    f"[probe] received subscription event: {type(event).__name__}; "
                    "re-pull tools/list to see the new roster",
                    file=sys.stderr,
                )
    except Exception as exc:
        print(f"[probe] listen ended: {type(exc).__name__}: {exc}", file=sys.stderr)


async def run_probe() -> None:
    root = Path(tempfile.mkdtemp(prefix="grep-mcp-probe-"))
    fleet_port, itops_port, docs_port = pick_ports(3)
    _live_procs.update(
        {
            "fleet_gen": start_demo("fleet_gen.py", fleet_port),
            "itops": start_demo("itops.py", itops_port),
            "docs": start_demo("docs.py", docs_port),
        }
    )
    REMOTE_DEMOS.update(
        {
            "fleet_gen": ("fleet_gen.py", fleet_port),
            "itops": ("itops.py", itops_port),
            "docs": ("docs.py", docs_port),
        }
    )
    try:
        for name, (_script, port) in REMOTE_DEMOS.items():
            wait_port_accepts(port)
        config_path = write_demo_config(root, fleet_port, itops_port, docs_port)
        index_dir = root / "tools_index"
        command = console_script()

        print(
            f"probe: starting {command[0]} --config {config_path}",
            file=sys.stderr,
        )
        print(
            f"probe: ports fleet_gen={fleet_port} itops={itops_port} docs={docs_port}; "
            "refresh_interval=30s; hr/broken are proxied stdio children",
            file=sys.stderr,
        )
        print(
            "probe: type keywords, 'invoke <target> <json>', 'status', 'tools', "
            "'kill/start <server>'; EOF exits.",
            file=sys.stderr,
        )

        params = StdioServerParameters(command=command[0], args=[*command[1:], "--config", str(config_path)])
        async with Client(params, message_handler=on_message) as client:
            listener = asyncio.create_task(listen_printer(client))
            try:
                await _command_loop(client, index_dir)
            finally:
                listener.cancel()  # EOF 与异常路径共用：先收订阅任务再断开
                with contextlib.suppress(asyncio.CancelledError):
                    await listener
    finally:
        for proc in _live_procs.values():
            with contextlib.suppress(Exception):
                proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)
        _live_procs.clear()


async def _command_loop(client: Client, index_dir: Path) -> None:
    """交互循环：关键词搜索 / invoke / status / tools / kill / start。"""
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

        if line == "tools":
            listing = await client.list_tools()
            by_name = {tool.name: tool for tool in listing.tools}
            print("--- search_mcp_tool description ---")
            print(by_name["search_mcp_tool"].description or "(empty)")
            print("--- end ---")
            continue

        # 前缀匹配（非首 token）：避免劫持名为 kill/start 的关键词搜索
        if line.startswith("kill ") or line.startswith("start "):
            parts = line.split()
            if len(parts) != 2 or parts[1] not in REMOTE_DEMOS:
                print(
                    f"usage: {parts[0]} <{'|'.join(sorted(REMOTE_DEMOS))}> "
                    "(hr/broken are proxied stdio children - probe holds no handle)"
                )
                continue
            name = parts[1]
            if parts[0] == "kill":
                proc = _live_procs.get(name)
                if proc is None or proc.poll() is not None:
                    print(f"[probe] {name} is not running")
                    continue
                proc.terminate()
                proc.wait(timeout=10)
                print(f"[probe] killed {name}; wait one refresh round (~30s)")
            else:
                if name in _live_procs and _live_procs[name].poll() is None:
                    print(f"[probe] {name} already running")
                    continue
                script, port = REMOTE_DEMOS[name]
                _live_procs[name] = start_demo(script, port)
                wait_port_accepts(port)
                print(f"[probe] started {name}; healing next refresh round (~30s)")
            continue

        if line.startswith("invoke "):
            parts = line.split(maxsplit=2)
            if len(parts) < 2:
                print("usage: invoke <target> <json>")
                continue
            target = parts[1]
            try:
                payload = json.loads(parts[2]) if len(parts) > 2 else {}
            except json.JSONDecodeError as exc:
                print(f"usage: invoke <target> <json> (bad json: {exc})")
                continue
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


def main() -> int:
    asyncio.run(run_probe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
