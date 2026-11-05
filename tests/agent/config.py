"""Agent 测试台的配置读取。

设计约束（B04 design 决策 8）：只认项目根 `.env`，用 `dotenv_values()` 拿到
纯字典后显式取值，**绝不读写 `os.environ`**——避免「shell 导出」与 `.env`
之间的优先级歧义。必填项缺失即 fail-fast，可选模型参数给默认值。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_MODEL = "deepseek-flash"
DEFAULT_REASONING_EFFORT = "low"


class ConfigError(RuntimeError):
    """配置缺失/非法。"""


@dataclass(frozen=True)
class AgentConfig:
    """DeepSeek OpenAI 兼容端点的连接参数。"""

    base_url: str
    api_key: str
    model: str = DEFAULT_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT


def project_root() -> Path:
    """仓库根目录（本文件位于 `<root>/tests/agent/config.py`）。"""
    return Path(__file__).resolve().parents[2]


def _clean(value: str | None) -> str | None:
    """去首尾空白；空串视为缺失。"""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _require(values: dict[str, str | None], key: str, env_file: Path) -> str:
    value = _clean(values.get(key))
    if value is None:
        raise ConfigError(
            f"缺少 {key}（或值为空）。请复制 .env.example 为 .env 并填写；"
            f"当前读取的文件：{env_file}"
        )
    return value


def _optional(values: dict[str, str | None], key: str, default: str) -> str:
    value = _clean(values.get(key))
    return default if value is None else value


def load_config(env_file: Path | None = None) -> AgentConfig:
    """从 `.env` 读取配置；默认位置为项目根 `.env`。

    `dotenv_values()` 只解析文件，不会写入/读取 `os.environ`，因此进程环境
    变量不会参与兜底或覆盖。
    """
    path = Path(env_file) if env_file is not None else project_root() / ".env"
    values = dotenv_values(path)

    return AgentConfig(
        base_url=_require(values, "DEEPSEEK_BASE_URL", path),
        api_key=_require(values, "DEEPSEEK_API_KEY", path),
        model=_optional(values, "DEEPSEEK_MODEL", DEFAULT_MODEL),
        reasoning_effort=_optional(
            values, "DEEPSEEK_REASONING_EFFORT", DEFAULT_REASONING_EFFORT
        ),
    )
