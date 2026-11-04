"""5.2 _status.json 写盘的单测（TDD：先于实现）。

契约：最小字段 servers.{name}.{state,tools,reason,updated_at}；
tmp+rename 原子写、UTF-8；内容与内存注册表一致。
"""

from __future__ import annotations

import json

from grep_mcp_tools.health import HealthRegistry, write_status_json


def test_write_status_json_fields_and_consistency(tmp_path):
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    registry.record_unreachable("dead_http", reason="connection refused")

    path = write_status_json(registry, tmp_path)

    assert path == tmp_path / "_status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == registry.snapshot()
    assert payload["servers"]["dead_http"]["reason"] == "connection refused"


def test_write_status_json_creates_directory(tmp_path):
    index_root = tmp_path / "nested" / "tools_index"
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=1)

    path = write_status_json(registry, index_root)

    assert path.is_file()


def test_write_status_json_atomic_no_temp_residue(tmp_path):
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=1)
    write_status_json(registry, tmp_path)

    leftovers = [p for p in tmp_path.iterdir() if p.name != "_status.json"]
    assert leftovers == []


def test_write_status_json_utf8_chinese_reason(tmp_path):
    registry = HealthRegistry()
    registry.record_unreachable("dead_http", reason="连接被拒绝")
    path = write_status_json(registry, tmp_path)

    raw = path.read_bytes()
    assert "连接被拒绝".encode("utf-8") in raw
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["servers"]["dead_http"]["reason"] == "连接被拒绝"


def test_write_status_json_overwrites_previous_snapshot(tmp_path):
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    write_status_json(registry, tmp_path)

    registry2 = HealthRegistry()
    registry2.record_unreachable("hr", reason="spawn failed")
    write_status_json(registry2, tmp_path)

    payload = json.loads((tmp_path / "_status.json").read_text(encoding="utf-8"))
    assert payload["servers"]["hr"]["state"] == "unreachable"
