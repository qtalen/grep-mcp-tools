"""搜索引擎测试的共用构造器。

IndexedTool.raw_line 与生产一致：json.dumps(..., ensure_ascii=False) 单行
（匹配域 = 原始 JSON 整行，所以测试必须喂真行文本）。
"""

from __future__ import annotations

import json

from grep_mcp_tools.search import IndexedTool, LiveServer, SearchContext


def row(
    server: str,
    name: str,
    description: str = "",
    *,
    input_schema: dict | None = None,
    annotations: dict | None = None,
    title: str | None = None,
    line_no: int = 1,
) -> IndexedTool:
    schema = input_schema or {"type": "object", "properties": {}}
    payload = {
        "server": server,
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": schema,
        "annotations": annotations,
    }
    raw_line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return IndexedTool(
        server=server,
        name=name,
        title=title,
        description=description,
        input_schema=schema,
        annotations=annotations,
        raw_line=raw_line,
        line_no=line_no,
    )


def make_ctx(
    tools: list[IndexedTool],
    *,
    live: list[tuple[str, int]] | None = None,
    unavailable_count: int = 0,
    threshold: int = 30,
    fallback_sample_size: int = 10,
) -> SearchContext:
    if live is None:
        counts: dict[str, int] = {}
        for tool in tools:
            counts[tool.server] = counts.get(tool.server, 0) + 1
        live = sorted(counts.items())
    return SearchContext(
        tools=tuple(tools),
        live_servers=tuple(LiveServer(name=n, tool_count=c) for n, c in live),
        unavailable_count=unavailable_count,
        threshold=threshold,
        fallback_sample_size=fallback_sample_size,
    )
