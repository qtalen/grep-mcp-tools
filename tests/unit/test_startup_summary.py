"""6.3 启动摘要与 stdout 零污染的单测。

契约：stderr 一行 ``x/y upstreams connected (server FAILED: reason)``；
stdout 是协议通道，零污染。
"""

from __future__ import annotations

from grep_mcp_tools.health import HealthRegistry, emit_startup_summary, startup_summary


def test_summary_all_live():
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    registry.record_live("itops", tool_count=12)
    registry.record_live("audit", tool_count=3)
    assert startup_summary(registry) == "3/3 upstreams connected"


def test_summary_with_failure():
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    registry.record_live("itops", tool_count=12)
    registry.record_live("audit", tool_count=3)
    registry.record_unreachable("dead_http", reason="connection refused")
    assert startup_summary(registry) == (
        "3/4 upstreams connected (dead_http FAILED: connection refused)"
    )


def test_summary_counts_zero_tool_service_as_live():
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    registry.record_live("empty_srv", tool_count=0)
    assert startup_summary(registry) == "2/2 upstreams connected"


def test_emit_writes_stderr_only(capsys):
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    registry.record_unreachable("dead_http", reason="connection refused")

    emit_startup_summary(registry)

    captured = capsys.readouterr()
    assert "1/2 upstreams connected" in captured.err
    assert "dead_http FAILED: connection refused" in captured.err
    assert captured.out == ""
