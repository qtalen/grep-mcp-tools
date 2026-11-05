"""D-27：Streamable HTTP 上游关闭不发 DELETE（terminate_on_close=False）。

内网本地上游会话为内存态，DELETE 无收益；不可达上游会吃满 30s connect
超时拖慢退出（SDK 默认 True 时先 DELETE 再取消任务组）。进程级退出耗时
断言归 B03 E 区，这里锁 transport 构造参数。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from grep_mcp_tools.config import ServerEntry
from grep_mcp_tools.index import _http_transport


async def test_http_transport_passes_terminate_on_close_false(monkeypatch) -> None:
    """_http_transport 构造 streamable_http_client 时必须传 terminate_on_close=False。"""
    captured: dict = {}

    @asynccontextmanager
    async def fake_streamable_http_client(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        yield ("read_stream", "write_stream")

    monkeypatch.setattr(
        "grep_mcp_tools.index.streamable_http_client", fake_streamable_http_client
    )
    entry = ServerEntry(name="itops", transport="http", url="http://127.0.0.1:8123/mcp")
    async with _http_transport(entry):
        pass

    assert captured["terminate_on_close"] is False
