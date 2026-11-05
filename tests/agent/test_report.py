"""报告构建单测：数值、配对差异、M2 判据与 Markdown 渲染。"""

from __future__ import annotations

import pytest

from tests.agent.report import build_report, render_markdown
from tests.agent.tasks import TASKS
from tests.agent.transcript import Transcript, make_run_id


def _verdict(passed: bool, hallucinated: bool = False, reason: str = "ok") -> dict:
    return {"passed": passed, "reason": reason, "hallucinated": hallucinated}


def _transcript(
    arm: str,
    task_id: str,
    rep: int = 1,
    passed: bool = True,
    hallucinated: bool = False,
    tool_calls: list[dict] | None = None,
    usages: list[dict] | None = None,
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
        gave_up=False,
        verdict=_verdict(passed, hallucinated),
        duration_s=0.1,
    )


def _search_first() -> list[dict]:
    return [
        {"name": "search_mcp_tool", "arguments": {"keywords": ["x"]}, "result": ""},
        {
            "name": "invoke_this_tool",
            "arguments": {"target": "hr__get_leave_balance", "params": {}},
            "result": "",
        },
    ]


def _sample_transcripts() -> list[Transcript]:
    return [
        _transcript("a", "t1_explicit", 1, tool_calls=_search_first()),
        _transcript("a", "t1_explicit", 2, passed=False),
        _transcript("b", "t1_explicit", 1),
        _transcript("b", "t1_explicit", 2),
        _transcript("c", "t1_explicit", 1),
        _transcript("d", "t1_explicit", 1, passed=False),
        _transcript(
            "a", "t5_negative", 1, hallucinated=False, tool_calls=_search_first()
        ),
        _transcript("a", "t5_negative", 2, passed=False, hallucinated=True),
    ]


def test_build_report_per_task_totals_and_pairing() -> None:
    report = build_report(_sample_transcripts(), TASKS)

    assert report["tasks"] == [task.id for task in TASKS]
    assert report["per_task"]["t1_explicit"] == {
        "a": {"passed": 1, "total": 2},
        "b": {"passed": 2, "total": 2},
        "c": {"passed": 1, "total": 1},
        "d": {"passed": 0, "total": 1},
    }
    assert report["pairing"]["t1_explicit"] == -1
    assert report["totals"]["a"] == {"passed": 2, "total": 4}
    assert report["totals"]["b"] == {"passed": 2, "total": 2}
    assert report["per_task"]["t6_split"]["a"] == {"passed": 0, "total": 0}


def test_build_report_diagnostics_and_tokens_present() -> None:
    transcripts = [
        _transcript(
            "a",
            "t1_explicit",
            1,
            tool_calls=_search_first(),
            usages=[
                {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
            ],
        )
    ]

    report = build_report(transcripts, TASKS)

    assert report["self_discovery"] == {"passed": 1, "denominator": 1, "rate": 1.0}
    assert report["hallucination_rate"]["a"] == 0.0
    assert report["diagnostics"]["invoke_accuracy"]["invokes"] == 1
    assert report["diagnostics"]["status_calls"]["a"] == 0
    assert report["tokens"]["a"]["t1_explicit"] == {
        "prompt": 100,
        "completion": 10,
        "total": 110,
    }
    assert report["resident"] is None


def test_ablation_table_covers_a_c_d_only() -> None:
    report = build_report(_sample_transcripts(), TASKS)

    ablation = report["ablation"]
    assert set(ablation["per_task"]["t1_explicit"]) == {"a", "c", "d"}
    assert ablation["per_task"]["t1_explicit"]["c"] == {"passed": 1, "total": 1}
    assert ablation["totals"] == {
        "a": {"passed": 2, "total": 4},
        "c": {"passed": 1, "total": 1},
        "d": {"passed": 0, "total": 1},
    }
    assert ablation["self_discovery"]["c"] == {
        "passed": 0,
        "denominator": 1,
        "rate": 0.0,
    }


def test_criteria_pass_at_boundaries() -> None:
    # 臂 a 3 个样本全过（3 个任务），臂 b 6 个样本全过 → 差 -3，>= -3 通过。
    transcripts = [
        _transcript("a", task_id, 1, tool_calls=_search_first())
        for task_id in ("t1_explicit", "t2_vocab", "t3_over")
    ]
    for task_id in ("t1_explicit", "t2_vocab", "t3_over"):
        for rep in (1, 2):
            transcripts.append(_transcript("b", task_id, rep))

    report = build_report(
        transcripts,
        TASKS,
        resident={
            "a": {
                "with_tools": 200,
                "without_tools": 100,
                "tools_prompt_tokens": 100,
                "tools_chars": 1000,
            },
            "b": {
                "with_tools": 1100,
                "without_tools": 100,
                "tools_prompt_tokens": 1000,
                "tools_chars": 40000,
            },
        },
    )

    criteria = report["criteria"]
    assert criteria["proxy_vs_direct"] == {
        "proxy_pass": 3,
        "direct_pass": 6,
        "delta": -3,
        "threshold": -3,
        "passed": True,
    }
    assert criteria["self_discovery"] == {
        "rate": 1.0,
        "threshold": 0.8,
        "passed": True,
    }
    assert criteria["resident_reduction"] == {
        "reduction": 0.9,
        "threshold": 0.8,
        "passed": True,
    }
    assert criteria["complete"] is True
    assert criteria["all_passed"] is True


def test_criteria_fail_when_proxy_gap_exceeds_budget() -> None:
    transcripts = [_transcript("a", "t1_explicit", 1, passed=False)]
    for rep in (1, 2, 3, 4):
        transcripts.append(_transcript("b", "t1_explicit", rep))

    report = build_report(transcripts, TASKS)

    criteria = report["criteria"]
    assert criteria["proxy_vs_direct"]["delta"] == -4
    assert criteria["proxy_vs_direct"]["passed"] is False
    assert criteria["self_discovery"]["passed"] is False
    assert criteria["resident_reduction"]["passed"] is None
    assert criteria["complete"] is False
    assert criteria["all_passed"] is False


def test_render_markdown_has_two_tables_and_criteria() -> None:
    report = build_report(_sample_transcripts(), TASKS)

    markdown = render_markdown(report)

    assert "A/B" in markdown
    assert "消融" in markdown
    assert "M2" in markdown
    assert "t1_explicit" in markdown
    assert "1/2" in markdown
    assert "自发现率" in markdown
    assert "幻觉率" in markdown
    assert "常驻" in markdown
    assert "n/a" in markdown
    # 两张表：至少 4 行表头 + 分隔行。
    assert sum(1 for line in markdown.splitlines() if line.startswith("|")) >= 6


def test_render_markdown_with_resident_reports_reduction() -> None:
    report = build_report(
        _sample_transcripts(),
        TASKS,
        resident={
            "a": {
                "with_tools": 200,
                "without_tools": 100,
                "tools_prompt_tokens": 100,
                "tools_chars": 1000,
            },
            "b": {
                "with_tools": 1100,
                "without_tools": 100,
                "tools_prompt_tokens": 1000,
                "tools_chars": 4321,
            },
        },
    )

    markdown = render_markdown(report)

    assert "90.0%" in markdown
    assert "4321" in markdown
    assert "n/a" not in markdown


def test_resident_reduction_uses_proxy_vs_direct_measurements() -> None:
    report = build_report(
        _sample_transcripts(),
        TASKS,
        resident={
            "a": {
                "with_tools": 300,
                "without_tools": 240,
                "tools_prompt_tokens": 60,
                "tools_chars": 1000,
            },
            "b": {
                "with_tools": 1440,
                "without_tools": 240,
                "tools_prompt_tokens": 1200,
                "tools_chars": 40000,
            },
        },
    )

    reduction = report["criteria"]["resident_reduction"]
    assert reduction["reduction"] == pytest.approx(0.95)
    assert reduction["passed"] is True

    markdown = render_markdown(report)
    assert "臂 a" in markdown
    assert "臂 b" in markdown
    assert "40000" in markdown


def test_missing_resident_marks_incomplete_but_keeps_known_criteria() -> None:
    transcripts = [
        _transcript("a", "t1_explicit", 1, tool_calls=_search_first()),
        _transcript("b", "t1_explicit", 1),
    ]
    report = build_report(transcripts, TASKS)

    criteria = report["criteria"]
    assert criteria["resident_reduction"]["passed"] is None
    assert criteria["complete"] is False
    # 已知判据（代理差额、自发现率）都通过，all_passed 不被缺失判据拉成 False。
    assert criteria["proxy_vs_direct"]["passed"] is True
    assert criteria["self_discovery"]["passed"] is True
    assert criteria["all_passed"] is True


def test_complete_resident_marks_criteria_complete() -> None:
    transcripts = [
        _transcript("a", "t1_explicit", 1, tool_calls=_search_first()),
        _transcript("b", "t1_explicit", 1),
    ]
    report = build_report(
        transcripts,
        TASKS,
        resident={
            "a": {"tools_prompt_tokens": 100},
            "b": {"tools_prompt_tokens": 1000},
        },
    )

    criteria = report["criteria"]
    assert criteria["resident_reduction"]["passed"] is True
    assert criteria["complete"] is True
    assert criteria["all_passed"] is True


def test_render_markdown_shows_na_for_untested_criterion() -> None:
    markdown = render_markdown(build_report(_sample_transcripts(), TASKS))

    assert "未测（n/a）" in markdown
