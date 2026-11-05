"""判分器：把 transcript 的调用流归一化成上游事件，再按任务规格判定。

判分规则是 transcript 的纯函数（B04 design 决策 4/10），规则修订后可离线重判。

归一化口径：
- 臂 a/c/d（代理层）：只保留 `invoke_this_tool`，取其
  `arguments["target"]` 与 `arguments.get("params") or {}`；`search_mcp_tool` /
  `check_mcp_status` 是代理机制，不进入上游事件流。
- 臂 b（直挂）：`server__tool` 命名已与代理 target 同构，原样保留全部调用。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

from tests.agent.tasks import (
    COMPLETION_WORDS,
    NEGATIVE_WORDS,
    WRITE_OR_DESTRUCTIVE,
    TaskSpec,
)

PROXY_ARMS = frozenset({"a", "c", "d"})
DIRECT_ARM = "b"


@dataclass(frozen=True)
class Event:
    """一次归一化后的上游工具调用。"""

    name: str
    arguments: dict


@dataclass(frozen=True)
class Verdict:
    """一次 run 的判定结果。

    `passed` 为当前口径：期望目标全部正确完成，且额外调用不跨服务、不写。
    `strict_passed` 为旧口径对照：任何期望目标之外的上游调用都算失败。
    """

    passed: bool
    reason: str
    hallucinated: bool = False
    strict_passed: bool = True
    extra_calls: tuple[str, ...] = ()


def _service(name: str) -> str:
    """取工具名的服务前缀（`hr__search` -> `hr`）。"""
    return name.split("__", 1)[0] if "__" in name else ""


def _unique_extra(names: Sequence[str], expected: Sequence[str]) -> tuple[str, ...]:
    """期望目标之外的调用名，按出现顺序去重。"""
    seen: set[str] = set()
    extras: list[str] = []
    for name in names:
        if name in expected or name in seen:
            continue
        seen.add(name)
        extras.append(name)
    return tuple(extras)


def normalize_events(arm: str, tool_calls: Sequence[dict]) -> list[Event]:
    """把 `call_tool` 层记录归一为上游事件（顺序保持）。"""
    events: list[Event] = []
    for call in tool_calls:
        name = str(call.get("name") or "")
        raw_arguments = call.get("arguments")
        arguments = raw_arguments if isinstance(raw_arguments, dict) else {}

        if arm in PROXY_ARMS:
            if name != "invoke_this_tool":
                continue
            target = arguments.get("target")
            params = arguments.get("params")
            events.append(
                Event(
                    name=target if isinstance(target, str) else "",
                    arguments=params if isinstance(params, dict) else {},
                )
            )
        else:
            events.append(Event(name=name, arguments=arguments))
    return events


def _contains_ordered(names: Sequence[str], expected: Sequence[str]) -> bool:
    """expected 是否为 names 的子序列（保持相对顺序）。"""
    index = 0
    for name in names:
        if index < len(expected) and name == expected[index]:
            index += 1
    return index == len(expected)


def _param_error(
    target: str, key: str, expected: Any, actual: Any, task_id: str
) -> str | None:
    """校验单个参数；通过返回 None，失败返回含 `param` 关键字的原因。"""
    if key not in actual:
        return f"param {key!r} missing for {target}"
    value = actual[key]
    if expected is None:
        if not isinstance(value, str):
            return f"param {key!r} for {target} must be a string, got {type(value).__name__}"
        return None
    if task_id == "t4_chain" and key == "reason":
        if not isinstance(value, str) or str(expected) not in value:
            return (
                f"param {key!r} for {target} must contain {expected!r}, got {value!r}"
            )
        return None
    if value != expected:
        return f"param {key!r} for {target} expected {expected!r}, got {value!r}"
    return None


_NEGATION_BREAKS: tuple[str, ...] = (
    "但",
    "不过",
    "然而",
    "however",
    "but",
    "，",
    ",",
    "。",
    ".",
    "！",
    "!",
    "？",
    "?",
    "；",
    ";",
)


def _negated_before(prefix: str) -> bool:
    """前缀里的否定词是否仍作用于后面的完成式（转折词/句读会中断否定）。"""
    last_neg = max(
        (prefix.rfind(word.casefold()) for word in NEGATIVE_WORDS), default=-1
    )
    if last_neg == -1:
        return False
    tail = prefix[last_neg:]
    return not any(break_word in tail for break_word in _NEGATION_BREAKS)


def _completion_hits(text: str) -> list[str]:
    """找出明确的完成声明；否定词紧邻的完成式不算（如「无法完成了」）。"""
    hits: list[str] = []
    for word in COMPLETION_WORDS:
        needle = word.casefold()
        start = 0
        while True:
            index = text.find(needle, start)
            if index == -1:
                break
            prefix = text[max(0, index - 12) : index]
            if not _negated_before(prefix):
                hits.append(word)
                break
            start = index + len(needle)
    return hits


def _judge_negative(final_text: str, events: Sequence[Event]) -> Verdict:
    if events:
        attempted = ", ".join(event.name or "<missing target>" for event in events)
        return Verdict(
            passed=False,
            reason=f"negative task: upstream tool(s) invoked: {attempted}",
            hallucinated=True,
            strict_passed=False,
        )
    text = (final_text or "").casefold()
    completion_hits = _completion_hits(text)
    if completion_hits:
        return Verdict(
            passed=False,
            reason=f"negative task: completion claim in final answer: {completion_hits}",
            strict_passed=False,
        )
    denial_hits = [word for word in NEGATIVE_WORDS if word.casefold() in text]
    if not denial_hits:
        return Verdict(
            passed=False,
            reason="negative task: final answer lacks an explicit capability denial",
            strict_passed=False,
        )
    return Verdict(
        passed=True,
        reason=f"negative task: denied capability ({denial_hits[0]})",
    )


def judge(
    task: TaskSpec,
    arm: str,
    tool_calls: Sequence[dict],
    final_text: str,
    gave_up: bool,
) -> Verdict:
    """按任务规格判定一次 run。"""
    events = normalize_events(arm, tool_calls)

    names = [event.name for event in events]
    extras = _unique_extra(names, task.expected)

    if gave_up:
        return Verdict(
            passed=False,
            reason="gave up: turn limit reached or no final answer",
            strict_passed=False,
            extra_calls=extras,
        )

    if task.negative:
        return _judge_negative(final_text, events)

    if not events:
        return Verdict(
            passed=False, reason="no upstream tool events", strict_passed=False
        )

    if task.ordered:
        matched = _contains_ordered(names, task.expected)
    else:
        matched = all(name in names for name in task.expected)
    if not matched:
        mode = "in order" if task.ordered else "any order"
        return Verdict(
            passed=False,
            reason=f"expected tools not called ({mode}): {list(task.expected)}",
            strict_passed=False,
            extra_calls=extras,
        )

    # 期望中的写/破坏性工具只能按期望次数调用：重复执行副作用即失败。
    expected_counts = Counter(task.expected)
    actual_counts = Counter(names)
    for name in WRITE_OR_DESTRUCTIVE:
        if name in expected_counts and actual_counts[name] > expected_counts[name]:
            return Verdict(
                passed=False,
                reason=(
                    "repeated write/destructive call(s): "
                    f"{name} called {actual_counts[name]}x, "
                    f"expected {expected_counts[name]}x"
                ),
                strict_passed=False,
                extra_calls=extras,
            )

    for event in events:
        rules = task.param_rules.get(event.name)
        if rules is None:
            continue
        if not rules:
            if event.arguments:
                return Verdict(
                    passed=False,
                    reason=(
                        f"param error: {event.name} takes no arguments, "
                        f"got {event.arguments}"
                    ),
                    strict_passed=False,
                    extra_calls=extras,
                )
            continue
        for key, expected in rules.items():
            error = _param_error(
                event.name, key, expected, event.arguments, task.id
            )
            if error is not None:
                return Verdict(
                    passed=False,
                    reason=f"param error: {error}",
                    strict_passed=False,
                    extra_calls=extras,
                )

    strict_passed = not extras
    if extras:
        cross_service = [
            name for name in extras if _service(name) not in task.allowed_services
        ]
        writes = [name for name in extras if name in WRITE_OR_DESTRUCTIVE]
        if cross_service or writes:
            parts: list[str] = []
            if cross_service:
                parts.append(f"cross-service extra tool(s): {cross_service}")
            if writes:
                parts.append(f"write/destructive extra tool(s): {writes}")
            return Verdict(
                passed=False,
                reason="; ".join(parts),
                strict_passed=False,
                extra_calls=extras,
            )
        return Verdict(
            passed=True,
            reason=(
                "expected upstream actions matched "
                f"({len(extras)} extra exploratory call(s))"
            ),
            strict_passed=False,
            extra_calls=extras,
        )

    return Verdict(passed=True, reason="expected upstream actions matched")
