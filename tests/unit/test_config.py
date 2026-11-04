"""config 解析与 fail-fast 校验的单测（TDD：先于实现落盘）。

契约来源：openspec/changes/b01-walking-skeleton/specs/upstream-config/spec.md
与 docs/design/architecture.md『config 格式』（唯一来源）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grep_mcp_tools.config import ConfigError, load_config


def write_config(tmp_path: Path, payload) -> str:
    path = tmp_path / "config.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# ---------- 结构错误族 ----------


def test_missing_file_fails_fast(tmp_path):
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path / "nope.json")
    assert "nope.json" in str(exc_info.value)


def test_invalid_json_fails_fast(tmp_path):
    path = write_config(tmp_path, "{not json")
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "JSON" in str(exc_info.value)


def test_missing_mcpservers_key_fails_fast(tmp_path):
    path = write_config(tmp_path, {"settings": {}})
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "mcpServers" in str(exc_info.value)


def test_mcpservers_wrong_type_fails_fast(tmp_path):
    path = write_config(tmp_path, {"mcpServers": ["hr"]})
    with pytest.raises(ConfigError):
        load_config(path)


def test_top_level_not_object_fails_fast(tmp_path):
    path = write_config(tmp_path, "[1, 2]")
    with pytest.raises(ConfigError):
        load_config(path)


def test_duplicate_name_case_insensitive_fails_fast(tmp_path):
    path = write_config(
        tmp_path,
        {
            "mcpServers": {
                "hr": {"command": "python", "args": ["hr.py"]},
                "HR": {"command": "python", "args": ["hr2.py"]},
            }
        },
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    msg = str(exc_info.value).lower()
    assert "duplicate" in msg and "hr" in msg


def test_entry_not_object_fails_fast(tmp_path):
    path = write_config(tmp_path, {"mcpServers": {"hr": "python hr.py"}})
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "hr" in str(exc_info.value)


def test_command_wrong_type_fails_fast(tmp_path):
    path = write_config(tmp_path, {"mcpServers": {"hr": {"command": 42}}})
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "hr" in str(exc_info.value) and "command" in str(exc_info.value)


def test_args_wrong_type_fails_fast(tmp_path):
    path = write_config(
        tmp_path, {"mcpServers": {"hr": {"command": "python", "args": "hr.py"}}}
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "args" in str(exc_info.value)


def test_env_wrong_type_fails_fast(tmp_path):
    path = write_config(
        tmp_path,
        {"mcpServers": {"hr": {"command": "python", "env": {"DEBUG": 1}}}},
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "env" in str(exc_info.value)


# ---------- 服务名白名单 ----------


@pytest.mark.parametrize("bad_name", ["hr__legacy", "../evil", "a/b", "hr tool", "hr!", ""])
def test_server_name_whitelist_rejects(tmp_path, bad_name):
    path = write_config(
        tmp_path, {"mcpServers": {bad_name: {"command": "python"}}}
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "name" in str(exc_info.value)


def test_server_name_whitelist_accepts_hardhat(tmp_path):
    path = write_config(
        tmp_path, {"mcpServers": {"my-srv_2": {"command": "python"}}}
    )
    config = load_config(path)
    assert "my-srv_2" in config.servers


# ---------- 传输消歧 ----------


def test_command_and_url_both_present_rejected(tmp_path):
    path = write_config(
        tmp_path,
        {
            "mcpServers": {
                "hr": {
                    "command": "python",
                    "url": "http://127.0.0.1:8000/mcp",
                }
            }
        },
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "hr" in str(exc_info.value)


def test_neither_command_nor_url_rejected(tmp_path):
    path = write_config(tmp_path, {"mcpServers": {"hr": {"args": ["x"]}}})
    with pytest.raises(ConfigError):
        load_config(path)


def test_illegal_type_value_rejected(tmp_path):
    path = write_config(
        tmp_path,
        {
            "mcpServers": {
                "hr": {"url": "http://127.0.0.1:8000/mcp", "type": "grpc"}
            }
        },
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "type" in str(exc_info.value)


def test_non_http_url_rejected(tmp_path):
    path = write_config(
        tmp_path,
        {"mcpServers": {"hr": {"url": "ftp://example.com/mcp"}}},
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "url" in str(exc_info.value)


def test_sse_type_with_command_rejected(tmp_path):
    path = write_config(
        tmp_path,
        {
            "mcpServers": {
                "hr": {"command": "python", "type": "sse"}
            }
        },
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    assert "sse" in str(exc_info.value).lower() or "type" in str(exc_info.value)


def test_stdio_type_with_url_rejected(tmp_path):
    """与 'sse 配 command' 对称：显式 stdio 声明配 url 也是传输冲突。"""
    path = write_config(
        tmp_path,
        {
            "mcpServers": {
                "hr": {"url": "http://127.0.0.1:8000/mcp", "type": "stdio"}
            }
        },
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_http_type_with_command_only_rejected(tmp_path):
    path = write_config(
        tmp_path,
        {"mcpServers": {"hr": {"command": "python", "type": "http"}}},
    )
    with pytest.raises(ConfigError):
        load_config(path)


# ---------- 合法形态 ----------


def test_valid_stdio_entry(tmp_path):
    path = write_config(
        tmp_path,
        {
            "mcpServers": {
                "hr": {
                    "command": "python",
                    "args": ["hr.py"],
                    "env": {"HR_MODE": "demo"},
                }
            }
        },
    )
    config = load_config(path)
    entry = config.servers["hr"]
    assert entry.transport == "stdio"
    assert entry.command == "python"
    assert entry.args == ["hr.py"]
    assert entry.env == {"HR_MODE": "demo"}


def test_valid_http_entry_defaults_to_streamable(tmp_path):
    path = write_config(
        tmp_path,
        {
            "mcpServers": {
                "itops": {
                    "url": "http://127.0.0.1:8123/mcp",
                    "headers": {"X-DEMO": "1"},
                }
            }
        },
    )
    config = load_config(path)
    entry = config.servers["itops"]
    assert entry.transport == "http"
    assert entry.url == "http://127.0.0.1:8123/mcp"
    assert entry.headers == {"X-DEMO": "1"}


def test_valid_sse_entry(tmp_path):
    path = write_config(
        tmp_path,
        {
            "mcpServers": {
                "audit": {
                    "url": "http://127.0.0.1:8124/sse",
                    "type": "sse",
                }
            }
        },
    )
    config = load_config(path)
    assert config.servers["audit"].transport == "sse"


def test_explicit_stdio_type_with_command_accepted(tmp_path):
    path = write_config(
        tmp_path,
        {"mcpServers": {"hr": {"command": "python", "type": "stdio"}}},
    )
    config = load_config(path)
    assert config.servers["hr"].transport == "stdio"


def test_server_name_with_trailing_newline_rejected(tmp_path):
    """正则 $ 允许尾随换行，必须用 fullmatch 拒绝（否则索引文件名含控制符）。"""
    path = write_config(
        tmp_path, {"mcpServers": {"hr\n": {"command": "python"}}}
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_empty_mcpservers_is_valid(tmp_path):
    path = write_config(tmp_path, {"mcpServers": {}})
    config = load_config(path)
    assert config.servers == {}


def test_utf8_bom_config_is_accepted(tmp_path):
    """Windows 编辑器/PowerShell 常写 BOM；按 UTF-8 读取时容忍 BOM。"""
    path = tmp_path / "config.json"
    payload = json.dumps({"mcpServers": {"hr": {"command": "python"}}})
    path.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))
    config = load_config(path)
    assert set(config.servers) == {"hr"}
