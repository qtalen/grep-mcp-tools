"""A 区：启动与索引（B03 tasks 4.1-4.6）。

共享 session fixture `std`（hr + itops + audit + dead_http）承载只读用例；
任何改变状态或需要独享进程的用例走 `launch()` 或手动 `ProxyProcess`。
等待一律 harness 的 `wait_until` / `wait_stderr` / `wait_exit`，不用固定 sleep。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.e2e.harness import (
    DEMO_DIR,
    ProxyProcess,
    RawSession,
    pick_ports,
    read_index_lines,
    spawn_demo,
    wait_until,
    write_config,
)

if TYPE_CHECKING:
    from tests.e2e.conftest import StdStack, Variant

HR_ENTRY = {"command": sys.executable, "args": [str(DEMO_DIR / "hr.py")]}

QUIET_REFRESH = {"refresh_interval": 600}


def _hr_jsonl(root: Path, name: str = "hr") -> Path:
    return Path(root) / f"{name}.jsonl"


def _tool_text(session: RawSession, keywords: list[str]) -> str:
    return session.call_tool_text("search_mcp_tool", {"keywords": keywords})


def _status_text(session: RawSession) -> str:
    return session.call_tool_text("check_mcp_status")


def _tool_descriptions(session: RawSession) -> dict[str, str]:
    return {tool["name"]: tool.get("description") or "" for tool in session.list_tools()}


def _invoke_error_text(
    session: RawSession, target: str, params: dict | None = None
) -> str:
    """invoke 的代理级错误文本（接受 JSON-RPC error 与 isError 结果两种形态）。"""
    try:
        result = session.call_tool(
            "invoke_this_tool",
            {"target": target, "params": params if params is not None else {}},
            timeout=30,
        )
    except AssertionError as exc:  # RawSession 把 JSON-RPC error 抛成 AssertionError
        return str(exc)
    text = "".join(
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict)
    )
    assert result.get("isError") is True, f"invoke {target!r} unexpectedly succeeded: {text}"
    return text


# ---------- 共享 std：多传输握手、命名空间、summary、status ----------


def test_std_multi_transport_index_line_counts(std: "StdStack") -> None:
    """hr(stdio)=8 / itops(HTTP)=12 / audit(SSE)=3 行索引。"""
    wait_until(lambda: _hr_jsonl(std.index_dir).is_file(), deadline=10)
    hr_lines = read_index_lines(std.index_dir, "hr")
    itops_lines = read_index_lines(std.index_dir, "itops")
    audit_lines = read_index_lines(std.index_dir, "audit")

    assert (len(hr_lines), len(itops_lines), len(audit_lines)) == (8, 12, 3)
    assert all(json.loads(line)["server"] == "itops" for line in itops_lines)


def test_std_same_named_tool_kept_in_own_index(std: "StdStack") -> None:
    """hr 与 itops 的同名工具 search 各在自己的索引文件，互不覆盖。"""
    hr_lines = read_index_lines(std.index_dir, "hr")
    itops_lines = read_index_lines(std.index_dir, "itops")

    hr_search = [line for line in hr_lines if '"name":"search"' in line]
    itops_search = [line for line in itops_lines if '"name":"search"' in line]

    assert len(hr_search) == 1 and len(itops_search) == 1
    # 索引行是工具定义（description 来自各服务自己的文案），不是调用结果
    assert "Search employee records" in hr_search[0]
    assert "Search the operations knowledge base" in itops_search[0]


def test_std_startup_summary_connected(std: "StdStack") -> None:
    """stderr 启动摘要：3/4 upstreams connected（dead_http 计失败）。"""
    line = std.proxy.wait_stderr("upstreams connected", deadline=10)

    assert "3/4 upstreams connected" in line
    assert "dead_http FAILED:" in line


def test_std_status_dead_upstream_strong_warning(std: "StdStack") -> None:
    """有死者时 check_mcp_status 强提示 + dead_http 原因在场。"""
    text = _status_text(std.session)

    assert "dead_http: UNREACHABLE - " in text
    dead_line = next(
        line for line in text.splitlines() if line.startswith("- dead_http: UNREACHABLE")
    )
    assert dead_line.split("UNREACHABLE - ", 1)[1].strip()
    assert "WARNING:" in text
    assert "not searchable or callable" in text


# ---------- 单传输变体（HTTP / SSE 单独） ----------


@pytest.mark.parametrize(
    ("script", "suffix", "transport", "expected_lines", "probe_tool"),
    [
        ("itops.py", "/mcp", None, 12, "region_deploy_status"),
        ("audit.py", "/sse", "sse", 3, "audit_trail_retention"),
    ],
    ids=["http-only", "sse-only"],
)
def test_single_transport_variant(
    launch,
    script: str,
    suffix: str,
    transport: str | None,
    expected_lines: int,
    probe_tool: str,
) -> None:
    """itops 单独、audit 单独都能启动并落索引。"""
    port = pick_ports(1)[0]
    launch.track_demo(spawn_demo(script, port))
    entry: dict = {"url": f"http://127.0.0.1:{port}{suffix}"}
    if transport is not None:
        entry["type"] = transport

    variant: "Variant" = launch({script.removesuffix(".py"): entry}, settings=QUIET_REFRESH)

    server = script.removesuffix(".py")
    assert len(read_index_lines(variant.index_dir, server)) == expected_lines
    assert f"{server}__{probe_tool}" in _tool_text(variant.session, [probe_tool])


# ---------- 不可达族：fail-soft 且不拖累其余上游 ----------


def test_blackhole_handshake_timeout_does_not_block_startup(launch) -> None:
    """blackhole（死等端口）+ broken（stdio 秒退）+ hr：启动不被拖死，hr 正常握手。

    blackhole 与 broken 同一进程内并发握手，一次变体覆盖两种"卡住/秒退"的
    fail-soft 形态：blackhole 触发 handshake_timeout，broken 立即失败。
    handshake_timeout=5：2s 会让 hr 自身的子进程 import+握手也超时（Windows
    冷启动抖动），5s 下 blackhole 仍会超时，健康上游有足够预算。
    """
    port = pick_ports(1)[0]
    launch.track_demo(spawn_demo("blackhole.py", port))
    started = time.monotonic()

    variant: "Variant" = launch(
        {
            "blackhole": {"url": f"http://127.0.0.1:{port}/mcp"},
            "broken": {"command": sys.executable, "args": [str(DEMO_DIR / "broken.py")]},
            "hr": HR_ENTRY,
        },
        settings={**QUIET_REFRESH, "handshake_timeout": 5},
    )
    elapsed = time.monotonic() - started

    assert elapsed < 20, f"startup took {elapsed:.1f}s with a blackhole upstream"
    text = _status_text(variant.session)
    assert "hr: live, 8 tool(s)" in text
    assert "broken: UNREACHABLE" in text
    assert not _hr_jsonl(variant.index_dir, "broken").exists()
    assert len(read_index_lines(variant.index_dir, "hr")) == 8
    line = variant.proxy.wait_stderr("blackhole handshake failed", deadline=10)
    assert "timed out after 5s" in line
    variant.proxy.wait_stderr("broken handshake failed", deadline=10)


def test_unreachable_family_fails_soft(launch, std: "StdStack") -> None:
    """dead_sse（无人监听 + type: sse）与 wrong_path（std.itops_port 的 /wrong）+ hr。

    一次变体覆盖"连不上"与"连上但 404"两种不可达形态：均 UNREACHABLE 且
    原因非空，stderr 摘要点名失败服务，hr 不受拖累。
    """
    port = pick_ports(1)[0]
    variant: "Variant" = launch(
        {
            "dead_sse": {"url": f"http://127.0.0.1:{port}/sse", "type": "sse"},
            "wrong_path": {"url": f"http://127.0.0.1:{std.itops_port}/wrong"},
            "hr": HR_ENTRY,
        },
        settings={**QUIET_REFRESH, "handshake_timeout": 5},
    )
    text = _status_text(variant.session)

    for name in ("dead_sse", "wrong_path"):
        assert f"{name}: UNREACHABLE - " in text, name
        line = next(
            item for item in text.splitlines() if item.startswith(f"- {name}: UNREACHABLE")
        )
        assert line.split("UNREACHABLE - ", 1)[1].strip(), line
    assert "hr: live, 8 tool(s)" in text
    assert not _hr_jsonl(variant.index_dir, "dead_sse").exists()
    assert not _hr_jsonl(variant.index_dir, "wrong_path").exists()
    summary = variant.proxy.wait_stderr("upstreams connected", deadline=10)
    assert "dead_sse FAILED:" in summary
    assert "wrong_path FAILED:" in summary
    variant.proxy.wait_stderr("dead_sse handshake failed", deadline=10)
    variant.proxy.wait_stderr("wrong_path handshake failed", deadline=10)


# ---------- check_mcp_status：全健康 / 全挂 ----------


def test_all_healthy_status_has_no_warning(launch) -> None:
    """hr + itops + audit 全健康（自 spawn 新 demo，新端口）：无警告。"""
    itops_port, audit_port = pick_ports(2)
    launch.track_demo(spawn_demo("itops.py", itops_port))
    launch.track_demo(spawn_demo("audit.py", audit_port))
    variant: "Variant" = launch(
        {
            "hr": HR_ENTRY,
            "itops": {"url": f"http://127.0.0.1:{itops_port}/mcp"},
            "audit": {"url": f"http://127.0.0.1:{audit_port}/sse", "type": "sse"},
        },
        settings=QUIET_REFRESH,
    )
    text = _status_text(variant.session)

    assert "WARNING" not in text
    assert "UNREACHABLE" not in text
    for expected in ("hr: live, 8 tool(s)", "itops: live, 12 tool(s)", "audit: live, 3 tool(s)"):
        assert expected in text
    assert "3/3 upstreams connected" in variant.proxy.wait_stderr("upstreams connected", deadline=10)


def test_all_upstreams_down_reports_live_zero(launch) -> None:
    """全挂变体（全部指向无人监听端口）：启动合法、live=0、搜索给出全挂警告。"""
    port_a, port_b = pick_ports(2)
    variant: "Variant" = launch(
        {
            "dead_a": {"url": f"http://127.0.0.1:{port_a}/mcp"},
            "dead_b": {"url": f"http://127.0.0.1:{port_b}/sse", "type": "sse"},
        },
        settings={**QUIET_REFRESH, "handshake_timeout": 5},
    )
    text = _status_text(variant.session)
    search_text = _tool_text(variant.session, ["leave"])

    assert "0/2 upstreams connected" in variant.proxy.wait_stderr("upstreams connected", deadline=10)
    assert "dead_a: UNREACHABLE" in text and "dead_b: UNREACHABLE" in text
    assert "NO upstream services are currently available" in search_text
    assert "check_mcp_status" in search_text


# ---------- 边界：0 工具上游、空配置 ----------


def test_empty_upstream_is_live_and_counts_as_connected(launch) -> None:
    """empty（0 工具）+ hr：status 记 `empty: live, 0 tool(s)`、无 empty.jsonl，且计入 connected。"""
    variant: "Variant" = launch(
        {"empty": {"command": sys.executable, "args": [str(DEMO_DIR / "empty.py")]}, "hr": HR_ENTRY},
        settings=QUIET_REFRESH,
    )
    wait_until(lambda: _hr_jsonl(variant.index_dir, "hr").is_file(), deadline=15)

    assert "empty: live, 0 tool(s)" in _status_text(variant.session)
    assert not _hr_jsonl(variant.index_dir, "empty").exists()
    assert len(read_index_lines(variant.index_dir, "hr")) == 8
    line = variant.proxy.wait_stderr("upstreams connected", deadline=15)
    assert "2/2 upstreams connected" in line
    assert "FAILED" not in line


def test_empty_config_starts_and_self_describes(launch) -> None:
    """空配置（mcpServers 为 {}）合法启动；status 自答；description 提示无上游。"""
    variant: "Variant" = launch({}, settings=QUIET_REFRESH)

    assert _status_text(variant.session).strip() == "no upstream services configured"
    description = _tool_descriptions(variant.session)["search_mcp_tool"]
    assert "no upstream" in description.casefold()
    assert "0/0 upstreams connected" in variant.proxy.wait_stderr("upstreams connected", deadline=10)


# ---------- fail-fast 族 ----------


@pytest.mark.parametrize(
    ("case", "payload", "pattern"),
    [
        ("missing-config", None, "not found"),
        ("invalid-json", '{"mcpServers": ', "invalid JSON"),
        ("no-mcpservers-key", '{"settings": {}}', "missing required key"),
        (
            "unknown-type",
            json.dumps(
                {
                    "mcpServers": {
                        "x": {"url": "http://127.0.0.1:9/mcp", "type": "websocket"}
                    }
                }
            ),
            "'type' must be",
        ),
        (
            "duplicate-name",
            json.dumps(
                {"mcpServers": {"hr": {"command": "python"}, "HR": {"command": "python"}}}
            ),
            "duplicate server name",
        ),
        (
            "double-underscore-name",
            json.dumps({"mcpServers": {"bad__name": {"command": "python"}}}),
            "must not contain",
        ),
    ],
)
def test_config_fail_fast_exit_code_two(
    tmp_path: Path, case: str, payload: str | None, pattern: str
) -> None:
    """config 问题一律退出码 2，且 stderr 指认原因（不静默）。"""
    config_path = tmp_path / f"{case}.json"
    if payload is not None:
        config_path.write_text(payload, encoding="utf-8")

    proxy = ProxyProcess(config_path).start()
    try:
        proxy.wait_stderr(pattern, deadline=20)
        assert proxy.wait_exit(timeout=20) == 2
    finally:
        proxy.stop()
    assert "config error" in proxy.stderr_text()


# ---------- 重启、相对路径、env 注入 ----------


def test_restart_replaces_index_instead_of_appending(launch) -> None:
    """重启后同 config 仍 8 行（替换不追加），索引目录无 tmp 残留。"""
    variant: "Variant" = launch({"hr": HR_ENTRY}, settings=QUIET_REFRESH)
    wait_until(lambda: _hr_jsonl(variant.index_dir).is_file(), deadline=15)
    assert len(read_index_lines(variant.index_dir, "hr")) == 8

    variant.proxy.stop()
    second = ProxyProcess(variant.config_path).start()
    session = RawSession(second).open()
    try:
        lines = read_index_lines(variant.index_dir, "hr")
        assert len(lines) == 8
        # 原子写进行中的 .tmp 是正常瞬态；等本轮落盘结束后不得残留
        wait_until(
            lambda: not any(
                path.suffix == ".tmp" for path in variant.index_dir.iterdir()
            ),
            deadline=10,
        )
    finally:
        session.close()
        second.stop()


def test_relative_index_dir_resolves_against_config_dir(tmp_path: Path) -> None:
    """settings.index_dir='idx'（相对）+ 另一 cwd 启动 → 索引落在 config 同目录。"""
    config_root = tmp_path / "cfg"
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    config_path = write_config(config_root, {"hr": HR_ENTRY}, settings={"index_dir": "idx"})

    proxy = ProxyProcess(config_path, cwd=other_cwd).start()
    session = RawSession(proxy).open()
    try:
        index_file = config_root / "idx" / "hr.jsonl"
        wait_until(index_file.is_file, deadline=20)
        assert len(index_file.read_text(encoding="utf-8").splitlines()) == 8
        assert not (other_cwd / "idx").exists()
    finally:
        session.close()
        proxy.stop()


def test_env_injection_reaches_upstream(launch) -> None:
    """config 条目 env 注入 gizmos：echo_env 读到 B03_MARKER=hello。"""
    variant: "Variant" = launch(
        {
            "gizmos": {
                "command": sys.executable,
                "args": [str(DEMO_DIR / "gizmos.py")],
                "env": {"B03_MARKER": "hello"},
            }
        },
        settings=QUIET_REFRESH,
    )
    text = variant.session.call_tool_text(
        "invoke_this_tool",
        {"target": "gizmos__echo_env", "params": {"key": "B03_MARKER"}},
    )

    assert text == "hello"


def test_gizmos_awkward_description_stays_single_line(launch) -> None:
    """gizmos 变体：换行/引号/中文描述在 jsonl 中仍是合法单行 JSON。"""
    variant: "Variant" = launch(
        {"gizmos": {"command": sys.executable, "args": [str(DEMO_DIR / "gizmos.py")]}},
        settings=QUIET_REFRESH,
    )
    wait_until(lambda: _hr_jsonl(variant.index_dir, "gizmos").is_file(), deadline=15)

    lines = read_index_lines(variant.index_dir, "gizmos")
    records = [json.loads(line) for line in lines]
    record = next(item for item in records if item["name"] == "multiline_quote")

    assert "中文描述" in record["description"]
    assert "line two" in record["description"]
    assert len(lines) == 5


# ---------- 刷新相关：中途死亡、roster 增删 ----------


def test_middle_death_two_phase(launch) -> None:
    """interval=5：翻转前仍可搜；翻转后 UNREACHABLE、索引隔离、invoke 代理级错误。"""
    itops_port = pick_ports(1)[0]
    itops = launch.track_demo(spawn_demo("itops.py", itops_port))
    variant: "Variant" = launch(
        {"itops": {"url": f"http://127.0.0.1:{itops_port}/mcp"}, "hr": HR_ENTRY},
        settings={"refresh_interval": 5, "handshake_timeout": 5},
    )
    assert "itops__region_deploy_status" in _tool_text(
        variant.session, ["region_deploy_status"]
    )

    itops.kill()

    # 阶段一：本轮快照未翻转，索引文件仍在 → 仍可搜；invoke 已打不通（代理级错误）。
    assert "itops__region_deploy_status" in _tool_text(
        variant.session, ["region_deploy_status"]
    )
    error_text = _invoke_error_text(variant.session, "itops__region_deploy_status")
    assert "Proxy error" in error_text

    # 阶段二：刷新轮翻 unreachable → 搜不到、invoke 原因自包含。
    wait_until(
        lambda: "itops: UNREACHABLE" in _status_text(variant.session), deadline=10
    )
    assert "itops__region_deploy_status" not in _tool_text(
        variant.session, ["region_deploy_status"]
    )
    unreachable_text = _invoke_error_text(
        variant.session, "itops__region_deploy_status"
    )
    assert "configured but unreachable" in unreachable_text


def test_mutable_roster_add_and_remove(launch, tmp_path: Path) -> None:
    """interval=1 + mutable 状态文件：删工具后索引/搜索/invoke 同步收缩，再加回。"""
    state_path = tmp_path / "mutable_state.json"
    state_path.write_text(
        json.dumps({"tools": ["m0", "m1", "m2", "m3", "m4", "m5"]}), encoding="utf-8"
    )
    port = pick_ports(1)[0]
    launch.track_demo(spawn_demo("mutable.py", port, args=["--state", str(state_path)]))
    variant: "Variant" = launch(
        {"mutable": {"url": f"http://127.0.0.1:{port}/mcp"}},
        settings={"refresh_interval": 1, "handshake_timeout": 5},
    )

    def index_count() -> int:
        path = _hr_jsonl(variant.index_dir, "mutable")
        return len(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else 0

    wait_until(lambda: index_count() == 6, deadline=15)
    assert "mutable__m1" in _tool_text(variant.session, ["m1"])

    state_path.write_text(json.dumps({"tools": ["m0", "m2"]}), encoding="utf-8")
    wait_until(lambda: index_count() == 2, deadline=10)
    wait_until(
        lambda: "mutable__m1" not in _tool_text(variant.session, ["m1"]), deadline=10
    )
    removed_text = _invoke_error_text(variant.session, "mutable__m1")
    assert "does not exist" in removed_text

    state_path.write_text(json.dumps({"tools": ["m0", "m2", "m3"]}), encoding="utf-8")
    wait_until(lambda: index_count() == 3, deadline=10)
    wait_until(lambda: "mutable__m3" in _tool_text(variant.session, ["m3"]), deadline=10)
