"""配置读取单测：只认 `.env`，进程环境变量一律忽略。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.agent.config import AgentConfig, ConfigError, load_config, project_root


def _env_file(tmp_path: Path, **values: str) -> Path:
    """写一个临时 .env（键值逐行，保持传入顺序）。"""
    lines = [f"{key}={value}" for key, value in values.items()]
    path = tmp_path / ".env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_project_root_is_repo_root() -> None:
    root = project_root()
    assert (root / "pyproject.toml").is_file()
    assert root.name == "grep-mcp-tools"


def test_loads_all_values(tmp_path: Path) -> None:
    env_file = _env_file(
        tmp_path,
        DEEPSEEK_BASE_URL="https://example.invalid/v1",
        DEEPSEEK_API_KEY="sk-test-not-real",
        DEEPSEEK_MODEL="deepseek-chat",
        DEEPSEEK_REASONING_EFFORT="high",
    )

    config = load_config(env_file)

    assert config == AgentConfig(
        base_url="https://example.invalid/v1",
        api_key="sk-test-not-real",
        model="deepseek-chat",
        reasoning_effort="high",
    )


def test_ignores_process_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env` 缺 API_KEY，即使 shell 有同名变量也必须 fail-fast。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-shell-must-be-ignored")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://shell.invalid/v1")
    env_file = _env_file(tmp_path, DEEPSEEK_BASE_URL="https://file.invalid/v1")

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_file)

    assert "DEEPSEEK_API_KEY" in str(excinfo.value)


def test_environment_alone_is_not_enough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连 `.env` 都不存在时，进程环境变量不能兜底。"""
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://shell.invalid/v1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-shell-must-be-ignored")

    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.env")


def test_missing_api_key_fails_fast(tmp_path: Path) -> None:
    env_file = _env_file(
        tmp_path,
        DEEPSEEK_BASE_URL="https://example.invalid/v1",
    )

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_file)

    message = str(excinfo.value)
    assert "DEEPSEEK_API_KEY" in message
    assert ".env.example" in message


def test_missing_base_url_fails_fast(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path, DEEPSEEK_API_KEY="sk-test-not-real")

    with pytest.raises(ConfigError) as excinfo:
        load_config(env_file)

    assert "DEEPSEEK_BASE_URL" in str(excinfo.value)


def test_blank_required_value_fails_fast(tmp_path: Path) -> None:
    env_file = _env_file(
        tmp_path,
        DEEPSEEK_BASE_URL="https://example.invalid/v1",
        DEEPSEEK_API_KEY="   ",
    )

    with pytest.raises(ConfigError):
        load_config(env_file)


def test_model_and_effort_default_when_missing(tmp_path: Path) -> None:
    env_file = _env_file(
        tmp_path,
        DEEPSEEK_BASE_URL="https://example.invalid/v1",
        DEEPSEEK_API_KEY="sk-test-not-real",
    )

    config = load_config(env_file)

    assert config.model == "deepseek-flash"
    assert config.reasoning_effort == "low"


def test_model_and_effort_default_when_blank(tmp_path: Path) -> None:
    env_file = _env_file(
        tmp_path,
        DEEPSEEK_BASE_URL="https://example.invalid/v1",
        DEEPSEEK_API_KEY="sk-test-not-real",
        DEEPSEEK_MODEL="",
        DEEPSEEK_REASONING_EFFORT="  ",
    )

    config = load_config(env_file)

    assert config.model == "deepseek-flash"
    assert config.reasoning_effort == "low"


def test_strips_surrounding_whitespace(tmp_path: Path) -> None:
    env_file = _env_file(
        tmp_path,
        DEEPSEEK_BASE_URL="  https://example.invalid/v1  ",
        DEEPSEEK_API_KEY="  sk-test-not-real  ",
        DEEPSEEK_MODEL=" deepseek-flash ",
        DEEPSEEK_REASONING_EFFORT=" low ",
    )

    config = load_config(env_file)

    assert config.base_url == "https://example.invalid/v1"
    assert config.api_key == "sk-test-not-real"
    assert config.model == "deepseek-flash"
    assert config.reasoning_effort == "low"
