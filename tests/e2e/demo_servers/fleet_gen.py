"""fleet_gen — Streamable HTTP demo upstream (default: 42 region report tools).

Tools are registered programmatically via a loop, producing unique names of the
form ``region_{area}_report``. ``--count`` scales the roster for the F-section
scale smoke; the default (42) reproduces the original 42 names exactly.
"""

from __future__ import annotations

import argparse

from mcp.server import MCPServer

mcp = MCPServer("fleet_gen")

DEFAULT_COUNT = 42
_MAJORS = ["north", "south", "east", "west", "central"]
_NUMS = [str(n) for n in range(1, 9)]
AREAS = [f"{major}_{num}" for major in _MAJORS for num in _NUMS] + ["polar", "orbit"]


def _make_report(area: str):
    def report(area: str = area) -> str:
        return f"region {area}: fleet nominal"

    report.__name__ = f"region_{area}_report"
    report.__doc__ = f"Report the fleet status for region {area}"
    return report


def areas_for(count: int) -> list[str]:
    """First ``count`` areas: the original 42, then synthetic ``sector_N`` ones."""
    areas = list(AREAS)
    index = 1
    while len(areas) < count:
        areas.append(f"sector_{index}")
        index += 1
    return areas[:count]


def register(count: int) -> None:
    for area in areas_for(count):
        mcp.tool()(_make_report(area))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="fleet_gen demo upstream (Streamable HTTP)"
    )
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    args = parser.parse_args()
    register(args.count)
    mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
