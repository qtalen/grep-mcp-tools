"""empty — stdio demo upstream that handshakes successfully with 0 tools (B03).

Used for the "0-tool upstream" case: the handshake must succeed, no index file
may be written, and the registry keeps the server at ``0 tools``.
"""

from __future__ import annotations

from mcp.server import MCPServer

mcp = MCPServer("empty")


if __name__ == "__main__":
    mcp.run(transport="stdio")
