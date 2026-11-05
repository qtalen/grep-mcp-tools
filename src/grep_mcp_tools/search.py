"""读路径语义机：``search_mcp_tool`` 的纯函数实现（无 IO、无状态）。

契约唯一来源：docs/design/architecture.md『读路径』与『稳定契约』。
本模块只消费调用方喂入的索引行（调用方负责陈旧索引隔离：只喂本轮
握手成功服务的行）与 live 服务清单。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

__all__ = [
    "IndexedTool",
    "LiveServer",
    "SearchContext",
    "SearchResponse",
    "render_search_description",
    "roster_signature",
    "search",
]


@dataclass(frozen=True)
class IndexedTool:
    """一行索引（raw_line 即 JSONL 原文，匹配域就是它）。"""

    server: str
    name: str
    title: str | None
    description: str | None
    input_schema: dict | None
    annotations: dict | None
    raw_line: str
    line_no: int

    @property
    def target(self) -> str:
        return f"{self.server}__{self.name}"

    @property
    def is_destructive(self) -> bool:
        annotations = self.annotations or {}
        return bool(
            annotations.get("destructiveHint") or annotations.get("destructive_hint")
        )


@dataclass(frozen=True)
class LiveServer:
    name: str
    tool_count: int


# ---------- description 注入面（下游 tools/list 的动态清单） ----------


def _visible_servers(
    live_servers: list[LiveServer] | tuple[LiveServer, ...], max_servers: int
) -> tuple[list[LiveServer], int]:
    """字典序排序；超上限取前 N（选取定死，防跨轮抖动）。返回 (可见集, 总数)。"""
    ordered = sorted(live_servers, key=lambda s: s.name)
    if len(ordered) <= max_servers:
        return ordered, len(ordered)
    return ordered[:max_servers], len(ordered)


def render_search_description(
    trigger_text: str,
    live_servers: list[LiveServer] | tuple[LiveServer, ...],
    unavailable_count: int,
    max_servers: int,
) -> str:
    """拼接 search_mcp_tool 的动态 description：触发文案 + 服务清单。

    清单为字典序单行（名+工具数）；超上限降级为前 N + ``(of M)`` 总数
    标注；死者不计入清单、以 ``(k unavailable)`` 计数后缀 + 指针呈现
    （被动信道纪律）；空配置与全挂文案可区分。
    """
    if not live_servers:
        if unavailable_count:
            return (
                f"{trigger_text}\n\nno upstream services currently connected "
                f"({unavailable_count} unavailable). Run check_mcp_status for details."
            )
        return f"{trigger_text}\n\nno upstream services configured"
    visible, total = _visible_servers(live_servers, max_servers)
    entries = ", ".join(
        f"{s.name} ({s.tool_count} tool{'s' if s.tool_count != 1 else ''})"
        for s in visible
    )
    header = (
        "Connected services"
        if total <= max_servers
        else f"Connected services (of {total})"
    )
    line = f"{header}: {entries}"
    if unavailable_count:
        line += f" ({unavailable_count} unavailable - run check_mcp_status)"
    return f"{trigger_text}\n\n{line}"


def roster_signature(
    live_servers: list[LiveServer] | tuple[LiveServer, ...],
    unavailable_count: int,
    max_servers: int,
) -> tuple:
    """description 文本的决定因素：可见集 (server, tool_count) + 总数 + 死者数。

    未超上限时可见集即 live 全集；超上限时被隐藏服务的变化不改变签名
    （description 文本未变，不触发重注册与通知）。
    """
    visible, total = _visible_servers(live_servers, max_servers)
    return (
        tuple((s.name, s.tool_count) for s in visible),
        total,
        unavailable_count,
    )


@dataclass(frozen=True)
class SearchContext:
    tools: tuple[IndexedTool, ...]
    live_servers: tuple[LiveServer, ...]
    unavailable_count: int
    threshold: int = 30
    fallback_sample_size: int = 10


@dataclass(frozen=True)
class SearchResponse:
    kind: str  # matches | partial | over_threshold | signpost | empty_keywords
    tools: tuple[IndexedTool, ...] = ()
    total_matches: int = 0
    text: str = ""


def search(keywords: list[str] | tuple[str, ...], ctx: SearchContext) -> SearchResponse:
    """读路径语义机的唯一入口。"""
    normalized = _normalize_keywords(keywords)
    if not normalized:
        return _empty_keywords_response(ctx)

    folded = [(tool, tool.raw_line.casefold()) for tool in ctx.tools]

    and_hits = [tool for tool, line in folded if all(k in line for k in normalized)]
    if len(and_hits) > ctx.threshold:
        return _over_threshold_response(len(and_hits), normalized, ctx)
    if and_hits:
        ordered = sorted(and_hits, key=lambda t: (t.server, t.line_no))
        return SearchResponse(
            kind="matches",
            tools=tuple(ordered),
            total_matches=len(ordered),
            text=_render_matches(ordered, normalized),
        )

    scored: list[tuple[int, IndexedTool]] = []
    for tool, line in folded:
        matched = sum(1 for k in normalized if k in line)
        if matched:
            scored.append((matched, tool))
    if scored:
        scored.sort(key=lambda pair: (-pair[0], pair[1].server, pair[1].line_no))
        shown = scored[: ctx.fallback_sample_size]
        return SearchResponse(
            kind="partial",
            tools=tuple(tool for _count, tool in shown),
            total_matches=len(scored),
            text=_render_partial(shown, len(scored), normalized),
        )

    return _signpost_response(normalized, ctx)


# ---------- normalization ----------


def _normalize_keywords(keywords: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    for keyword in keywords:
        if not isinstance(keyword, str):
            continue
        stripped = keyword.strip()
        if not stripped:
            continue
        folded = stripped.casefold()
        if folded not in normalized:
            normalized.append(folded)
    return normalized


# ---------- response builders ----------


def _render_matches(tools: list[IndexedTool], keywords: list[str]) -> str:
    header = f"{len(tools)} tool(s) matched [{', '.join(keywords)}]:"
    body = "\n\n".join(_render_tool(tool) for tool in tools)
    return f"{header}\n\n{body}"


def _render_partial(
    shown: list[tuple[int, IndexedTool]], total: int, keywords: list[str]
) -> str:
    header = (
        f"Partial match: no tool matched all keywords [{', '.join(keywords)}]. "
        f"Showing {len(shown)} of {total} tools matching at least one keyword, "
        "sorted by match count (best first):"
    )
    body = "\n\n".join(_render_tool(tool) for _count, tool in shown)
    return f"{header}\n\n{body}"


def _over_threshold_response(
    total: int, keywords: list[str], ctx: SearchContext
) -> SearchResponse:
    text = (
        f"Too many matches: {total} tools match [{', '.join(keywords)}] "
        f"(threshold: {ctx.threshold}). No results were listed.\n"
        'Narrow the search: add more keywords, or include a server name '
        '(e.g. "hr leave").'
    )
    return SearchResponse(kind="over_threshold", total_matches=total, text=text)


def _signpost_response(keywords: list[str], ctx: SearchContext) -> SearchResponse:
    keyword_repr = ", ".join(keywords)
    if not ctx.live_servers:
        if ctx.unavailable_count == 0:
            text = (
                f"No tools matched [{keyword_repr}]. "
                "No upstream services are configured."
            )
        else:
            text = (
                f"No tools matched [{keyword_repr}], and NO upstream services are "
                f"currently available ({ctx.unavailable_count} configured service(s) "
                "unreachable).\n"
                "Run check_mcp_status to see which services failed and why."
            )
    else:
        text = (
            f"No tools matched [{keyword_repr}].\n\n"
            f"{_render_service_list(ctx)}\n\n"
            'Tip: try fewer or different keywords, or include a server name '
            '(e.g. "hr leave").'
        )
    return SearchResponse(kind="signpost", text=text)


def _empty_keywords_response(ctx: SearchContext) -> SearchResponse:
    text = (
        'Usage: search_mcp_tool(keywords: ["leave", "hr"]) - pass one or more '
        "keywords.\n\n" + _render_service_list(ctx)
    )
    return SearchResponse(kind="empty_keywords", text=text)


def _render_service_list(ctx: SearchContext) -> str:
    if not ctx.live_servers:
        if ctx.unavailable_count == 0:
            return "No upstream services are configured."
        return (
            f"NO upstream services are currently available ({ctx.unavailable_count} "
            "configured service(s) unreachable).\n"
            "Run check_mcp_status to see which services failed and why."
        )
    lines = ["Available services:"]
    for server in sorted(ctx.live_servers, key=lambda s: s.name):
        lines.append(f"- {server.name}: {server.tool_count} tools")
    if ctx.unavailable_count:
        lines.append(
            f"({ctx.unavailable_count} unavailable -- run check_mcp_status for details)"
        )
    return "\n".join(lines)


# ---------- tool rendering ----------


def _render_tool(tool: IndexedTool) -> str:
    target = tool.target
    if tool.is_destructive:
        target += " [destructive]"
    description = " ".join((tool.description or "").split())
    head = f"{target} - {description}" if description else target
    return f"{head}\n  {_render_params(tool)}"


def _render_params(tool: IndexedTool) -> str:
    schema = tool.input_schema or {}
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict) or not properties:
        return "params: none"
    required = set(schema.get("required") or ())
    parts: list[str] = []
    for name, prop in properties.items():
        prop = prop if isinstance(prop, dict) else {}
        type_repr = _type_repr(prop)
        optional = "" if name in required else ", optional"
        parts.append(f"{name} ({type_repr}{optional})")
    return "params: " + ", ".join(parts)


def _type_repr(prop: dict) -> str:
    type_value = prop.get("type")
    if isinstance(type_value, str):
        return type_value
    if isinstance(type_value, list):
        return "|".join(str(item) for item in type_value)
    if "anyOf" in prop or "oneOf" in prop:
        return "any"
    return "any"
