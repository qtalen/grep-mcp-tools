"""config 解析与 fail-fast 校验。

契约唯一来源：docs/design/architecture.md『config 格式』与 settings 键表。
本模块不做任何 IO 之外的事：解析、校验、返回冻结的数据类；
一切配置问题以 :class:`ConfigError` 大声失败（黄金律 4）。

校验规则（architecture 契约的完整实现）：
- 结构：文件不存在 / 非法 JSON / 顶层非对象 / 缺 ``mcpServers`` 键 /
  ``mcpServers`` 非对象 / 条目非对象 / 字段类型错 / 重复服务名（大小写
  不敏感——Windows 文件系统下 hr 与 HR 会写同一个索引文件）。
- 服务名白名单：``^[A-Za-z0-9_-]+$``（覆盖 ``__`` 禁令与路径安全）。
- 传输消歧：``command`` 与 ``url`` 并存或全无；``type`` 非法值；``url``
  非 ``http(s)://``；``"type": "sse"`` 配 ``command``；对称地，
  ``"type": "stdio"`` 配 ``url``、HTTP 型 ``type`` 配 ``command`` 同为冲突。
- 空配置语义：缺 ``mcpServers`` 键 → fail-fast；``mcpServers: {}`` → 合法。
- settings：键表外的未知键视为拼写错误，fail-fast（防静默回退默认值）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_VALID_TYPES = {"stdio", "http", "sse"}


class ConfigError(Exception):
    """config fail-fast 错误；信息指认具体条目与字段。"""


@dataclass(frozen=True)
class ServerEntry:
    """一个上游服务条目（已消歧后的规范形态）。"""

    name: str
    transport: str  # "stdio" | "http" | "sse"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Settings:
    """settings 键表（默认值以 architecture 键表为唯一来源）。"""

    threshold: int = 30
    refresh_interval: int = 600
    fallback_sample_size: int = 10
    description_max_servers: int = 15
    handshake_timeout: int = 10
    index_dir: str | None = None


@dataclass(frozen=True)
class Config:
    servers: dict[str, ServerEntry]
    settings: Settings
    config_path: Path

    def index_root(self) -> Path:
        """索引目录：settings.index_dir 覆盖（相对路径按 config 同目录解析），
        缺省 = config 同目录 tools_index/。"""
        if self.settings.index_dir:
            candidate = Path(self.settings.index_dir)
            if not candidate.is_absolute():
                candidate = self.config_path.parent / candidate
            return candidate
        return self.config_path.parent / "tools_index"


_INT_SETTINGS = {
    "threshold",
    "refresh_interval",
    "fallback_sample_size",
    "description_max_servers",
    "handshake_timeout",
}


def load_config(path: str | Path) -> Config:
    """读取、解析并校验 config；任何配置问题抛 :class:`ConfigError`。"""
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {config_path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"config file is not valid UTF-8: {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"config top level must be a JSON object, got {type(raw).__name__}")

    if "mcpServers" not in raw:
        raise ConfigError(f"config missing required key 'mcpServers': {config_path}")

    mcp_servers = raw["mcpServers"]
    if not isinstance(mcp_servers, dict):
        raise ConfigError(
            f"'mcpServers' must be an object, got {type(mcp_servers).__name__}"
        )

    settings = _parse_settings(raw.get("settings", {}))
    servers = _parse_servers(mcp_servers)
    return Config(servers=servers, settings=settings, config_path=config_path)


def _parse_servers(raw: dict) -> dict[str, ServerEntry]:
    servers: dict[str, ServerEntry] = {}
    seen_lowercase: dict[str, str] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            raise ConfigError(
                f"invalid server name {name!r}: must match ^[A-Za-z0-9_-]+$ "
                "(no path characters)"
            )
        if "__" in name:
            raise ConfigError(
                f"invalid server name {name!r}: must not contain '__' "
                "(the target namespace separator)"
            )
        lower = name.lower()
        if lower in seen_lowercase:
            raise ConfigError(
                f"duplicate server name (case-insensitive): {seen_lowercase[lower]!r} and {name!r}"
            )
        seen_lowercase[lower] = name

        if not isinstance(entry, dict):
            raise ConfigError(f"server {name!r}: entry must be an object, got {type(entry).__name__}")

        servers[name] = _parse_entry(name, entry)
    return servers


def _parse_entry(name: str, entry: dict) -> ServerEntry:
    command = entry.get("command")
    url = entry.get("url")
    declared_type = entry.get("type")

    if command is not None and not isinstance(command, str):
        raise ConfigError(f"server {name!r}: 'command' must be a string")
    if url is not None and not isinstance(url, str):
        raise ConfigError(f"server {name!r}: 'url' must be a string")
    if declared_type is not None and declared_type not in _VALID_TYPES:
        raise ConfigError(
            f"server {name!r}: 'type' must be one of {sorted(_VALID_TYPES)}, got {declared_type!r}"
        )

    if command is not None and url is not None:
        raise ConfigError(f"server {name!r}: 'command' and 'url' are both set — pick one transport")

    if command is not None:
        if declared_type in ("http", "sse"):
            raise ConfigError(
                f"server {name!r}: type {declared_type!r} requires 'url', not 'command'"
            )
        transport = "stdio"
    elif url is not None:
        if not (url.lower().startswith("http://") or url.lower().startswith("https://")):
            raise ConfigError(f"server {name!r}: 'url' must use http(s):// scheme, got {url!r}")
        if declared_type == "stdio":
            raise ConfigError(f"server {name!r}: type 'stdio' requires 'command', not 'url'")
        transport = "sse" if declared_type == "sse" else "http"
    else:
        raise ConfigError(f"server {name!r}: neither 'command' nor 'url' is set")

    args = _parse_string_list(name, entry, "args")
    env = _parse_string_map(name, entry, "env")
    headers = _parse_string_map(name, entry, "headers")

    return ServerEntry(
        name=name,
        transport=transport,
        command=command,
        args=args,
        env=env,
        url=url,
        headers=headers,
    )


def _parse_string_list(name: str, entry: dict, key: str) -> list[str]:
    value = entry.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"server {name!r}: '{key}' must be a list of strings")
    return list(value)


def _parse_string_map(name: str, entry: dict, key: str) -> dict[str, str]:
    value = entry.get(key, {})
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ConfigError(f"server {name!r}: '{key}' must be an object of string to string")
    return dict(value)


def _parse_settings(raw: object) -> Settings:
    if not isinstance(raw, dict):
        raise ConfigError(f"'settings' must be an object, got {type(raw).__name__}")

    known = set(_INT_SETTINGS) | {"index_dir"}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(
            f"unknown settings key(s) {unknown}: known keys are {sorted(known)} "
            "(typos fail fast instead of silently using defaults)"
        )

    kwargs: dict[str, object] = {}
    for key in _INT_SETTINGS:
        if key in raw:
            value = raw[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"settings.{key} must be an integer, got {value!r}")
            if value < 1:
                raise ConfigError(f"settings.{key} must be >= 1, got {value!r}")
            kwargs[key] = value
    if "index_dir" in raw and raw["index_dir"] is not None:
        value = raw["index_dir"]
        if not isinstance(value, str):
            raise ConfigError(f"settings.index_dir must be a string or null, got {value!r}")
        kwargs["index_dir"] = value

    return Settings(**kwargs)  # type: ignore[arg-type]
