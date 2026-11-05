"""迷你 agent 工具循环。

契约（B04 tasks 2.3）：`chat.create()` 同步返回服务端 assistant message 原对象；
循环把它**原样 append** 进历史（不重建字段），从而保留 DeepSeek Thinking Mode
要求回传的 `reasoning_content`。工具结果以 `role=tool` 消息回填，直到模型不再
发起工具调用，或达到轮次上限。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol


@dataclass
class Usage:
    """一轮请求的 token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatResponse:
    """模型响应：`message` 必须是服务端返回的原对象。"""

    message: dict
    usage: Usage


@dataclass
class ToolCallRecord:
    """一次工具调用的执行记录（transcript / 判分用）。"""

    name: str
    arguments: dict
    result: str


@dataclass
class LoopResult:
    """工具循环的完整结果。"""

    messages: list[dict]
    final_text: str
    gave_up: bool
    turns: int
    usages: list[Usage]
    tool_calls: list[ToolCallRecord]


class ChatClient(Protocol):
    """循环所需的 chat 客户端窄接口（真实 openai 包装在后续任务提供）。"""

    def create(self, messages: list[dict], tools: list[dict]) -> ChatResponse: ...


CallTool = Callable[[str, dict], Awaitable[str]]


class ToolArgumentsError(ValueError):
    """工具调用参数不是合法 JSON。"""


def _parse_arguments(raw: Any) -> dict:
    """解析 function.arguments；空串视为无参数 `{}`，非法 JSON 抛错。"""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise ToolArgumentsError(f"invalid tool arguments: {raw!r}") from error


def _as_text(result: Any) -> str:
    """工具返回值统一成字符串（正常情况下本身就是 str）。"""
    return result if isinstance(result, str) else str(result)


async def run_tool_loop(
    chat: ChatClient,
    user_prompt: str,
    tools: list[dict],
    call_tool: CallTool,
    max_turns: int = 16,
    system: str | None = None,
) -> LoopResult:
    """跑一次带工具调用的对话，返回完整消息历史与统计。"""
    messages: list[dict] = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_prompt})

    usages: list[Usage] = []
    tool_calls: list[ToolCallRecord] = []
    turns = 0
    last_message: dict | None = None

    while turns < max_turns:
        response = chat.create(messages, tools)
        message = response.message
        # 保留服务端对象本身（含 reasoning_content / tool_calls），不重建字典。
        messages.append(message)
        last_message = message
        turns += 1
        usages.append(response.usage)

        pending = message.get("tool_calls") or []
        if not pending:
            return LoopResult(
                messages=messages,
                final_text=message.get("content") or "",
                gave_up=False,
                turns=turns,
                usages=usages,
                tool_calls=tool_calls,
            )

        for call in pending:
            function = call.get("function") or {}
            name = function.get("name") or ""
            arguments = _parse_arguments(function.get("arguments"))
            result = _as_text(await call_tool(name, arguments))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or "",
                    "content": result,
                }
            )
            tool_calls.append(
                ToolCallRecord(name=name, arguments=arguments, result=result)
            )

    # 轮次耗尽仍未给出最终回答：记放弃，final_text 取最后一轮 assistant content。
    return LoopResult(
        messages=messages,
        final_text=(last_message or {}).get("content") or "",
        gave_up=True,
        turns=turns,
        usages=usages,
        tool_calls=tool_calls,
    )
