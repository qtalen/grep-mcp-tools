"""C 区：invoke 透传（14 例）。

契约来源：`src/grep_mcp_tools/server.py::ProxyServer.invoke`（代理级错误文案
以 `Proxy error:` 开头）与 `docs/design/batches/B03-e2e-matrix.md`『C invoke
透传（14 例）』。上游业务错误原样透传（不是 `Proxy error:`）；上游模块对
schema 的校验失败属于上游错误，代理不拦截。

共享 `std` 上的用例只读；凡改动状态或需要独享上游的用例一律走 `launch`
变体工厂（每次独享目录、配置与代理进程）。
"""

from __future__ import annotations

import sys
import threading
import time
from typing import TYPE_CHECKING

from tests.e2e.harness import (
    DEMO_DIR,
    RawSession,
    pick_ports,
    read_index_lines,
    spawn_demo,
    wait_until,
)

if TYPE_CHECKING:
    from tests.e2e.conftest import StdStack

COPY_HINT = "Copy an exact target from search_mcp_tool results."


def _invoke(
    session: RawSession,
    target: str,
    params: dict | None = None,
    timeout: float = 30.0,
) -> tuple[str, bool]:
    """调 `invoke_this_tool`，返回 `(text, is_error)`。

    上游业务错误既可能以 `isError` 结果返回，也可能作为 JSON-RPC error 冒泡
    （代理 `raise` 原样透传）——这里统一折算成文本，调用方只需断言文案。
    """
    arguments: dict = {"target": target}
    if params is not None:
        arguments["params"] = params
    try:
        result = session.call_tool("invoke_this_tool", arguments, timeout=timeout)
    except AssertionError as exc:  # RawSession 把 JSON-RPC error 抛成 AssertionError
        return str(exc), True
    text = "".join(
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict)
    )
    return text, bool(result.get("isError"))


# ---------- 共享 std 上的透传 ----------


def test_invoke_normal_call(std: "StdStack") -> None:
    """正常调用：`hr__get_employee` 原样返回上游结果，且不是错误。"""
    text, is_error = _invoke(std.session, "hr__get_employee", {"employee_id": "E-1"})

    assert is_error is False
    assert "Ada Lovelace" in text
    assert "Proxy error" not in text


def test_invoke_same_name_tools_are_split_by_server(std: "StdStack") -> None:
    """同名工具分流：`hr__search` 与 `itops__search` 各自返回自身文案。"""
    hr_text, hr_error = _invoke(std.session, "hr__search", {"query": "ada"})
    itops_text, itops_error = _invoke(std.session, "itops__search", {"query": "ada"})

    assert hr_error is False and itops_error is False
    assert "hr search results for 'ada'" in hr_text
    assert "itops search results for 'ada'" in itops_text
    assert hr_text != itops_text


def test_invoke_upstream_error_passthrough(std: "StdStack") -> None:
    """上游错误透传：`itops__boom` 报错且文案来自上游，不是代理级错误。"""
    text, is_error = _invoke(std.session, "itops__boom")

    assert is_error is True
    assert "boom" in text
    assert not text.startswith("Proxy error:"), text


def test_invoke_bad_target_shapes(std: "StdStack") -> None:
    """target 三类坏形态（无 `__` / 空串 / 首段非已知服务）→ 代理级错误。"""
    for target in ("hrget_employee", "", "nosuch__tool"):
        text, is_error = _invoke(std.session, target)

        assert is_error is True, target
        assert "Proxy error" in text, target
        assert COPY_HINT in text, target


def test_invoke_configured_but_unreachable(std: "StdStack", launch) -> None:
    """config 内但不可达：代理级错误、原因自包含，与"非已知服务"文案可区分。"""
    dead_port = pick_ports(1)[0]
    variant = launch(
        {
            "hr": {"command": sys.executable, "args": [str(DEMO_DIR / "hr.py")]},
            "dead_http": {"url": f"http://127.0.0.1:{dead_port}/mcp"},
        },
        settings={"handshake_timeout": 5, "refresh_interval": 600},
    )

    text, is_error = _invoke(variant.session, "dead_http__anything")

    assert is_error is True
    assert "Proxy error" in text
    assert "dead_http" in text
    assert "configured but unreachable" in text
    assert "is not configured" not in text
    # 原因自包含：括号内带具体传输失败原因，而不是空白/占位
    assert "not connected" not in text
    reason = text.split("configured but unreachable (", 1)[1].split(")", 1)[0]
    assert len(reason) > 10, text


def test_invoke_indexed_tool_on_dead_upstream(launch) -> None:
    """索引有但上游挂：kill 后立即 invoke → 代理级错误，不报"工具不存在"。"""
    port = pick_ports(1)[0]
    demo = launch.track_demo(spawn_demo("itops.py", port))
    variant = launch(
        {"itops": {"url": f"http://127.0.0.1:{port}/mcp"}},
        settings={"handshake_timeout": 10, "refresh_interval": 600},
    )
    wait_until(
        lambda: any("firehose" in line for line in read_index_lines(variant.index_dir, "itops"))
    )

    demo.kill()

    text, is_error = _invoke(variant.session, "itops__firehose", timeout=60.0)

    assert is_error is True
    assert "Proxy error" in text
    assert "does not exist" not in text, text
    assert "firehose" in text
    assert "check_mcp_status" in text  # 自包含的状态检查提示


def test_invoke_100kb_passthrough_intact(std: "StdStack") -> None:
    """100KB 原样：`itops__firehose` 文本长度恰为 102400，不截断。"""
    text, is_error = _invoke(std.session, "itops__firehose", timeout=60.0)

    assert is_error is False
    assert len(text) == 102400


def test_invoke_params_are_validated_by_upstream(std: "StdStack") -> None:
    """空参数/多余参数/深嵌套：交上游校验，代理不拦截（错误非 `Proxy error`）。"""
    empty_text, empty_error = _invoke(std.session, "hr__get_employee", {})
    extra_text, extra_error = _invoke(
        std.session, "hr__get_employee", {"unexpected": 1}
    )
    nested_text, nested_error = _invoke(
        std.session, "hr__get_employee", {"employee_id": {"nested": [1, 2]}}
    )

    for text, is_error in (
        (empty_text, empty_error),
        (extra_text, extra_error),
        (nested_text, nested_error),
    ):
        assert is_error is True, text
        assert "Proxy error" not in text, text
        assert "employee_id" in text, text


def test_invoke_same_upstream_sequential_no_crosstalk(std: "StdStack") -> None:
    """同上游连续调用连接复用不串话：两次不同 employee_id 各自正确、顺序不乱。"""
    first_text, first_error = _invoke(
        std.session, "hr__get_employee", {"employee_id": "E-1"}
    )
    second_text, second_error = _invoke(
        std.session, "hr__get_employee", {"employee_id": "E-2"}
    )

    assert first_error is False and second_error is False
    assert "employee E-1: Ada Lovelace" in first_text
    assert "employee E-2: Ada Lovelace" in second_text
    assert "E-2" not in first_text
    assert "E-1: Ada" not in second_text


# ---------- gizmos 变体 ----------


def test_invoke_gizmos_variant_roundtrip(launch) -> None:
    """gizmos 变体一次拉起：中文往返、深嵌套交上游校验、慢工具不截断。"""
    variant = launch(
        {"gizmos": {"command": sys.executable, "args": [str(DEMO_DIR / "gizmos.py")]}}
    )

    # 中文参数无损：`chinese_echo` 原样回显中文
    text, is_error = _invoke(
        variant.session, "gizmos__chinese_echo", {"text": "你好，世界"}
    )
    assert is_error is False
    assert text == "收到：你好，世界"

    # 深嵌套参数：代理整包转发，由上游 schema 校验拒绝（非 `Proxy error`）
    nested_text, nested_error = _invoke(
        variant.session,
        "gizmos__echo_env",
        {"key": {"nested": {"deep": [1, 2, 3]}}},
    )
    assert nested_error is True
    assert "Proxy error" not in nested_text, nested_text
    assert "key" in nested_text, nested_text

    # 慢工具不提前掐断：`slow(2)` 耗时 ≥2s 且结果完整
    started = time.monotonic()
    slow_text, slow_error = _invoke(
        variant.session, "gizmos__slow", {"seconds": 2}, timeout=60.0
    )
    elapsed = time.monotonic() - started

    assert slow_error is False
    assert elapsed >= 2.0, elapsed
    assert "slept 2" in slow_text


def test_invoke_concurrent_upstreams_do_not_block(launch) -> None:
    """并发跨上游不阻塞：gizmos slow(2) 与 hr 调用并行，总耗时 < 3.5s。"""
    variant = launch(
        {
            "gizmos": {
                "command": sys.executable,
                "args": [str(DEMO_DIR / "gizmos.py")],
            },
            "hr": {"command": sys.executable, "args": [str(DEMO_DIR / "hr.py")]},
        }
    )

    results: dict[str, tuple[str, bool]] = {}

    def _run_slow() -> None:
        results["slow"] = _invoke(variant.session, "gizmos__slow", {"seconds": 2}, timeout=60.0)

    def _run_hr() -> None:
        results["hr"] = _invoke(
            variant.session, "hr__get_employee", {"employee_id": "E-9"}, timeout=60.0
        )

    slow_thread = threading.Thread(target=_run_slow)
    hr_thread = threading.Thread(target=_run_hr)
    started = time.monotonic()
    slow_thread.start()
    hr_thread.start()
    slow_thread.join(timeout=60.0)
    hr_thread.join(timeout=60.0)
    elapsed = time.monotonic() - started

    assert set(results) == {"slow", "hr"}
    assert elapsed < 3.5, elapsed
    assert results["slow"][1] is False
    assert "slept 2" in results["slow"][0]
    assert results["hr"][1] is False
    assert "employee E-9" in results["hr"][0]
