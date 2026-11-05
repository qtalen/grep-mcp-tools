"""实验报告：A/B 主表、消融对照表与 M2 判据（transcript 的纯函数）。

报告结构可由 `build_report` 离线重算，`render_markdown` 只做展示，不藏判据。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tests.agent.metrics import ARMS, PROXY_ARMS, derive_metrics
from tests.agent.tasks import TASKS, TaskSpec
from tests.agent.transcript import Transcript

SELF_DISCOVERY_THRESHOLD = 0.8
RESIDENT_REDUCTION_THRESHOLD = 0.8
PAIRING_BUDGET = -3


def _passed(transcript: Transcript) -> bool:
    verdict = transcript.verdict
    if verdict is None:
        return False
    if isinstance(verdict, dict):
        return bool(verdict.get("passed", False))
    return bool(getattr(verdict, "passed", False))


def _arm_totals(
    transcripts: Sequence[Transcript], task_ids: Sequence[str], arms: Sequence[str]
) -> tuple[dict[str, dict[str, dict[str, int]]], dict[str, dict[str, int]]]:
    """逐任务 / 逐臂的 passed/total 计数，以及各臂总计。"""
    per_task: dict[str, dict[str, dict[str, int]]] = {
        task_id: {arm: {"passed": 0, "total": 0} for arm in arms}
        for task_id in task_ids
    }
    totals: dict[str, dict[str, int]] = {
        arm: {"passed": 0, "total": 0} for arm in arms
    }
    for transcript in transcripts:
        bucket = per_task.get(transcript.task_id)
        if bucket is None or transcript.arm not in bucket:
            continue
        bucket[transcript.arm]["total"] += 1
        totals[transcript.arm]["total"] += 1
        if _passed(transcript):
            bucket[transcript.arm]["passed"] += 1
            totals[transcript.arm]["passed"] += 1
    return per_task, totals


def _resident_reduction(resident: dict | None) -> float | None:
    """常驻缩减口径：优先显式值，其次 a/b 两臂工具 token 差。"""
    if not resident:
        return None
    if "reduction" in resident and resident["reduction"] is not None:
        return float(resident["reduction"])
    proxy = resident.get("a")
    direct = resident.get("b")
    if isinstance(proxy, dict) and isinstance(direct, dict):
        direct_tokens = int(direct.get("tools_prompt_tokens", 0) or 0)
        if direct_tokens > 0:
            proxy_tokens = int(proxy.get("tools_prompt_tokens", 0) or 0)
            return 1.0 - proxy_tokens / direct_tokens
    return None


def build_report(
    transcripts: Sequence[Transcript],
    tasks: Sequence[TaskSpec] = TASKS,
    resident: dict | None = None,
) -> dict:
    """构建完整报告数据（数值 + 判据布尔值）。"""
    metrics = derive_metrics(transcripts, tasks)
    task_ids = [task.id for task in tasks]

    per_task, totals = _arm_totals(transcripts, task_ids, ARMS)
    pairing = {
        task_id: per_task[task_id]["a"]["passed"] - per_task[task_id]["b"]["passed"]
        for task_id in task_ids
    }

    ablation_per_task, ablation_totals = _arm_totals(transcripts, task_ids, PROXY_ARMS)

    proxy_pass = totals["a"]["passed"]
    direct_pass = totals["b"]["passed"]
    delta = proxy_pass - direct_pass

    reduction = _resident_reduction(resident)
    self_discovery = metrics["self_discovery"]

    criteria: dict[str, Any] = {
        "proxy_vs_direct": {
            "proxy_pass": proxy_pass,
            "direct_pass": direct_pass,
            "delta": delta,
            "threshold": PAIRING_BUDGET,
            "passed": delta >= PAIRING_BUDGET,
        },
        "self_discovery": {
            "rate": self_discovery["rate"],
            "threshold": SELF_DISCOVERY_THRESHOLD,
            "passed": self_discovery["rate"] >= SELF_DISCOVERY_THRESHOLD,
        },
        "resident_reduction": {
            "reduction": reduction,
            "threshold": RESIDENT_REDUCTION_THRESHOLD,
            "passed": (
                None if reduction is None else reduction >= RESIDENT_REDUCTION_THRESHOLD
            ),
        },
    }
    passed_values = [criterion["passed"] for criterion in criteria.values()]
    criteria["all_passed"] = all(
        value is True for value in passed_values if value is not None
    )
    criteria["complete"] = all(value is not None for value in passed_values)

    return {
        "tasks": task_ids,
        "per_task": per_task,
        "pairing": pairing,
        "totals": totals,
        "self_discovery": self_discovery,
        "hallucination_rate": metrics["hallucination_rate"],
        "diagnostics": {
            "searches_per_task": metrics["searches_per_task"],
            "zero_hit_recovery": metrics["zero_hit_recovery"],
            "invoke_accuracy": metrics["invoke_accuracy"],
            "status_calls": metrics["status_calls"],
            "give_up_rate": metrics["give_up_rate"],
        },
        "tokens": metrics["tokens"],
        "resident": resident,
        "ablation": {
            "per_task": ablation_per_task,
            "totals": ablation_totals,
            "self_discovery": metrics["ablation"]["self_discovery"],
        },
        "criteria": criteria,
    }


def _ratio(count: dict[str, int]) -> str:
    return f"{count['passed']}/{count['total']}"


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _mark(value: bool | None) -> str:
    if value is None:
        return "未测（n/a）"
    return "✅" if value else "❌"


def _arm_list(
    values: dict[str, Any], formatter, arms: Sequence[str] = ARMS
) -> str:
    return " / ".join(f"{arm} {formatter(values.get(arm))}" for arm in arms)


def render_markdown(report: dict) -> str:
    """把报告数据渲染成两张 Markdown 表 + 判据清单。"""
    arms = ARMS
    lines: list[str] = ["# B04 agent harness 报告", ""]

    # ---- A/B 主表 ----
    lines.append("## A/B 主表")
    lines.append("")
    lines.append("| 任务 | " + " | ".join(arms) + " | a-b |")
    lines.append("|---|" + "---|" * (len(arms) + 1))
    for task_id in report["tasks"]:
        row = [
            _ratio(report["per_task"][task_id][arm])
            for arm in arms
        ]
        lines.append(
            f"| {task_id} | " + " | ".join(row) + f" | {report['pairing'][task_id]:+d} |"
        )
    lines.append("")

    self_discovery = report["self_discovery"]
    lines.append(
        "- 自发现率（臂 a，排除负样本）："
        f"{_percent(self_discovery['rate'])}"
        f"（{self_discovery['passed']}/{self_discovery['denominator']}）"
    )
    lines.append(
        "- 幻觉率（负样本）："
        + _arm_list(report["hallucination_rate"], _percent)
    )
    diagnostics = report["diagnostics"]
    lines.append(
        "- 搜索次数/任务（臂 a）："
        + ", ".join(
            f"{task_id} {value:.1f}"
            for task_id, value in diagnostics["searches_per_task"].items()
        )
    )
    recovery = diagnostics["zero_hit_recovery"]
    lines.append(
        f"- 零命中恢复（臂 a）：{recovery['recovered']}/{recovery['opportunities']}"
    )
    accuracy = diagnostics["invoke_accuracy"]
    lines.append(
        "- invoke 正确率（臂 a）："
        f"target 原样复制 {accuracy['copied_from_results']}/{accuracy['invokes']}，"
        f"参数错误 {accuracy['param_errors']}"
    )
    lines.append(
        "- check_mcp_status 调用次数："
        + _arm_list(diagnostics["status_calls"], str)
    )
    lines.append("- 放弃率：" + _arm_list(diagnostics["give_up_rate"], _percent))
    tokens = report["tokens"]
    for arm in arms:
        totals = ", ".join(
            f"{task_id} {values['prompt']}/{values['completion']}/{values['total']}"
            for task_id, values in tokens.get(arm, {}).items()
        )
        lines.append(f"- token（{arm}：prompt/completion/total）：{totals}")

    resident = report["resident"]
    if resident is None:
        lines.append("- 常驻对比：未测（n/a）")
    else:
        for arm in arms:
            measurement = resident.get(arm)
            if not isinstance(measurement, dict):
                continue
            lines.append(
                f"- 常驻对比（臂 {arm}）："
                f"有工具 {measurement.get('with_tools')} / "
                f"无工具 {measurement.get('without_tools')} tokens；"
                f"差值 {measurement.get('tools_prompt_tokens')} tokens；"
                f"序列化 {measurement.get('tools_chars')} chars"
            )
    lines.append("")

    # ---- 消融对照表 ----
    ablation = report["ablation"]
    lines.append("## 消融对照表")
    lines.append("")
    lines.append("| 任务 | " + " | ".join(PROXY_ARMS) + " |")
    lines.append("|---|" + "---|" * len(PROXY_ARMS))
    for task_id in report["tasks"]:
        row = [
            _ratio(ablation["per_task"][task_id][arm]) for arm in PROXY_ARMS
        ]
        lines.append(f"| {task_id} | " + " | ".join(row) + " |")
    lines.append("")
    lines.append(
        "- 通过总数："
        + _arm_list(ablation["totals"], _ratio, PROXY_ARMS)
    )
    lines.append(
        "- 自发现率："
        + _arm_list(
            ablation["self_discovery"],
            lambda value: _percent(value["rate"]),
            PROXY_ARMS,
        )
    )
    lines.append("")

    # ---- M2 判据 ----
    criteria = report["criteria"]
    pairing = criteria["proxy_vs_direct"]
    discovery = criteria["self_discovery"]
    resident_criterion = criteria["resident_reduction"]
    lines.append("## M2 判据")
    lines.append("")
    lines.append(
        "- 代理通过总数 ≥ 直挂 − 3："
        f"{pairing['proxy_pass']} ≥ {pairing['direct_pass']} − 3"
        f"（差 {pairing['delta']:+d}）"
        f"{_mark(pairing['passed'])}"
    )
    lines.append(
        "- 自主发现率 ≥ 80%："
        f"{_percent(discovery['rate'])}{_mark(discovery['passed'])}"
    )
    if resident_criterion["reduction"] is None:
        resident_text = "未测（n/a）"
    else:
        resident_text = _percent(resident_criterion["reduction"]) + _mark(
            resident_criterion["passed"]
        )
    lines.append(f"- 常驻上下文缩减 ≥ 80%：{resident_text}")
    lines.append(f"- 判据完整：{_mark(criteria['complete'])}")
    lines.append(f"- 全部通过：{_mark(criteria['all_passed'])}")
    lines.append("")
    return "\n".join(lines)
