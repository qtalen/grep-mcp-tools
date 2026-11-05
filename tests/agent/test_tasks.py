"""任务套件定义单测：6 条齐全、提示原样、标记与词表正确。"""

from __future__ import annotations

from tests.agent.tasks import COMPLETION_WORDS, NEGATIVE_WORDS, TASKS, TaskSpec

EXPECTED_IDS = [
    "t1_explicit",
    "t2_vocab",
    "t3_over",
    "t4_chain",
    "t5_negative",
    "t6_split",
]

EXPECTED_PROMPTS = {
    "t1_explicit": "帮查一下工号 E1001 的年假余额",
    "t2_vocab": "这条配置记录是谁改的？我想查变更历史",
    "t3_over": "查一下 east_1 区的报表",
    "t4_chain": "查工号 E1001 还剩几天年假；若剩 5 天以上，帮他提交 2 天年假申请，理由写\"家事\"",
    "t5_negative": "在 GitHub 上给 grep-mcp-tools 仓库点个 star",
    "t6_split": "在运维知识库搜一下部署文档，再在人事档案里查张三的记录",
}

TOOL_HINTS = ("search_mcp_tool", "invoke_this_tool", "check_mcp_status", "搜索工具")


def _by_id(task_id: str) -> TaskSpec:
    return next(task for task in TASKS if task.id == task_id)


def test_all_six_tasks_present_in_order() -> None:
    assert [task.id for task in TASKS] == EXPECTED_IDS


def test_prompts_are_verbatim() -> None:
    for task in TASKS:
        assert task.prompt == EXPECTED_PROMPTS[task.id]


def test_prompts_contain_no_tool_hints() -> None:
    for task in TASKS:
        for hint in TOOL_HINTS:
            assert hint not in task.prompt, f"{task.id} leaks tool hint {hint!r}"


def test_expected_tools_match_batch_table() -> None:
    assert _by_id("t1_explicit").expected == ("hr__get_leave_balance",)
    assert _by_id("t2_vocab").expected == ("audit__audit_trail_retention",)
    assert _by_id("t3_over").expected == ("fleet_gen__region_east_1_report",)
    assert _by_id("t4_chain").expected == (
        "hr__get_leave_balance",
        "hr__apply_leave",
    )
    assert _by_id("t5_negative").expected == ()
    assert _by_id("t6_split").expected == ("itops__search", "hr__search")


def test_flags_are_correct() -> None:
    assert _by_id("t4_chain").ordered is True
    assert all(task.ordered is False for task in TASKS if task.id != "t4_chain")
    assert _by_id("t5_negative").negative is True
    assert all(task.negative is False for task in TASKS if task.id != "t5_negative")


def test_whitelist_covers_expected_and_excludes_interference() -> None:
    for task in TASKS:
        assert set(task.expected) <= task.whitelist
    assert _by_id("t5_negative").whitelist == frozenset()
    assert "docs__search_docs" not in _by_id("t6_split").whitelist


def test_param_rules_are_declared_for_expected_targets() -> None:
    assert _by_id("t1_explicit").param_rules == {
        "hr__get_leave_balance": {"employee_id": "E1001"}
    }
    assert _by_id("t2_vocab").param_rules == {"audit__audit_trail_retention": {}}
    assert _by_id("t3_over").param_rules == {}
    assert _by_id("t4_chain").param_rules == {
        "hr__get_leave_balance": {"employee_id": "E1001"},
        "hr__apply_leave": {"employee_id": "E1001", "days": 2, "reason": "家事"},
    }
    assert _by_id("t6_split").param_rules == {
        "itops__search": {"query": None},
        "hr__search": {"query": None},
    }


def test_negative_and_completion_word_lists_are_versioned() -> None:
    for word in (
        "无法",
        "不能",
        "没有",
        "不支持",
        "做不到",
        "缺少",
        "找不到",
        "cannot",
        "unable",
        "can't",
        "don't have",
        "no tool",
        "no capability",
    ):
        assert word in NEGATIVE_WORDS
    for word in (
        "已完成",
        "已点",
        "已 star",
        "已star",
        "已成功",
        "已经成功",
        "成功了",
        "完成了",
        "done",
        "starred",
        "succeeded",
    ):
        assert word in COMPLETION_WORDS
    assert "成功" not in COMPLETION_WORDS
