"""判分器单测：逐原型覆盖正常、失败与边界。"""

from __future__ import annotations

from tests.agent.judge import Event, judge, normalize_events
from tests.agent.tasks import TASKS, TaskSpec


def _task(task_id: str) -> TaskSpec:
    return next(task for task in TASKS if task.id == task_id)


def _call(name: str, arguments: dict | None = None) -> dict:
    return {"name": name, "arguments": arguments or {}, "result": ""}


def _invoke(target: str, params: dict | None = None) -> dict:
    return _call("invoke_this_tool", {"target": target, "params": params or {}})


def _agent_call(name: str, keywords: list[str] | None = None) -> dict:
    return _call(name, {"keywords": keywords or []})


# ---------- normalize_events ----------


def test_normalize_proxy_arm_keeps_only_invoke_events() -> None:
    calls = [
        _agent_call("search_mcp_tool", ["leave"]),
        _agent_call("check_mcp_status"),
        _invoke("hr__get_leave_balance", {"employee_id": "E1001"}),
        _agent_call("search_mcp_tool", ["apply"]),
    ]

    events = normalize_events("a", calls)

    assert events == [
        Event(name="hr__get_leave_balance", arguments={"employee_id": "E1001"})
    ]


def test_normalize_ablation_arms_behave_like_proxy() -> None:
    calls = [_invoke("audit__audit_trail_retention")]
    for arm in ("a", "c", "d"):
        assert normalize_events(arm, calls) == [
            Event(name="audit__audit_trail_retention", arguments={})
        ]


def test_normalize_direct_arm_keeps_every_call() -> None:
    calls = [
        _call("itops__search", {"query": "deploy"}),
        _call("hr__search", {"query": "张三"}),
    ]

    assert normalize_events("b", calls) == [
        Event(name="itops__search", arguments={"query": "deploy"}),
        Event(name="hr__search", arguments={"query": "张三"}),
    ]


def test_normalize_invoke_without_target_is_an_event() -> None:
    events = normalize_events("a", [_call("invoke_this_tool", {})])

    assert events == [Event(name="", arguments={})]


# ---------- 正向任务 ----------


def test_t1_passes_with_correct_target_and_param() -> None:
    verdict = judge(
        _task("t1_explicit"),
        "a",
        [_invoke("hr__get_leave_balance", {"employee_id": "E1001"})],
        "张三还剩 12 天年假。",
        gave_up=False,
    )

    assert verdict.passed is True
    assert verdict.hallucinated is False


def test_t2_passes_without_params_via_direct_arm() -> None:
    verdict = judge(
        _task("t2_vocab"),
        "b",
        [_call("audit__audit_trail_retention")],
        "记录由 audit 服务保留 400 天。",
        gave_up=False,
    )

    assert verdict.passed is True


def test_t2_fails_when_unexpected_params_are_sent() -> None:
    verdict = judge(
        _task("t2_vocab"),
        "a",
        [_invoke("audit__audit_trail_retention", {"since": "7d"})],
        "查到了。",
        gave_up=False,
    )

    assert verdict.passed is False
    assert "param" in verdict.reason


def test_t3_passes_with_area_param() -> None:
    verdict = judge(
        _task("t3_over"),
        "a",
        [_invoke("fleet_gen__region_east_1_report", {"area": "east_1"})],
        "east_1 区机队正常。",
        gave_up=False,
    )

    assert verdict.passed is True


def test_t3_passes_without_area_param() -> None:
    # area 是可选参数：无参调用同样合法（批次文档判分只要求 target 正确）。
    verdict = judge(
        _task("t3_over"),
        "b",
        [_call("fleet_gen__region_east_1_report")],
        "east_1 区机队正常。",
        gave_up=False,
    )

    assert verdict.passed is True


def test_t3_passes_without_params_via_proxy_arm() -> None:
    verdict = judge(
        _task("t3_over"),
        "a",
        [_invoke("fleet_gen__region_east_1_report")],
        "east_1 区机队正常。",
        gave_up=False,
    )

    assert verdict.passed is True


def test_t3_fails_when_another_region_tool_is_called() -> None:
    verdict = judge(
        _task("t3_over"),
        "b",
        [_call("fleet_gen__region_east_2_report", {"area": "east_2"})],
        "east_2 区机队正常。",
        gave_up=False,
    )

    assert verdict.passed is False


def test_t4_passes_when_chain_is_ordered_and_reason_contains() -> None:
    verdict = judge(
        _task("t4_chain"),
        "a",
        [
            _invoke("hr__get_leave_balance", {"employee_id": "E1001"}),
            _invoke(
                "hr__apply_leave",
                {"employee_id": "E1001", "days": 2, "reason": "家事（家里有事）"},
            ),
        ],
        "已提交 2 天年假申请。",
        gave_up=False,
    )

    assert verdict.passed is True


def test_t4_fails_with_only_one_step() -> None:
    verdict = judge(
        _task("t4_chain"),
        "a",
        [_invoke("hr__get_leave_balance", {"employee_id": "E1001"})],
        "还剩 12 天。",
        gave_up=False,
    )

    assert verdict.passed is False


def test_t4_fails_when_steps_are_out_of_order() -> None:
    verdict = judge(
        _task("t4_chain"),
        "a",
        [
            _invoke(
                "hr__apply_leave",
                {"employee_id": "E1001", "days": 2, "reason": "家事"},
            ),
            _invoke("hr__get_leave_balance", {"employee_id": "E1001"}),
        ],
        "已提交。",
        gave_up=False,
    )

    assert verdict.passed is False


def test_t4_fails_when_reason_does_not_contain_keyword() -> None:
    verdict = judge(
        _task("t4_chain"),
        "a",
        [
            _invoke("hr__get_leave_balance", {"employee_id": "E1001"}),
            _invoke(
                "hr__apply_leave",
                {"employee_id": "E1001", "days": 2, "reason": "personal"},
            ),
        ],
        "已提交。",
        gave_up=False,
    )

    assert verdict.passed is False
    assert "param" in verdict.reason


def test_t6_passes_in_any_order_and_reports_query_type_errors() -> None:
    passed = judge(
        _task("t6_split"),
        "b",
        [
            _call("hr__search", {"query": "张三"}),
            _call("itops__search", {"query": "部署文档"}),
        ],
        "两边都查到了。",
        gave_up=False,
    )
    assert passed.passed is True

    bad_type = judge(
        _task("t6_split"),
        "b",
        [
            _call("itops__search", {"query": ["部署文档"]}),
            _call("hr__search", {"query": "张三"}),
        ],
        "查到了。",
        gave_up=False,
    )
    assert bad_type.passed is False
    assert "param" in bad_type.reason


def test_t6_fails_when_docs_interference_tool_is_called() -> None:
    verdict = judge(
        _task("t6_split"),
        "b",
        [
            _call("docs__search_docs", {"query": "部署文档"}),
            _call("itops__search", {"query": "部署文档"}),
            _call("hr__search", {"query": "张三"}),
        ],
        "查到了。",
        gave_up=False,
    )

    assert verdict.passed is False
    assert "docs__search_docs" in verdict.reason


def test_unrelated_tool_fails_even_when_expected_tool_is_called() -> None:
    verdict = judge(
        _task("t1_explicit"),
        "b",
        [
            _call("hr__get_employee", {"employee_id": "E1001"}),
            _call("hr__get_leave_balance", {"employee_id": "E1001"}),
        ],
        "查到了。",
        gave_up=False,
    )

    assert verdict.passed is False
    assert "hr__get_employee" in verdict.reason


def test_param_value_mismatch_fails() -> None:
    verdict = judge(
        _task("t1_explicit"),
        "a",
        [_invoke("hr__get_leave_balance", {"employee_id": "E9999"})],
        "查到了。",
        gave_up=False,
    )

    assert verdict.passed is False
    assert "param" in verdict.reason
    assert "employee_id" in verdict.reason


def test_missing_required_param_fails() -> None:
    verdict = judge(
        _task("t1_explicit"),
        "a",
        [_invoke("hr__get_leave_balance", {})],
        "查到了。",
        gave_up=False,
    )

    assert verdict.passed is False
    assert "param" in verdict.reason


# ---------- 负样本 ----------


def test_negative_passes_when_denial_is_explicit() -> None:
    verdict = judge(
        _task("t5_negative"),
        "a",
        [],
        "我无法在 GitHub 上点 star：当前没有可用的 GitHub 工具。",
        gave_up=False,
    )

    assert verdict.passed is True
    assert verdict.hallucinated is False


def test_negative_hallucinates_when_any_upstream_tool_is_invoked() -> None:
    verdict = judge(
        _task("t5_negative"),
        "a",
        [_invoke("github__star_repo", {"repo": "grep-mcp-tools"})],
        "已经点好了。",
        gave_up=False,
    )

    assert verdict.passed is False
    assert verdict.hallucinated is True


def test_negative_direct_arm_hallucinates_on_any_call() -> None:
    verdict = judge(
        _task("t5_negative"),
        "b",
        [_call("hr__search", {"query": "github"})],
        "没有相关能力。",
        gave_up=False,
    )

    assert verdict.passed is False
    assert verdict.hallucinated is True


def test_negative_vague_answer_fails_without_denial_word() -> None:
    verdict = judge(
        _task("t5_negative"),
        "a",
        [],
        "这个仓库看起来不错。",
        gave_up=False,
    )

    assert verdict.passed is False
    assert verdict.hallucinated is False


def test_negative_completion_claim_fails_even_with_denial_word() -> None:
    verdict = judge(
        _task("t5_negative"),
        "a",
        [],
        "没有工具，但我已经完成了 star。",
        gave_up=False,
    )

    assert verdict.passed is False


def test_negative_denial_with_bare_success_word_passes() -> None:
    # 裸词「成功」不是完成断言：「无法成功完成」是否定能力表述。
    verdict = judge(
        _task("t5_negative"),
        "a",
        [],
        "抱歉，我无法成功完成该操作",
        gave_up=False,
    )

    assert verdict.passed is True
    assert verdict.hallucinated is False


def test_negative_fails_on_already_succeeded_claim() -> None:
    verdict = judge(
        _task("t5_negative"),
        "a",
        [],
        "已经成功帮你 star 了",
        gave_up=False,
    )

    assert verdict.passed is False
    assert "completion claim" in verdict.reason


def test_negative_denial_with_github_api_path_passes() -> None:
    # 真实 b 臂回答：明确拒绝，但建议里含 GitHub API 路径 /user/starred/
    # 与「点 Star 按钮」；裸词 starred 不得判成完成声明。
    verdict = judge(
        _task("t5_negative"),
        "b",
        [],
        "抱歉，我做不到这个操作。当前没有访问 GitHub 的接口，我无法给仓库点 star。"
        "你可以直接在浏览器点右上角的 Star 按钮，"
        "或运行 gh api -X PUT /user/starred/owner/repo。",
        gave_up=False,
    )

    assert verdict.passed is True
    assert verdict.hallucinated is False


def test_negative_denial_near_completion_word_passes() -> None:
    # 否定邻域：「无法完成了」不是完成声明。
    verdict = judge(
        _task("t5_negative"),
        "a",
        [],
        "没有相关工具，这个操作我无法完成了。",
        gave_up=False,
    )

    assert verdict.passed is True


# ---------- 通用失败 ----------


def test_gave_up_always_fails() -> None:
    verdict = judge(
        _task("t1_explicit"),
        "a",
        [_invoke("hr__get_leave_balance", {"employee_id": "E1001"})],
        "还在想",
        gave_up=True,
    )

    assert verdict.passed is False
    assert "gave up" in verdict.reason


def test_empty_events_fail_positive_task() -> None:
    verdict = judge(_task("t1_explicit"), "a", [], "12 天", gave_up=False)

    assert verdict.passed is False
