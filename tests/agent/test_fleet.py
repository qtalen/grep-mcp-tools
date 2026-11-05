"""fleet.py 编成单测：名称/传输固定、端口顺序、代理配置与直挂 spec 形状。"""

from __future__ import annotations

import sys

from tests.agent.fleet import (
    FLEET,
    build_server_config,
    build_upstream_specs,
    pick_fleet_ports,
)

PORTS = [41001, 41002, 41003, 41004]


def test_fleet_names_and_transports_are_fixed() -> None:
    assert [(spec.name, spec.transport) for spec in FLEET] == [
        ("hr", "stdio"),
        ("itops", "streamable-http"),
        ("audit", "sse"),
        ("docs", "sse"),
        ("fleet_gen", "streamable-http"),
    ]
    hr = FLEET[0]
    assert hr.command == sys.executable
    assert hr.args and hr.args[0].endswith("hr.py")


def test_pick_fleet_ports_returns_four_distinct_ports() -> None:
    ports = pick_fleet_ports()

    assert len(ports) == 4
    assert len(set(ports)) == 4


def test_build_server_config_keeps_stdio_and_urls() -> None:
    config = build_server_config(PORTS)

    assert config["hr"] == {"command": sys.executable, "args": [FLEET[0].args[0]]}
    assert config["itops"] == {"url": "http://127.0.0.1:41001/mcp"}
    assert config["audit"] == {"url": "http://127.0.0.1:41002/sse", "type": "sse"}
    assert config["docs"] == {"url": "http://127.0.0.1:41003/sse", "type": "sse"}
    assert config["fleet_gen"] == {"url": "http://127.0.0.1:41004/mcp"}


def test_build_upstream_specs_follows_port_order() -> None:
    specs = build_upstream_specs(PORTS)

    assert [(spec.name, spec.transport) for spec in specs] == [
        (spec.name, spec.transport) for spec in FLEET
    ]
    assert specs[0].command == sys.executable
    assert specs[0].args == FLEET[0].args
    assert specs[1].url == "http://127.0.0.1:41001/mcp"
    assert specs[2].url == "http://127.0.0.1:41002/sse"
    assert specs[3].url == "http://127.0.0.1:41003/sse"
    assert specs[4].url == "http://127.0.0.1:41004/mcp"
