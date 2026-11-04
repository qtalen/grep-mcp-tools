"""settings 合并与索引目录解析的单测。

契约来源：docs/design/architecture.md settings 键表（唯一来源）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grep_mcp_tools.config import ConfigError, load_config


def write_config(tmp_path: Path, payload) -> str:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_settings_defaults_follow_architecture_table(tmp_path):
    path = write_config(tmp_path, {"mcpServers": {}})
    settings = load_config(path).settings
    assert settings.threshold == 30
    assert settings.refresh_interval == 600
    assert settings.fallback_sample_size == 10
    assert settings.description_max_servers == 15
    assert settings.handshake_timeout == 10
    assert settings.index_dir is None


def test_threshold_override_takes_effect(tmp_path):
    path = write_config(tmp_path, {"mcpServers": {}, "settings": {"threshold": 5}})
    settings = load_config(path).settings
    assert settings.threshold == 5
    assert settings.refresh_interval == 600  # 未覆盖键仍取默认


def test_settings_wrong_type_fails_fast(tmp_path):
    path = write_config(tmp_path, {"mcpServers": {}, "settings": {"threshold": "30"}})
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "threshold" in str(exc_info.value)


def test_settings_bool_rejected_for_int_key(tmp_path):
    """bool 是 int 子类，但配置语义上不是整数。"""
    path = write_config(tmp_path, {"mcpServers": {}, "settings": {"threshold": True}})
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("key", ["threshold", "fallback_sample_size", "handshake_timeout"])
@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_settings_rejected(tmp_path, key, bad):
    """零/负值会产生静默怪异行为（如 threshold=0 全部超量），必须 fail-fast。"""
    path = write_config(tmp_path, {"mcpServers": {}, "settings": {key: bad}})
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert key in str(exc_info.value)


def test_unknown_settings_key_fails_fast(tmp_path):
    path = write_config(tmp_path, {"mcpServers": {}, "settings": {"thresold": 5}})
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "thresold" in str(exc_info.value)


def test_settings_not_object_fails_fast(tmp_path):
    path = write_config(tmp_path, {"mcpServers": {}, "settings": []})
    with pytest.raises(ConfigError):
        load_config(path)


def test_index_root_defaults_to_config_dir(tmp_path):
    path = write_config(tmp_path, {"mcpServers": {}})
    config = load_config(path)
    assert config.index_root() == tmp_path / "tools_index"


def test_index_root_override(tmp_path):
    custom = tmp_path / "custom_index"
    path = write_config(
        tmp_path, {"mcpServers": {}, "settings": {"index_dir": str(custom)}}
    )
    config = load_config(path)
    assert config.index_root() == custom


def test_relative_index_dir_resolves_against_config_dir(tmp_path, monkeypatch):
    """相对 index_dir 必须按 config 目录解析（防不同 cwd 产生两套索引）。"""
    config_dir = tmp_path / "conf"
    config_dir.mkdir()
    path = config_dir / "config.json"
    path.write_text(
        json.dumps({"mcpServers": {}, "settings": {"index_dir": "idx"}}),
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    config = load_config(path)
    assert config.index_root() == config_dir / "idx"
