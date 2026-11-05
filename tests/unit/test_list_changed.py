"""4.1-4.3 list_changed 双线通知（TDD 先红）。

- 签名 diff：变化 → remove+重注册+双线通知；不变 → 安静
- 旧代线：middleware 捕获 session → send_tool_list_changed（真 Client 观察）
- 新代线：注入的 SubscriptionBus publish ToolsListChanged
- 能力位：initialize 声明 tools.listChanged=true（wrap 修正）
"""

from __future__ import annotations

from pathlib import Path

from mcp import Client

from grep_mcp_tools.config import Config, Settings
from grep_mcp_tools.health import HealthRegistry
from grep_mcp_tools.server import ProxyServer


def make_proxy(tmp_path: Path, registry: HealthRegistry) -> ProxyServer:
    config = Config(
        servers={},
        settings=Settings(),
        config_path=tmp_path / "config.json",
    )
    return ProxyServer(config, registry, {}, index_root=tmp_path / "idx")


class FakeSession:
    """旧代线观察点：记录 send_tool_list_changed 调用。

    注：SDK 2.3.0 的 Client 直连 MCPServer 走新代 in-process 路径
    （2026-07-28），该模式下变更通知只经 subscriptions/listen 流，
    旧代 session 推送对直连客户端不可见——旧代线单测只能断言
    "session 方法被调"，全链路验证归 B03（真实 initialize 客户端）。
    """

    def __init__(self) -> None:
        self.calls = 0

    async def send_tool_list_changed(self) -> None:
        self.calls += 1


async def test_signature_change_reregisters_and_notifies_both_lines(tmp_path: Path) -> None:
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    proxy = make_proxy(tmp_path, registry)

    bus_events: list = []
    proxy.bus.subscribe(bus_events.append)  # 新代线观察点
    fake_session = FakeSession()
    proxy._session_capture.session = fake_session  # 旧代线观察点

    async with Client(proxy.mcp) as client:
        await client.list_tools()  # 首个请求走通 middleware
        assert not bus_events and fake_session.calls == 0  # 尚无变化

        # 清单变化：hr 工具数 8 → 9
        registry.record_live("hr", tool_count=9)
        await proxy.sync_after_refresh()

        # 旧代线：session 推送被触发
        assert fake_session.calls == 1
        # 重拉 tools/list：description 已更新
        listing = await client.list_tools()
        by_name = {tool.name: tool for tool in listing.tools}
        assert "hr (9 tools)" in (by_name["search_mcp_tool"].description or "")

    # 新代线：bus 收到 ToolsListChanged
    assert len(bus_events) == 1
    assert type(bus_events[0]).__name__ == "ToolsListChanged"


async def test_same_signature_stays_quiet(tmp_path: Path) -> None:
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    proxy = make_proxy(tmp_path, registry)

    bus_events: list = []
    proxy.bus.subscribe(bus_events.append)
    registrations: list = []
    original = proxy._register_search_tool
    proxy._register_search_tool = lambda description: (  # type: ignore[method-assign]
        registrations.append(description),
        original(description),
    )

    registry.record_live("hr", tool_count=8)  # 同签名
    await proxy.sync_after_refresh()

    assert registrations == []  # 未重注册
    assert bus_events == []  # 未发通知


def test_initialize_capability_advertises_tools_list_changed(tmp_path: Path) -> None:
    """SDK 默认 NotificationOptions 全 False；wrap 后能力位必须为 true。"""
    proxy = make_proxy(tmp_path, HealthRegistry())
    options = proxy.mcp._lowlevel_server.create_initialization_options()
    capabilities = options.capabilities
    assert capabilities is not None
    assert capabilities.tools is not None
    assert capabilities.tools.list_changed is True


async def test_notify_without_session_is_silent(tmp_path: Path) -> None:
    """尽力而为：无任何下游会话时签名变化不报错（bus 无 listener 静默）。"""
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    proxy = make_proxy(tmp_path, registry)  # 无 Client 连接

    registry.record_live("hr", tool_count=9)
    await proxy.sync_after_refresh()  # 不抛


async def test_middleware_captures_session_on_first_request(tmp_path: Path) -> None:
    """旧代线捕获机制：首个真实下游请求后 _session_capture.session 非 None。"""
    proxy = make_proxy(tmp_path, HealthRegistry())
    assert proxy._session_capture.session is None  # 尚无请求

    async with Client(proxy.mcp) as client:
        await client.list_tools()
        assert proxy._session_capture.session is not None  # middleware 已捕获


async def test_reregister_failure_forces_retry_next_round(tmp_path: Path) -> None:
    """重注册中途异常不得让 search_mcp_tool 永久缺席（签名成功后才提交）。"""
    registry = HealthRegistry()
    registry.record_live("hr", tool_count=8)
    proxy = make_proxy(tmp_path, registry)
    fake_session = FakeSession()
    proxy._session_capture.session = fake_session

    # 第一次重注册失败（渲染抛错），签名不得提交
    def boom_description():
        raise RuntimeError("render blew up")

    proxy._render_search_description = boom_description  # type: ignore[method-assign]
    registry.record_live("hr", tool_count=9)
    try:
        await proxy.sync_after_refresh()
        raise AssertionError("expected the reregister failure to propagate")
    except RuntimeError:
        pass
    # 工具被 remove 后未注册回来（本轮失败）；签名保持旧值 → 下轮重试

    # 第二轮：恢复正常渲染，重试成功、工具回归、通知补发
    del proxy._render_search_description  # 移除实例遮蔽，回到真实现
    await proxy.sync_after_refresh()
    assert fake_session.calls == 1  # 恢复轮补发了通知
    tools = await proxy.mcp.list_tools()
    names = {tool.name for tool in tools}
    assert "search_mcp_tool" in names
