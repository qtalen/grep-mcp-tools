"""B04 五上游编成：实验编排与冒烟测试共用的唯一编成来源。

编成定稿（批次文档『上游编成』）：hr（stdio，由代理/直挂臂自己 spawn）、
itops（streamable-http）、audit（sse）、docs（sse）、fleet_gen
（streamable-http）；四个 HTTP/SSE 上游由测试进程拉起，端口顺序固定为
itops / audit / docs / fleet_gen。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from tests.agent.arms import UpstreamSpec
from tests.e2e.harness import DEMO_DIR, DemoProcess, spawn_demo
from tests.shared.proc import pick_ports

#: 四个 HTTP/SSE 上游的端口顺序（`pick_fleet_ports()` / `spawn_http_demos()` 同序）。
HTTP_PORT_ORDER: tuple[str, ...] = ("itops", "audit", "docs", "fleet_gen")

#: 上游名称 → 监听路径；hr 走 stdio，无 URL。
HTTP_PATHS: dict[str, str] = {
    "itops": "/mcp",
    "audit": "/sse",
    "docs": "/sse",
    "fleet_gen": "/mcp",
}

#: 上游名称 → demo 脚本文件名。
HTTP_SCRIPTS: dict[str, str] = {
    "itops": "itops.py",
    "audit": "audit.py",
    "docs": "docs.py",
    "fleet_gen": "fleet_gen.py",
}

#: 五上游骨架：名称与传输固定；HTTP 上游的 URL 由 `build_upstream_specs` 填。
FLEET: tuple[UpstreamSpec, ...] = (
    UpstreamSpec(
        name="hr",
        transport="stdio",
        command=sys.executable,
        args=(str(DEMO_DIR / "hr.py"),),
    ),
    UpstreamSpec(name="itops", transport="streamable-http"),
    UpstreamSpec(name="audit", transport="sse"),
    UpstreamSpec(name="docs", transport="sse"),
    UpstreamSpec(name="fleet_gen", transport="streamable-http"),
)


def pick_fleet_ports() -> list[int]:
    """取四个空闲端口，顺序对应 itops / audit / docs / fleet_gen。"""
    return pick_ports(len(HTTP_PORT_ORDER))


def build_server_config(ports: Sequence[int]) -> dict:
    """给代理用的 `mcpServers` 字典（hr 走 stdio，其余走 url）。"""
    hr = next(spec for spec in FLEET if spec.transport == "stdio")
    transports = {spec.name: spec.transport for spec in FLEET}
    servers: dict[str, dict] = {
        "hr": {"command": hr.command, "args": list(hr.args)}
    }
    for name, port in zip(HTTP_PORT_ORDER, ports):
        entry: dict = {"url": f"http://127.0.0.1:{port}{HTTP_PATHS[name]}"}
        if transports[name] == "sse":
            entry["type"] = "sse"
        servers[name] = entry
    return servers


def build_upstream_specs(ports: Sequence[int]) -> list[UpstreamSpec]:
    """直挂臂用的上游连接参数（hr 原样，HTTP 上游按端口填 url）。"""
    specs: list[UpstreamSpec] = []
    for spec in FLEET:
        if spec.transport == "stdio":
            specs.append(spec)
            continue
        index = HTTP_PORT_ORDER.index(spec.name)
        specs.append(
            UpstreamSpec(
                name=spec.name,
                transport=spec.transport,
                url=f"http://127.0.0.1:{ports[index]}{HTTP_PATHS[spec.name]}",
            )
        )
    return specs


def spawn_http_demos(ports: Sequence[int]) -> list[DemoProcess]:
    """拉起四个 HTTP/SSE 假上游，端口按 `HTTP_PORT_ORDER` 对应。"""
    return [
        spawn_demo(HTTP_SCRIPTS[name], port)
        for name, port in zip(HTTP_PORT_ORDER, ports)
    ]
