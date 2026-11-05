"""8.4 config.example.json 必须以 config 契约解析通过。"""

from __future__ import annotations

from pathlib import Path

from grep_mcp_tools.config import load_config

EXAMPLE = Path(__file__).resolve().parents[2] / "config.example.json"


def test_config_example_parses() -> None:
    config = load_config(EXAMPLE)
    assert set(config.servers) == {"hr", "itops", "audit"}
    assert config.servers["hr"].transport == "stdio"
    assert config.servers["itops"].transport == "http"
    assert config.servers["audit"].transport == "sse"


def test_config_example_covers_all_settings_keys() -> None:
    settings = load_config(EXAMPLE).settings
    assert settings.threshold == 30
    assert settings.refresh_interval == 600
    assert settings.fallback_sample_size == 10
    assert settings.description_max_servers == 15
    assert settings.handshake_timeout == 10
    assert settings.index_dir is None
