"""B04 任务套件：6 个原型的定稿任务、白名单与参数规则。

唯一来源：`docs/design/batches/B04-agent-harness.md`『任务套件与判分（定稿）』。
提示文本一律光秃秃的自然语言，零工具暗示（实验有效性前提）。
"""

from __future__ import annotations

from tests.agent.tasks.tasks import (
    COMPLETION_WORDS,
    NEGATIVE_WORDS,
    TASKS,
    WRITE_OR_DESTRUCTIVE,
    TaskSpec,
)

__all__ = [
    "TaskSpec",
    "TASKS",
    "NEGATIVE_WORDS",
    "COMPLETION_WORDS",
    "WRITE_OR_DESTRUCTIVE",
]
