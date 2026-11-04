"""audit — SSE demo upstream for the B01 walking skeleton.

3 tools, plus:
- one scanner tool whose description is only "audit trail retention"
  (vocabulary-mismatch / OR-fallback demo)
- "region" planted in one tool definition (over-threshold demo)
"""

from __future__ import annotations

import argparse

from mcp.server import MCPServer

mcp = MCPServer("audit")


@mcp.tool()
def audit_trail_retention() -> str:
    """audit trail retention"""
    return "retention policy: 400 days, immutable"


@mcp.tool()
def list_audit_events(since: str = "24h") -> str:
    """List audit events recorded since a time window."""
    return f"audit events since {since}: 88 events"


@mcp.tool()
def region_audit_export(region_id: str) -> str:
    """Export the audit records for a region."""
    return f"region {region_id}: 10 audit records exported"


def main() -> None:
    parser = argparse.ArgumentParser(description="audit demo upstream (SSE)")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    mcp.run(transport="sse", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
