"""5.1 健康注册表三态的单测（TDD：先于实现）。

契约：server -> live N 工具 / live 0 工具 / unreachable+原因；
"本轮" = 最近一次握手尝试轮；updated_at 记录握手时刻。
"""

from __future__ import annotations

from datetime import datetime

from grep_mcp_tools.health import HealthRegistry


def test_live_with_tools():
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    entry = registry.get("hr")
    assert entry is not None
    assert entry.state == "live"
    assert entry.tools == 8
    assert entry.reason is None


def test_live_zero_tools():
    registry = HealthRegistry()
    registry.record_live("empty_srv", tool_count=0)
    entry = registry.get("empty_srv")
    assert entry is not None
    assert entry.state == "live"
    assert entry.tools == 0


def test_unreachable_with_reason():
    registry = HealthRegistry()
    registry.record_unreachable("dead_http", reason="connection refused")
    entry = registry.get("dead_http")
    assert entry is not None
    assert entry.state == "unreachable"
    assert entry.tools == 0
    assert entry.reason == "connection refused"


def test_entries_sorted_by_name():
    registry = HealthRegistry()
    registry.record_live("zeta", tool_count=1)
    registry.record_live("alpha", tool_count=2)
    registry.record_unreachable("mid", reason="timeout")
    assert [e.server for e in registry.entries()] == ["alpha", "mid", "zeta"]


def test_unavailable_count():
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    registry.record_unreachable("dead_http", reason="connection refused")
    registry.record_unreachable("dead_sse", reason="timeout")
    assert registry.unavailable_count() == 2


def test_live_servers_include_zero_tool_services():
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    registry.record_live("empty_srv", tool_count=0)
    registry.record_unreachable("dead_http", reason="connection refused")
    servers = {s.name: s.tool_count for s in registry.live_servers()}
    assert servers == {"hr": 8, "empty_srv": 0}


def test_updated_at_is_iso_timestamp():
    registry = HealthRegistry()
    before = datetime.now().astimezone()
    registry.record_live("hr", tool_count=8)
    entry = registry.get("hr")
    assert entry is not None
    parsed = datetime.fromisoformat(entry.updated_at)
    assert parsed >= before.replace(microsecond=0)


def test_rerecord_overwrites_previous_state():
    registry = HealthRegistry()
    registry.record_unreachable("hr", reason="connection refused")
    registry.record_live("hr", tool_count=8)
    entry = registry.get("hr")
    assert entry is not None
    assert entry.state == "live"
    assert entry.reason is None


def test_snapshot_shape():
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    registry.record_unreachable("dead_http", reason="connection refused")
    snapshot = registry.snapshot()
    assert set(snapshot) == {"servers"}
    assert set(snapshot["servers"]) == {"hr", "dead_http"}
    hr = snapshot["servers"]["hr"]
    assert set(hr) == {"state", "tools", "reason", "updated_at"}
    assert hr["state"] == "live" and hr["tools"] == 8
    dead = snapshot["servers"]["dead_http"]
    assert dead["state"] == "unreachable"
    assert dead["reason"] == "connection refused"
