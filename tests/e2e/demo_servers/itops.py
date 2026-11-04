"""itops — Streamable HTTP demo upstream for the B01 walking skeleton.

12 tools across ticket / deployment / log domains, plus:
- a ``search`` tool that shares its name with hr (namespace test)
- ``firehose`` returning ~100 KB (passthrough size test)
- ``boom`` raising an error (upstream error passthrough test)
- "region" planted in four tool definitions (over-threshold demo)
"""

from __future__ import annotations

import argparse

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer("itops")


@mcp.tool()
def search(query: str) -> str:
    """Search the operations knowledge base for runbooks and deployment docs."""
    return f"itops search results for {query!r}: 2 runbooks"


@mcp.tool()
def create_ticket(title: str, severity: str) -> str:
    """Create an operations ticket."""
    return f"ticket created: {title!r} (severity={severity})"


@mcp.tool()
def list_tickets(status: str = "open") -> str:
    """List operations tickets, optionally filtered by status."""
    return f"tickets with status={status}: 7 tickets"


@mcp.tool()
def deploy_service(service: str, version: str, env: str) -> str:
    """Deploy a service version to an environment."""
    return f"deployed {service} {version} to {env}"


@mcp.tool(annotations=ToolAnnotations(destructive_hint=True))
def rollback_deploy(deployment_id: str) -> str:
    """Roll back a deployment (irreversible side effects)."""
    return f"deployment {deployment_id} rolled back"


@mcp.tool()
def get_logs(service: str, lines: int = 50) -> str:
    """Fetch recent logs for a service."""
    return f"last {lines} log lines for {service}"


@mcp.tool()
def firehose() -> str:
    """Return a very large log payload (~100 KB) for passthrough testing."""
    return "x" * 102400


@mcp.tool()
def boom() -> str:
    """Intentionally raise an error to exercise upstream error passthrough."""
    raise RuntimeError("upstream boom: intentional failure")


@mcp.tool()
def region_deploy_status(region_id: str) -> str:
    """Show the deployment status for a region."""
    return f"region {region_id}: 3 services healthy"


@mcp.tool()
def region_log_summary(region_id: str) -> str:
    """Summarize the log stream for a region."""
    return f"region {region_id}: 120 errors in the last hour"


@mcp.tool()
def region_capacity(region_id: str) -> str:
    """Report the capacity utilization for a region."""
    return f"region {region_id}: 61% capacity used"


@mcp.tool()
def region_incident_digest(region_id: str) -> str:
    """Digest the open incidents for a region."""
    return f"region {region_id}: 2 open incidents"


def main() -> None:
    parser = argparse.ArgumentParser(description="itops demo upstream (Streamable HTTP)")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
