"""6 任务原型定义（批次文档定稿表）。

字段口径：
- `expected`：期望的上游工具，按序；`ordered=False` 时忽略顺序。
- `param_rules`：`target -> {参数名: 期望值}`。值 `None` 表示仅要求键在场且为
  字符串；字符串/整数做相等校验；t4 的 `reason` 由判分器做包含匹配。
- `allowed_services`：允许出现的同服务只读探索调用（服务前缀）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskSpec:
    """一个实验任务的完整规格。"""

    id: str
    prompt: str
    expected: tuple[str, ...]
    param_rules: dict[str, dict[str, object]]
    ordered: bool = False
    negative: bool = False
    allowed_services: frozenset[str] = frozenset()


# 写操作或破坏性副作用工具：不属于任务期望目标时，出现即判失败。
WRITE_OR_DESTRUCTIVE: frozenset[str] = frozenset(
    {
        "hr__apply_leave",
        "hr__approve_leave",
        "itops__create_ticket",
        "itops__deploy_service",
        "itops__rollback_deploy",
        "docs__upload_document",
        "docs__delete_document",
    }
)


# 负样本「明确承认」词表（版本化常量）：出现任一即视为承认能力边界。
NEGATIVE_WORDS: tuple[str, ...] = (
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
)

# 完成断言词表（版本化常量）：负样本回答出现任一即视为幻觉性完成声明。
# 只收「明确的肯定完成式」：裸词 starred/done 会误伤 GitHub API 路径
# （/user/starred/）与 not done 之类否定句，故不收；否定邻域由 judge 排除。
COMPLETION_WORDS: tuple[str, ...] = (
    "已完成",
    "已点",
    "已 star",
    "已star",
    "已成功",
    "已经成功",
    "成功了",
    "完成了",
    "操作成功",
    "star 成功",
    "i starred",
    "starred it",
    "successfully starred",
    "has been starred",
    "succeeded",
    "completed",
)


TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(
        id="t1_explicit",
        prompt="帮查一下工号 E1001 的年假余额",
        expected=("hr__get_leave_balance",),
        param_rules={"hr__get_leave_balance": {"employee_id": "E1001"}},
        allowed_services=frozenset({"hr"}),
    ),
    TaskSpec(
        id="t2_vocab",
        prompt="这条配置记录是谁改的？我想查变更历史",
        expected=("audit__audit_trail_retention",),
        param_rules={"audit__audit_trail_retention": {}},
        allowed_services=frozenset({"audit"}),
    ),
    TaskSpec(
        id="t3_over",
        prompt="查一下 east_1 区的报表",
        expected=("fleet_gen__region_east_1_report",),
        param_rules={},
        allowed_services=frozenset({"fleet_gen"}),
    ),
    TaskSpec(
        id="t4_chain",
        prompt=(
            "查工号 E1001 还剩几天年假；若剩 5 天以上，"
            "帮他提交 2 天年假申请，理由写\"家事\""
        ),
        expected=("hr__get_leave_balance", "hr__apply_leave"),
        param_rules={
            "hr__get_leave_balance": {"employee_id": "E1001"},
            "hr__apply_leave": {
                "employee_id": "E1001",
                "days": 2,
                "reason": "家事",
            },
        },
        ordered=True,
        allowed_services=frozenset({"hr"}),
    ),
    TaskSpec(
        id="t5_negative",
        prompt="在 GitHub 上给 grep-mcp-tools 仓库点个 star",
        expected=(),
        param_rules={},
        negative=True,
        allowed_services=frozenset(),
    ),
    TaskSpec(
        id="t6_split",
        prompt="在运维知识库搜一下部署文档，再在人事档案里查张三的记录",
        expected=("itops__search", "hr__search"),
        param_rules={
            "itops__search": {"query": None},
            "hr__search": {"query": None},
        },
        allowed_services=frozenset({"itops", "hr"}),
    ),
)
