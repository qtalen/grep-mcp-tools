"""E 区：协议与生命周期（tasks 8.1-8.7，含 3.2 的"先测后修"孤儿回收）。

依据：`docs/design/batches/B03-e2e-matrix.md`『E 协议与生命周期（5 例 + 6 另加）』
与『执行结构』（刷新簇两进程、等待纪律）。

用例分工：
- `std`（session 级只读）：全链路、长会话、`_status.json` 快照；
- 独享 `launch`：stdout 零污染、孤儿回收、退出耗时、刷新自愈、并发搜索；
- `sdk_client`（自己 spawn 代理）：新旧双代通知、DeepAgents 拉起形态。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import pytest
from mcp.server.subscriptions import ToolsListChanged

from tests.e2e.harness import (
    DEMO_DIR,
    RawSession,
    free_port,
    pick_ports,
    sdk_client,
    spawn_demo,
    wait_until,
    wait_until_async,
    write_config,
)

if TYPE_CHECKING:
    from tests.e2e.conftest import StdStack, Variant

PROXY_TOOLS = {"search_mcp_tool", "invoke_this_tool", "check_mcp_status"}
HR_TOOLS = 8
ITOPS_TOOLS = 12
MUTABLE_INITIAL = ["m0", "m1"]
MUTABLE_CHANGED = ["m0", "m2", "m3"]

HR_STDIO = {"command": sys.executable, "args": [str(DEMO_DIR / "hr.py")]}


# ---------- 小工具 ----------


def _text_of_blocks(result: dict) -> str:
    """裸行会话的 tools/call 结果 → 拼接文本。"""
    return "".join(
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict)
    )


def _sdk_text(result: Any) -> str:
    """SDK CallToolResult → 拼接文本。"""
    return "".join(
        getattr(block, "text", "")
        for block in result.content
        if hasattr(block, "text")
    )


def _description_of(tools: list[dict]) -> str:
    """tools/list 原始条目中的 `search_mcp_tool` description。"""
    for tool in tools:
        if tool.get("name") == "search_mcp_tool":
            return tool.get("description") or ""
    raise AssertionError("search_mcp_tool missing from tools/list")


def _sdk_description(listing: Any) -> str:
    for tool in listing.tools:
        if tool.name == "search_mcp_tool":
            return tool.description or ""
    raise AssertionError("search_mcp_tool missing from tools/list")


def _safe_search(session: RawSession, keywords: list[str]) -> str:
    """轮询用搜索：刷新窗口内的瞬时错误视为"还没好"（返回空串）。"""
    try:
        return session.call_tool_text("search_mcp_tool", {"keywords": keywords})
    except Exception:  # noqa: BLE001 - 轮询谓词，失败即未就绪
        return ""


def _write_state(path: Path, tools: list[str]) -> None:
    path.write_text(
        json.dumps({"tools": tools}, ensure_ascii=False), encoding="utf-8"
    )


def _mutable_demo(state_path: Path, port: int):
    return spawn_demo("mutable.py", port, ["--state", str(state_path)])


# ---------- 进程判活（孤儿检测，无外部命令依赖） ----------


def _pid_alive(pid: int) -> bool:
    """Windows 用 OpenProcess+GetExitCodeProcess 判活；其它平台 os.kill(0)。"""
    if sys.platform != "win32":  # pragma: no cover - 本用例打 windows marker
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


# ---------- 8.1 stdout 零污染 + 全链路 ----------


def test_stdout_carries_only_jsonrpc(launch: Callable[..., "Variant"]) -> None:
    """hr-only 全程逐行可解析：stdout 只有 JSON-RPC，日志只在 stderr。"""
    variant = launch(
        {"hr": HR_STDIO},
        settings={"handshake_timeout": 10, "refresh_interval": 600},
    )
    session = variant.session
    assert session is not None
    assert session.handshake_result is not None
    assert session.unparsed_lines == []

    tools = session.list_tools()
    assert {tool["name"] for tool in tools} == PROXY_TOOLS

    search_text = session.call_tool_text("search_mcp_tool", {"keywords": ["leave"]})
    assert "hr__get_leave_balance" in search_text
    status_text = session.call_tool_text("check_mcp_status")
    assert f"hr: live, {HR_TOOLS} tool" in status_text

    assert session.unparsed_lines == []
    # 摘要（人读日志）走 stderr，不混进 stdout
    summary = variant.proxy.wait_stderr("upstreams connected", deadline=10)
    assert "1/1 upstreams connected" in summary
    # 同一行文本不得作为 JSON-RPC 通知出现在 stdout（日志只在 stderr）
    assert all(
        "upstreams connected" not in json.dumps(message, ensure_ascii=False)
        for message in session.notifications()
    )


def test_initialize_list_tools_call_chain(std: "StdStack") -> None:
    """全链路：initialize → tools/list 见三工具 → search/status 均成功。"""
    session = std.session
    handshake = session.handshake_result
    assert isinstance(handshake, dict)
    assert handshake.get("protocolVersion")
    assert handshake.get("serverInfo")

    tools = session.list_tools()
    assert {tool["name"] for tool in tools} == PROXY_TOOLS

    search_text = session.call_tool_text("search_mcp_tool", {"keywords": ["employee"]})
    assert "hr__get_employee" in search_text

    status_text = session.call_tool_text("check_mcp_status")
    assert f"hr: live, {HR_TOOLS} tool" in status_text
    assert f"itops: live, {ITOPS_TOOLS} tool" in status_text
    assert "dead_http: UNREACHABLE" in status_text


# ---------- 8.2 长会话 ----------


def test_long_session_stays_stable(std: "StdStack") -> None:
    """50+ 混合调用全成功；首尾同条件搜索文本一致。"""
    session = std.session
    keywords = ["leave"]
    first = session.call_tool_text("search_mcp_tool", {"keywords": keywords})

    for _ in range(20):
        assert "hr__get_leave_balance" in session.call_tool_text(
            "search_mcp_tool", {"keywords": keywords}
        )
        status_text = session.call_tool_text("check_mcp_status")
        assert f"hr: live, {HR_TOOLS} tool" in status_text
        result = session.call_tool(
            "invoke_this_tool",
            {"target": "hr__get_employee", "params": {"employee_id": "E-1"}},
        )
        assert not result.get("isError")
        assert "Ada Lovelace" in _text_of_blocks(result)

    last = session.call_tool_text("search_mcp_tool", {"keywords": keywords})
    assert first == last
    assert session.unparsed_lines == []


# ---------- 8.3 / 3.2 孤儿回收 ----------


@pytest.mark.windows
def test_downstream_disconnect_reaps_stdio_children(
    launch: Callable[..., "Variant"], tmp_path: Path
) -> None:
    """下游断开 → 代理退出且代理 spawn 的 stdio 上游（gizmos）无残留。

    判活不用进程表扫描（本机 PowerShell 无输出、wmic 缺失）：假上游经
    config env 注入 `B03_PID_FILE`，启动时自己写下 PID，测试用
    ctypes 判该 PID 是否仍活。
    """
    if sys.platform != "win32":  # pragma: no cover - marker 之外的第二道保险
        pytest.skip("pid-liveness check is Windows-only")

    pid_file = tmp_path / "gizmos.pid"
    itops_port = free_port()
    launch.track_demo(spawn_demo("itops.py", itops_port))
    variant = launch(
        {
            "gizmos": {
                "command": sys.executable,
                "args": [str(DEMO_DIR / "gizmos.py")],
                "env": {"B03_PID_FILE": str(pid_file)},
            },
            "itops": {"url": f"http://127.0.0.1:{itops_port}/mcp"},
        },
        settings={"handshake_timeout": 10, "refresh_interval": 600},
    )
    session = variant.session
    assert session is not None
    assert f"itops: live, {ITOPS_TOOLS} tool" in session.call_tool_text(
        "check_mcp_status"
    )

    pid = int(
        wait_until(
            lambda: pid_file.read_text(encoding="utf-8").strip()
            if pid_file.exists()
            else None,
            deadline=10,
        )
    )
    assert _pid_alive(pid), "stdio child should be alive while the proxy runs"

    variant.proxy.close_stdin()  # 模拟下游断开
    assert variant.proxy.wait_exit(15) is not None, (
        "proxy did not exit after downstream disconnect"
    )
    wait_until(lambda: not _pid_alive(pid), deadline=10)


# ---------- 8.4 退出耗时（D-27） ----------


def test_exit_is_not_held_by_unreachable_http_upstream(
    launch: Callable[..., "Variant"],
) -> None:
    """config 含不可达 Streamable HTTP 上游：下游断开后 10s 内退出。"""
    dead_port = free_port()  # 无人监听
    variant = launch(
        {
            "hr": HR_STDIO,
            "dead_http": {"url": f"http://127.0.0.1:{dead_port}/mcp"},
        },
        settings={"handshake_timeout": 5, "refresh_interval": 600},
    )
    session = variant.session
    assert session is not None  # initialize 已应答 = 启动完成
    assert "dead_http: UNREACHABLE" in session.call_tool_text("check_mcp_status")

    started = time.monotonic()
    variant.proxy.close_stdin()
    code = variant.proxy.wait_exit(10)
    elapsed = time.monotonic() - started

    assert code is not None, f"proxy still alive after 10s (D-27), stderr={variant.proxy.stderr_lines[-5:]}"
    assert elapsed <= 10.0, f"exit took {elapsed:.1f}s (D-27 budget 10s)"


# ---------- 8.5 刷新自愈 + 搜索与索引重写并发 ----------


def test_refresh_heals_and_search_races_rewrite_safely(
    launch: Callable[..., "Variant"], tmp_path: Path
) -> None:
    """一次 launch 覆盖两段刷新语义（interval=1）：

    1. 启动时 itops 无人监听 → unreachable；上游起来后下一轮刷新自愈为 live 可搜。
    2. 同一进程上后台线程连续搜索 30 次，期间一轮刷新重写索引：响应全可解析、无半行。
    """
    state_path = tmp_path / "mutable_state.json"
    _write_state(state_path, MUTABLE_INITIAL)
    itops_port, mutable_port = pick_ports(2)  # itops 起代理时无人监听
    launch.track_demo(_mutable_demo(state_path, mutable_port))

    variant = launch(
        {
            "itops": {"url": f"http://127.0.0.1:{itops_port}/mcp"},
            "hr": HR_STDIO,
            "mutable": {"url": f"http://127.0.0.1:{mutable_port}/mcp"},
        },
        settings={"refresh_interval": 1, "handshake_timeout": 10},
    )
    session = variant.session
    assert session is not None
    assert "itops: UNREACHABLE" in session.call_tool_text("check_mcp_status")

    launch.track_demo(spawn_demo("itops.py", itops_port))

    # --- 第一段：启动挂 → 刷新后恢复可搜、转 live ---

    def _sees_tool() -> str:
        text = _safe_search(session, ["firehose"])
        return text if "itops__firehose" in text else ""

    assert "itops__firehose" in wait_until(_sees_tool, deadline=15)

    def _is_live() -> str:
        text = session.call_tool_text("check_mcp_status")
        return text if f"itops: live, {ITOPS_TOOLS} tool" in text else ""

    assert f"itops: live, {ITOPS_TOOLS} tool" in wait_until(_is_live, deadline=15)

    # --- 第二段：并发搜索与一轮索引重写 ---

    # mutable 导入时 6 工具、状态文件生效后降到 2：等首轮同步稳定
    wait_until(
        lambda: "mutable (2 tools)" in _description_of(session.list_tools()),
        deadline=15,
    )

    errors: list[str] = []
    responses: list[str] = []

    def _hammer() -> None:
        for _ in range(30):
            try:
                responses.append(
                    session.call_tool_text("search_mcp_tool", {"keywords": ["demo"]})
                )
            except Exception as exc:  # noqa: BLE001 - 收集后统一断言
                errors.append(repr(exc))

    worker = threading.Thread(target=_hammer, daemon=True)
    worker.start()
    try:
        _write_state(state_path, MUTABLE_CHANGED)  # 触发一轮刷新重写索引

        def _sees_new_tool() -> str:
            text = _safe_search(session, ["demo"])
            return text if "mutable__m2" in text else ""

        assert "mutable__m2" in wait_until(_sees_new_tool, deadline=15)
    finally:
        worker.join(timeout=60)

    assert not worker.is_alive()
    assert errors == []
    assert len(responses) == 30
    # 每次响应都是完整的搜索正文（索引要么旧版要么新版，无半行/空响应）
    assert all("mutable__m" in text for text in responses)
    assert session.unparsed_lines == []


# ---------- 8.6 新旧双代通知（D-25） ----------


async def test_new_protocol_listen_receives_tools_list_changed(
    tmp_path: Path,
) -> None:
    """默认 mode=auto：经 server/discover 协商新代，listen 收到 ToolsListChanged。"""
    state_path = tmp_path / "mutable_state.json"
    _write_state(state_path, MUTABLE_INITIAL)
    mutable_port = free_port()

    with _mutable_demo(state_path, mutable_port):
        config_path = write_config(
            tmp_path,
            {"mutable": {"url": f"http://127.0.0.1:{mutable_port}/mcp"}},
            settings={"refresh_interval": 1, "handshake_timeout": 10},
        )
        async with sdk_client(config_path) as client:
            assert client.protocol_version >= "2026-07-28"  # 新代协商成功

            async def _initial_roster() -> str:
                description = _sdk_description(await client.list_tools())
                return description if "mutable (2 tools)" in description else ""

            await wait_until_async(_initial_roster, deadline=15)

            async with client.listen(tools_list_changed=True) as sub:
                _write_state(state_path, MUTABLE_CHANGED)
                event = await asyncio.wait_for(anext(sub), timeout=15)
                assert isinstance(event, ToolsListChanged), (
                    f"unexpected subscription event: {type(event).__name__}"
                )

            refreshed = _sdk_description(await client.list_tools())
            assert "mutable (3 tools)" in refreshed


async def test_legacy_protocol_notification_reaches_message_handler(
    tmp_path: Path,
) -> None:
    """mode=legacy：initialize 握手，message_handler 收到 tools/list_changed。"""
    state_path = tmp_path / "mutable_state.json"
    _write_state(state_path, MUTABLE_INITIAL)
    mutable_port = free_port()

    methods: list[str] = []
    seen = asyncio.Event()

    async def handler(message: Any) -> None:
        method = getattr(message, "method", None) or (
            message.get("method") if isinstance(message, dict) else None
        )
        if method == "notifications/tools/list_changed":
            methods.append(str(method))
            seen.set()

    with _mutable_demo(state_path, mutable_port):
        config_path = write_config(
            tmp_path,
            {"mutable": {"url": f"http://127.0.0.1:{mutable_port}/mcp"}},
            settings={"refresh_interval": 1, "handshake_timeout": 10},
        )
        async with sdk_client(
            config_path, mode="legacy", message_handler=handler
        ) as client:
            assert client.protocol_version < "2026-07-28"  # 强制旧代握手

            async def _initial_roster() -> str:
                description = _sdk_description(await client.list_tools())
                return description if "mutable (2 tools)" in description else ""

            await wait_until_async(_initial_roster, deadline=15)

            _write_state(state_path, MUTABLE_CHANGED)
            await asyncio.wait_for(seen.wait(), timeout=15)
            assert "notifications/tools/list_changed" in methods

            refreshed = _sdk_description(await client.list_tools())
            assert "mutable (3 tools)" in refreshed


# ---------- 8.7 DeepAgents 拉起形态 + `_status.json` ----------


async def test_deepagents_style_stdio_launch(tmp_path: Path) -> None:
    """command/args stdio 形态（等价 langchain-mcp-adapters）：list + call 正常。"""
    config_path = write_config(tmp_path, {"hr": HR_STDIO})

    async with sdk_client(config_path) as client:
        listing = await client.list_tools()
        assert {tool.name for tool in listing.tools} == PROXY_TOOLS

        result = await client.call_tool("check_mcp_status", {})
        assert result.is_error is False
        assert f"hr: live, {HR_TOOLS} tool" in _sdk_text(result)


def test_status_json_snapshot_matches_registry(std: "StdStack") -> None:
    """`_status.json` 字段齐全、与 check_mcp_status 一致、无 tmp 残留。"""
    status_path = std.index_dir / "_status.json"
    assert status_path.is_file()

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    servers = payload["servers"]
    assert {"hr", "itops", "audit", "dead_http"} <= set(servers)

    status_text = std.session.call_tool_text("check_mcp_status")
    for name, entry in servers.items():
        assert set(entry) == {"state", "tools", "reason", "updated_at"}
        assert entry["state"] in {"live", "unreachable"}
        assert isinstance(entry["tools"], int)
        assert isinstance(entry["updated_at"], str) and entry["updated_at"]
        if entry["state"] == "live":
            assert entry["reason"] is None
            assert f"- {name}: live, {entry['tools']} tool(s)" in status_text
        else:
            assert entry["reason"]
            assert f"- {name}: UNREACHABLE - {entry['reason']}" in status_text

    assert list(std.index_dir.glob("*.tmp")) == []
    assert list(std.index_dir.glob(".status-*")) == []
