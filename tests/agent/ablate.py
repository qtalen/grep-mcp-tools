"""消融剥离器：harness 侧信道删除代理注入块（产品代码零改动）。

纪律：只删除、不添加、不改写——剥离后剩余的必须是代理原话，反事实才干净。
找不到块时原样返回（是否「剥不动」由调用方的运行时强断言把关）。
"""

from __future__ import annotations

INJECTION_MARKERS: tuple[str, ...] = (
    "\n\nConnected services",
    "\n\nno upstream services",
)

SIGNPOST_MARKER = "\n\nAvailable services:"


def strip_service_injection(description: str) -> str:
    """删除 `search_mcp_tool` description 中的动态服务清单注入块。

    命中 `\\n\\nConnected services...` 或 `\\n\\nno upstream services...` 时，
    返回注入块之前的静态触发文案；找不到注入块时原样返回。
    """
    for marker in INJECTION_MARKERS:
        index = description.find(marker)
        if index != -1:
            return description[:index]
    return description


def strip_signpost(text: str) -> str:
    """删除搜索响应末尾的 `Available services:` 路标块。

    双零命中（`No tools matched ...`）与空关键词（`Usage: ...`）两条路径都
    以 `\\n\\nAvailable services:` 起始，故统一从该标记删到结尾；找不到时原样
    返回。
    """
    index = text.find(SIGNPOST_MARKER)
    if index != -1:
        return text[:index]
    return text
