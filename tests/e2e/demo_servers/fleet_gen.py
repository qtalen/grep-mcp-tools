"""fleet_gen — Streamable HTTP demo upstream (42 programmatic region report tools).

All 42 tools are registered programmatically via a loop, producing unique
names of the form ``region_{area}_report``.
"""

from __future__ import annotations

import argparse

from mcp.server import MCPServer

mcp = MCPServer("fleet_gen")

_MAJORS = ["north", "south", "east", "west", "central"]
_NUMS = [str(n) for n in range(1, 9)]
AREAS = [f"{major}_{num}" for major in _MAJORS for num in _NUMS] + ["polar", "orbit"]


def _make_report(area: str):
    def report(area: str = area) -> str:
        return f"region {area}: fleet nominal"

    report.__name__ = f"region_{area}_report"
    report.__doc__ = f"Report the fleet status for region {area}"
    return report


for _area in AREAS:
    mcp.tool()(_make_report(_area))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="fleet_gen demo upstream (Streamable HTTP)"
    )
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
