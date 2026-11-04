"""loopback 上游绕过代理的回归测试（真实世界坑：系统代理劫持 127.0.0.1）。"""

from __future__ import annotations

import httpx2

from grep_mcp_tools.index import http_client_factory, is_loopback_url


def test_is_loopback_url_variants() -> None:
    assert is_loopback_url("http://127.0.0.1:8123/mcp")
    assert is_loopback_url("http://localhost:8123/mcp")
    assert is_loopback_url("http://[::1]:8123/mcp")
    assert is_loopback_url("https://127.0.0.1/mcp")
    assert not is_loopback_url("http://example.com/mcp")
    assert not is_loopback_url("http://127.0.0.1.evil.com/mcp")
    assert not is_loopback_url("not-a-url")


async def test_loopback_client_ignores_environment_proxies() -> None:
    factory = http_client_factory("http://127.0.0.1:8123/mcp")
    async with factory() as client:
        assert isinstance(client, httpx2.AsyncClient)
        assert client.trust_env is False


async def test_remote_client_keeps_environment_proxies() -> None:
    factory = http_client_factory("https://mcp.example.com/mcp")
    async with factory() as client:
        assert client.trust_env is True


async def test_read_timeout_disabled_for_invoke_golden_rule() -> None:
    """D-06：invoke 无代理级超时——读超时无限，连接/写/池超时保留。"""
    for url in ("http://127.0.0.1:8123/mcp", "https://mcp.example.com/mcp"):
        factory = http_client_factory(url)
        async with factory(timeout=httpx2.Timeout(5.0, read=300.0)) as client:
            assert client.timeout.read is None
            assert client.timeout.connect == 5.0
