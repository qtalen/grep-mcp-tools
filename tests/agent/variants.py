"""臂 c/d：在 harness 侧包装代理 Arm，剥离动态注入块与双零路标。

运行时强断言（B04 design『消融实现』）：剥离后发给模型的内容不得再含
`Connected services`（臂 c）/ `Available services`（臂 d）；剥不动即失败，
绝不静默降级成完整代理。
"""

from __future__ import annotations

from typing import Any

from tests.agent.ablate import (
    INJECTION_MARKERS,
    SIGNPOST_MARKER,
    strip_service_injection,
    strip_signpost,
)

SEARCH_TOOL = "search_mcp_tool"


class AblatedArm:
    """包装任意代理 Arm（结构类型），按开关剥离两类被动信道。"""

    def __init__(
        self,
        inner: Any,
        strip_description: bool = False,
        strip_signpost: bool = False,
    ) -> None:
        self._inner = inner
        self.strip_description = strip_description
        self.strip_signpost = strip_signpost

    async def list_openai_tools(self) -> list[dict]:
        tools = await self._inner.list_openai_tools()
        if not self.strip_description:
            return tools

        result: list[dict] = []
        for item in tools:
            function = dict(item.get("function") or {})
            if function.get("name") == SEARCH_TOOL:
                description = function.get("description") or ""
                stripped = strip_service_injection(description)
                if any(marker in description for marker in INJECTION_MARKERS):
                    assert not any(
                        marker in stripped for marker in INJECTION_MARKERS
                    ), "ablation failed: service injection block was not stripped"
                assert (
                    "Connected services" not in stripped
                ), "ablation failed: description still contains 'Connected services'"
                function["description"] = stripped
            result.append({**item, "function": function})
        return result

    async def call_upstream(self, name: str, arguments: dict) -> str:
        result = await self._inner.call_upstream(name, arguments)
        if not self.strip_signpost or name != SEARCH_TOOL:
            return result
        stripped = strip_signpost(result)
        if SIGNPOST_MARKER in result:
            assert (
                SIGNPOST_MARKER not in stripped
            ), "ablation failed: signpost block was not stripped"
        assert (
            "Available services" not in stripped
        ), "ablation failed: result still contains 'Available services'"
        return stripped
