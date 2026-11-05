"""从 transcript 派生实验指标（纯函数，可离线重算）。

指标口径见 `docs/design/batches/B04-agent-harness.md`『指标与统计』：
- 自主发现率：臂 a 每个样本首次工具调用是否为 `search_mcp_tool`；分母排除负样本。
- 幻觉率：负样本中 `verdict.hallucinated` 的比例（按臂）。
- 零命中恢复：臂 a 某次 `search_mcp_tool` 结果含 `No tools matched` 后是否还有搜索。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tests.agent.tasks import TASKS, TaskSpec
from tests.agent.transcript import Transcript

ARMS: tuple[str, ...] = ("a", "b", "c", "d")
PROXY_ARMS: tuple[str, ...] = ("a", "c", "d")
SEARCH = "search_mcp_tool"
INVOKE = "invoke_this_tool"
STATUS = "check_mcp_status"
NO_HIT_MARKER = "No tools matched"


def _verdict_field(transcript: Transcript, key: str, default: Any) -> Any:
    verdict = transcript.verdict
    if verdict is None:
        return default
    if isinstance(verdict, dict):
        return verdict.get(key, default)
    return getattr(verdict, key, default)


def _passed(transcript: Transcript) -> bool:
    return bool(_verdict_field(transcript, "passed", False))


def _tool_name(call: dict) -> str:
    return str(call.get("name") or "")


def _transcripts_for(
    transcripts: Sequence[Transcript], arm: str, task_id: str | None = None
) -> list[Transcript]:
    return [
        transcript
        for transcript in transcripts
        if transcript.arm == arm
        and (task_id is None or transcript.task_id == task_id)
    ]


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _self_discovery(
    transcripts: Sequence[Transcript], arms: Sequence[str], tasks: Sequence[TaskSpec]
) -> dict[str, dict[str, Any]]:
    """各臂的自主发现率（分母排除负样本任务）。"""
    negative_ids = {task.id for task in tasks if task.negative}
    result: dict[str, dict[str, Any]] = {}
    for arm in arms:
        samples = [
            transcript
            for transcript in transcripts
            if transcript.arm == arm and transcript.task_id not in negative_ids
        ]
        passed = sum(
            1
            for transcript in samples
            if transcript.tool_calls
            and _tool_name(transcript.tool_calls[0]) == SEARCH
        )
        result[arm] = {
            "passed": passed,
            "denominator": len(samples),
            "rate": _rate(passed, len(samples)),
        }
    return result


def derive_metrics(
    transcripts: Sequence[Transcript], tasks: Sequence[TaskSpec] = TASKS
) -> dict:
    """把一批 transcript 聚合成报告所需的全部指标。"""
    task_ids = [task.id for task in tasks]
    negative_ids = {task.id for task in tasks if task.negative}

    # ---- 逐任务 / 逐臂通过数 ----
    per_task: dict[str, dict[str, int]] = {
        task_id: {arm: 0 for arm in ARMS} for task_id in task_ids
    }
    for transcript in transcripts:
        bucket = per_task.setdefault(
            transcript.task_id, {arm: 0 for arm in ARMS}
        )
        if _passed(transcript):
            bucket[transcript.arm] = bucket.get(transcript.arm, 0) + 1

    pairing = {
        task_id: per_task[task_id].get("a", 0) - per_task[task_id].get("b", 0)
        for task_id in task_ids
    }

    # ---- 幻觉率（负样本）----
    hallucination_rate: dict[str, float] = {}
    for arm in ARMS:
        samples = [
            transcript
            for transcript in _transcripts_for(transcripts, arm)
            if transcript.task_id in negative_ids
        ]
        hallucinated = sum(
            1 for transcript in samples if _verdict_field(transcript, "hallucinated", False)
        )
        hallucination_rate[arm] = _rate(hallucinated, len(samples))

    # ---- 每任务搜索次数（臂 a）----
    searches_per_task: dict[str, float] = {}
    for task_id in task_ids:
        samples = _transcripts_for(transcripts, "a", task_id)
        total = sum(
            1
            for transcript in samples
            for call in transcript.tool_calls
            if _tool_name(call) == SEARCH
        )
        searches_per_task[task_id] = _rate(total, len(samples))

    # ---- 零命中恢复（臂 a）----
    opportunities = 0
    recovered = 0
    for transcript in _transcripts_for(transcripts, "a"):
        calls = transcript.tool_calls
        for index, call in enumerate(calls):
            if _tool_name(call) != SEARCH:
                continue
            if NO_HIT_MARKER not in str(call.get("result") or ""):
                continue
            opportunities += 1
            if any(_tool_name(later) == SEARCH for later in calls[index + 1 :]):
                recovered += 1

    # ---- invoke 正确率（臂 a）----
    invokes = 0
    copied_from_results = 0
    param_errors = 0
    for transcript in _transcripts_for(transcripts, "a"):
        seen_results: list[str] = []
        for call in transcript.tool_calls:
            if _tool_name(call) == INVOKE:
                invokes += 1
                arguments = call.get("arguments")
                target = arguments.get("target") if isinstance(arguments, dict) else None
                if isinstance(target, str) and target and any(
                    target in text for text in seen_results
                ):
                    copied_from_results += 1
            seen_results.append(str(call.get("result") or ""))
        if not _passed(transcript) and "param" in str(
            _verdict_field(transcript, "reason", "")
        ):
            param_errors += 1

    # ---- status 调用与放弃率 ----
    status_calls: dict[str, int] = {}
    give_up_rate: dict[str, float] = {}
    for arm in ARMS:
        samples = _transcripts_for(transcripts, arm)
        status_calls[arm] = sum(
            1
            for transcript in samples
            for call in transcript.tool_calls
            if _tool_name(call) == STATUS
        )
        give_up_rate[arm] = _rate(
            sum(1 for transcript in samples if transcript.gave_up), len(samples)
        )

    # ---- token 聚合（臂 × 任务）----
    tokens: dict[str, dict[str, dict[str, int]]] = {
        arm: {
            task_id: {"prompt": 0, "completion": 0, "total": 0}
            for task_id in task_ids
        }
        for arm in ARMS
    }
    for transcript in transcripts:
        bucket = tokens.setdefault(
            transcript.arm,
            {
                task_id: {"prompt": 0, "completion": 0, "total": 0}
                for task_id in task_ids
            },
        ).setdefault(
            transcript.task_id, {"prompt": 0, "completion": 0, "total": 0}
        )
        for usage in transcript.usages:
            bucket["prompt"] += int(usage.get("prompt_tokens", 0) or 0)
            bucket["completion"] += int(usage.get("completion_tokens", 0) or 0)
            bucket["total"] += int(usage.get("total_tokens", 0) or 0)

    # ---- 消融（a/c/d）----
    discovery = _self_discovery(transcripts, PROXY_ARMS, tasks)
    ablation_passed = {
        arm: sum(
            1 for transcript in _transcripts_for(transcripts, arm) if _passed(transcript)
        )
        for arm in PROXY_ARMS
    }

    return {
        "per_task": per_task,
        "pairing": pairing,
        "self_discovery": discovery["a"],
        "hallucination_rate": hallucination_rate,
        "searches_per_task": searches_per_task,
        "zero_hit_recovery": {
            "opportunities": opportunities,
            "recovered": recovered,
        },
        "invoke_accuracy": {
            "invokes": invokes,
            "copied_from_results": copied_from_results,
            "param_errors": param_errors,
        },
        "status_calls": status_calls,
        "give_up_rate": give_up_rate,
        "tokens": tokens,
        "ablation": {
            "passed": ablation_passed,
            "self_discovery": discovery,
        },
    }
