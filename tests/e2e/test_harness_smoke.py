"""1.2 harness 冒烟：共享裸行会话 + SDK 客户端两条路径。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from tests.e2e.harness import DEMO_DIR, read_index_lines, sdk_client, write_config

if TYPE_CHECKING:
    from tests.e2e.conftest import StdStack

EXPECTED_TOOLS = {"search_mcp_tool", "invoke_this_tool", "check_mcp_status"}


def test_std_stack_tools_status_and_index(std: "StdStack") -> None:
    tools = std.session.list_tools()
    assert {tool["name"] for tool in tools} == EXPECTED_TOOLS

    text = std.session.call_tool_text("check_mcp_status")
    assert "hr: live, 8 tool" in text
    assert "itops: live, 12 tool" in text
    assert "audit: live, 3 tool" in text
    assert "dead_http: UNREACHABLE" in text

    assert len(read_index_lines(std.index_dir, "hr")) == 8


async def test_sdk_client_hr_only(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path,
        {"hr": {"command": sys.executable, "args": [str(DEMO_DIR / "hr.py")]}},
    )
    async with sdk_client(config_path) as client:
        listing = await client.list_tools()
        assert {tool.name for tool in listing.tools} == EXPECTED_TOOLS

        result = await client.call_tool("check_mcp_status", {})
        assert result.is_error is False
        text = "".join(
            block.text for block in result.content if hasattr(block, "text")
        )
        assert "hr: live, 8 tool" in text
