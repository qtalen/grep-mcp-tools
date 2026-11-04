"""健康注册表与人类侧快照 ``_status.json``。

契约唯一来源：docs/design/architecture.md『健康注册表』与 `_status.json`
最小字段。注册表是握手循环的副产品：B01 为启动快照；"本轮" = 最近一次
握手尝试轮（B02 起由刷新翻转状态）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from grep_mcp_tools.search import LiveServer

__all__ = [
    "HealthEntry",
    "HealthRegistry",
    "emit_startup_summary",
    "startup_summary",
    "write_status_json",
]


@dataclass(frozen=True)
class HealthEntry:
    """单服务健康态：live N tools / live 0 tools / unreachable+reason。"""

    server: str
    state: str  # "live" | "unreachable"
    tools: int
    reason: str | None
    updated_at: str


class HealthRegistry:
    """内存注册表（一个配置服务一条记录）。"""

    def __init__(self) -> None:
        self._entries: dict[str, HealthEntry] = {}

    def record_live(self, server: str, *, tool_count: int) -> None:
        self._entries[server] = HealthEntry(
            server=server,
            state="live",
            tools=tool_count,
            reason=None,
            updated_at=_now(),
        )

    def record_unreachable(self, server: str, *, reason: str) -> None:
        self._entries[server] = HealthEntry(
            server=server,
            state="unreachable",
            tools=0,
            reason=reason,
            updated_at=_now(),
        )

    def get(self, server: str) -> HealthEntry | None:
        return self._entries.get(server)

    def entries(self) -> list[HealthEntry]:
        return [self._entries[name] for name in sorted(self._entries)]

    def unavailable_count(self) -> int:
        return sum(1 for entry in self._entries.values() if entry.state != "live")

    def live_servers(self) -> list[LiveServer]:
        return [
            LiveServer(name=entry.server, tool_count=entry.tools)
            for entry in self.entries()
            if entry.state == "live"
        ]

    def snapshot(self) -> dict:
        """`_status.json` 与 check_mcp_status 共用的快照形态。"""
        return {
            "servers": {
                entry.server: {
                    "state": entry.state,
                    "tools": entry.tools,
                    "reason": entry.reason,
                    "updated_at": entry.updated_at,
                }
                for entry in self.entries()
            }
        }


def write_status_json(registry: HealthRegistry, index_root: Path) -> Path:
    """原子写 ``<index_root>/_status.json``（tmp+rename, UTF-8）。"""
    index_root = Path(index_root)
    index_root.mkdir(parents=True, exist_ok=True)
    target = index_root / "_status.json"

    fd, tmp_name = tempfile.mkstemp(dir=index_root, prefix=".status-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(registry.snapshot(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def startup_summary(registry: HealthRegistry) -> str:
    """stderr 启动摘要一行：``x/y upstreams indexed (server FAILED: reason)``。"""
    entries = registry.entries()
    total = len(entries)
    live = [entry for entry in entries if entry.state == "live"]
    base = f"{len(live)}/{total} upstreams indexed"
    failures = [
        f"{entry.server} FAILED: {entry.reason}"
        for entry in entries
        if entry.state != "live"
    ]
    if failures:
        return f"{base} ({'; '.join(failures)})"
    return base


def emit_startup_summary(registry: HealthRegistry) -> None:
    print(startup_summary(registry), file=sys.stderr)
