"""transcript 记录器：落盘、读回与出口脱敏检查。

transcript 是判分与报告的唯一真相源（B04 design 决策 4/10）：一次 run 的完整
消息历史（含 `reasoning_content`）、工具调用、逐轮 usage、最终回答与判定结果
全部落成 JSON，判分规则变化时可离线重算。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


@dataclass
class Transcript:
    """一次实验 run 的完整记录。"""

    run_id: str
    arm: str
    task_id: str
    rep: int
    messages: list[dict]
    tool_calls: list[dict]
    usages: list[dict]
    final_text: str
    gave_up: bool
    verdict: dict | None
    duration_s: float


def make_run_id(arm: str, task_id: str, rep: int) -> str:
    """run-id 命名：`{arm}-{task_id}-r{rep}`。"""
    return f"{arm}-{task_id}-r{rep}"


def save_transcript(transcript: Transcript, out_dir: Path) -> Path:
    """把 transcript 写成 `<out_dir>/<run_id>.json`（目录不存在时创建）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{transcript.run_id}.json"
    payload = json.dumps(asdict(transcript), ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")
    return path


def load_transcript(path: Path) -> Transcript:
    """读回 transcript；只接受当前 schema 的字段。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    known = {field.name for field in fields(Transcript)}
    return Transcript(**{key: value for key, value in data.items() if key in known})


def _iter_strings(value: Any):
    """递归产出结构里所有字符串表示（含 mapping 的键）。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _iter_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            yield from _iter_strings(item)
    else:
        yield str(value)


def assert_no_secret(value: Any, secret: str) -> None:
    """递归检查 `value` 的任何字符串表示里不含 `secret`；命中即 AssertionError。"""
    # 空串会命中一切，视为没有可检查的密钥。
    if not secret:
        return
    for text in _iter_strings(value):
        if secret in text:
            raise AssertionError(f"transcript contains secret substring {secret!r}")
