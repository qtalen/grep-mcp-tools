"""指标派生单测：逐项覆盖正常与边界（无样本 / 分母为 0）。"""

from __future__ import annotations

from tests.agent.metrics import derive_metrics
from tests.agent.tasks import TASKS
from tests.agent.transcript import Transcript, make_run_id

_DEFAULT = object()


def _verdict(passed: bool, reason: str = "ok", hallucinated: bool = False) -> dict:
    return {"passed": passed, "reason": reason, "hallucinated": hallucinated}


def _call(name: str, result: str = "", **arguments: object) -> dict:
    return {"name": name, "arguments": dict(arguments), "result": result}


def _transcript(
    arm: str,
    task_id: str,
    rep: int = 1,
    tool_calls: list[dict] | None = None,
    usages: list[dict] | None = None,
    verdict: object = _DEFAULT,
    gave_up: bool = False,
) -> Transcript:
    return Transcript(
        run_id=make_run_id(arm, task_id, rep),
        arm=arm,
        task_id=task_id,
        rep=rep,
        messages=[],
        tool_calls=tool_calls or [],
        usages=usages or [],
        final_text="",
        gave_up=gave_up,
        verdict=_verdict(True) if verdict is _DEFAULT else verdict,
        duration_s=0.1,
    )


def test_per_task_counts_and_pairing_difference() -> None:
    transcripts = [
        _transcript("a", "t1_explicit", 1, verdict=_verdict(True)),
        _transcript("a", "t1_explicit", 2, verdict=_verdict(False)),
        _transcript("b", "t1_explicit", 1, verdict=_verdict(True)),
        _transcript("b", "t1_explicit", 2, verdict=_verdict(True)),
        _transcript("c", "t1_explicit", 1, verdict=_verdict(False)),
    ]

    metrics = derive_metrics(transcripts, TASKS)

    assert metrics["per_task"]["t1_explicit"] == {"a": 1, "b": 2, "c": 0, "d": 0}
    assert metrics["pairing"]["t1_explicit"] == -1
    # 没有样本的任务计数为 0。
    assert metrics["per_task"]["t6_split"] == {"a": 0, "b": 0, "c": 0, "d": 0}


def test_self_discovery_uses_arm_a_non_negative_samples_only() -> None:
    transcripts = [
        # 首次调用 search_mcp_tool → 记成功。
        _transcript(
            "a",
            "t1_explicit",
            1,
            tool_calls=[_call("search_mcp_tool"), _call("invoke_this_tool")],
        ),
        # 先 invoke → 记失败。
        _transcript("a", "t1_explicit", 2, tool_calls=[_call("invoke_this_tool")]),
        # 空调用 → 记失败。
        _transcript("a", "t2_vocab", 1),
        # 负样本不进分母。
        _transcript("a", "t5_negative", 1, tool_calls=[_call("search_mcp_tool")]),
    ]

    metrics = derive_metrics(transcripts, TASKS)

    assert metrics["self_discovery"] == {
        "passed": 1,
        "denominator": 3,
        "rate": 1 / 3,
    }


def test_self_discovery_full_suite_denominator_is_25() -> None:
    positive_tasks = [task.id for task in TASKS if not task.negative]
    transcripts = [
        _transcript("a", task_id, rep, tool_calls=[_call("search_mcp_tool")])
        for task_id in positive_tasks
        for rep in range(1, 6)
    ]
    transcripts.append(
        _transcript("a", "t5_negative", 1, tool_calls=[_call("search_mcp_tool")])
    )

    metrics = derive_metrics(transcripts, TASKS)

    assert positive_tasks == [
        "t1_explicit",
        "t2_vocab",
        "t3_over",
        "t4_chain",
        "t6_split",
    ]
    assert metrics["self_discovery"] == {
        "passed": 25,
        "denominator": 25,
        "rate": 1.0,
    }


def test_hallucination_rate_per_arm_on_negative_task() -> None:
    transcripts = [
        _transcript(
            "a",
            "t5_negative",
            1,
            verdict=_verdict(False, "invoked", hallucinated=True),
        ),
        _transcript(
            "a",
            "t5_negative",
            2,
            verdict=_verdict(True, "denied"),
        ),
        _transcript("b", "t5_negative", 1, verdict=_verdict(True, "denied")),
    ]

    metrics = derive_metrics(transcripts, TASKS)

    assert metrics["hallucination_rate"]["a"] == 0.5
    assert metrics["hallucination_rate"]["b"] == 0.0
    assert metrics["hallucination_rate"]["c"] == 0.0


def test_searches_per_task_is_arm_a_average() -> None:
    transcripts = [
        _transcript(
            "a",
            "t1_explicit",
            1,
            tool_calls=[_call("search_mcp_tool"), _call("search_mcp_tool")],
        ),
        _transcript("a", "t1_explicit", 2, tool_calls=[_call("invoke_this_tool")]),
        _transcript("b", "t1_explicit", 1, tool_calls=[_call("search_mcp_tool")]),
    ]

    metrics = derive_metrics(transcripts, TASKS)

    assert metrics["searches_per_task"]["t1_explicit"] == 1.0
    assert metrics["searches_per_task"]["t2_vocab"] == 0.0


def test_zero_hit_recovery_counts_follow_up_searches() -> None:
    no_hit = "No tools matched [zzz].\n\nAvailable services:\n- hr: 8 tools"
    transcripts = [
        _transcript(
            "a",
            "t2_vocab",
            1,
            tool_calls=[
                _call("search_mcp_tool", no_hit),
                _call("search_mcp_tool", "1 tool matched [audit]"),
            ],
        ),
        _transcript(
            "a",
            "t2_vocab",
            2,
            tool_calls=[
                _call("search_mcp_tool", no_hit),
                _call("invoke_this_tool"),
            ],
        ),
        # 命中过的搜索不算 zero-hit 机会。
        _transcript(
            "a",
            "t1_explicit",
            1,
            tool_calls=[_call("search_mcp_tool", "1 tool matched [hr]")],
        ),
    ]

    metrics = derive_metrics(transcripts, TASKS)

    assert metrics["zero_hit_recovery"] == {"opportunities": 2, "recovered": 1}


def test_invoke_accuracy_copies_targets_and_counts_param_errors() -> None:
    search_result = "hr__get_leave_balance - Query the remaining annual leave balance"
    transcripts = [
        _transcript(
            "a",
            "t1_explicit",
            1,
            tool_calls=[
                _call("search_mcp_tool", search_result),
                _call("invoke_this_tool", "", target="hr__get_leave_balance"),
            ],
        ),
        _transcript(
            "a",
            "t1_explicit",
            2,
            tool_calls=[
                _call("search_mcp_tool", search_result),
                _call("invoke_this_tool", "", target="github__star_repo"),
            ],
            verdict=_verdict(False, "param error: employee_id mismatch"),
        ),
        _transcript(
            "a",
            "t1_explicit",
            3,
            tool_calls=[_call("invoke_this_tool", "", target="hr__get_leave_balance")],
            verdict=_verdict(False, "unrelated upstream tool(s) called"),
        ),
    ]

    metrics = derive_metrics(transcripts, TASKS)

    assert metrics["invoke_accuracy"] == {
        "invokes": 3,
        "copied_from_results": 1,
        "param_errors": 1,
    }


def test_status_calls_give_up_rate_and_tokens() -> None:
    transcripts = [
        _transcript(
            "a",
            "t1_explicit",
            1,
            tool_calls=[_call("check_mcp_status"), _call("check_mcp_status")],
            usages=[
                {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
                {"prompt_tokens": 150, "completion_tokens": 5, "total_tokens": 155},
            ],
        ),
        _transcript("a", "t1_explicit", 2, gave_up=True),
        _transcript(
            "b",
            "t1_explicit",
            1,
            usages=[
                {"prompt_tokens": 1000, "completion_tokens": 20, "total_tokens": 1020}
            ],
        ),
        _transcript("b", "t2_vocab", 1, gave_up=True),
        _transcript("b", "t2_vocab", 2),
    ]

    metrics = derive_metrics(transcripts, TASKS)

    assert metrics["status_calls"] == {"a": 2, "b": 0, "c": 0, "d": 0}
    assert metrics["give_up_rate"]["a"] == 0.5
    assert metrics["give_up_rate"]["b"] == 1 / 3
    assert metrics["tokens"]["a"]["t1_explicit"] == {
        "prompt": 250,
        "completion": 15,
        "total": 265,
    }
    assert metrics["tokens"]["b"]["t1_explicit"] == {
        "prompt": 1000,
        "completion": 20,
        "total": 1020,
    }
    assert metrics["tokens"]["b"]["t2_vocab"] == {
        "prompt": 0,
        "completion": 0,
        "total": 0,
    }


def test_ablation_section_reports_a_c_d() -> None:
    transcripts = [
        _transcript("a", "t1_explicit", 1, tool_calls=[_call("search_mcp_tool")]),
        _transcript("b", "t1_explicit", 1),
        _transcript("c", "t1_explicit", 1, tool_calls=[_call("invoke_this_tool")]),
        _transcript("c", "t1_explicit", 2, tool_calls=[_call("search_mcp_tool")]),
        _transcript("d", "t1_explicit", 1, verdict=_verdict(False)),
    ]

    metrics = derive_metrics(transcripts, TASKS)

    assert metrics["ablation"]["passed"] == {"a": 1, "c": 2, "d": 0}
    assert metrics["ablation"]["self_discovery"]["a"] == {
        "passed": 1,
        "denominator": 1,
        "rate": 1.0,
    }
    assert metrics["ablation"]["self_discovery"]["c"] == {
        "passed": 1,
        "denominator": 2,
        "rate": 0.5,
    }
    assert metrics["ablation"]["self_discovery"]["d"] == {
        "passed": 0,
        "denominator": 1,
        "rate": 0.0,
    }


def test_empty_transcripts_yield_zero_metrics_without_division_errors() -> None:
    metrics = derive_metrics([], TASKS)

    assert metrics["per_task"]["t1_explicit"] == {
        "a": 0,
        "b": 0,
        "c": 0,
        "d": 0,
    }
    assert metrics["pairing"]["t1_explicit"] == 0
    assert metrics["self_discovery"] == {
        "passed": 0,
        "denominator": 0,
        "rate": 0.0,
    }
    assert metrics["hallucination_rate"] == {"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0}
    assert metrics["searches_per_task"]["t1_explicit"] == 0.0
    assert metrics["zero_hit_recovery"] == {"opportunities": 0, "recovered": 0}
    assert metrics["invoke_accuracy"] == {
        "invokes": 0,
        "copied_from_results": 0,
        "param_errors": 0,
    }
    assert metrics["status_calls"] == {"a": 0, "b": 0, "c": 0, "d": 0}
    assert metrics["give_up_rate"] == {"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0}
    assert metrics["tokens"]["a"]["t1_explicit"] == {
        "prompt": 0,
        "completion": 0,
        "total": 0,
    }
    assert metrics["ablation"]["passed"] == {"a": 0, "c": 0, "d": 0}


def test_transcript_without_verdict_is_not_counted_as_pass() -> None:
    transcripts = [_transcript("a", "t1_explicit", 1, verdict=None)]

    metrics = derive_metrics(transcripts, TASKS)

    assert metrics["per_task"]["t1_explicit"]["a"] == 0
