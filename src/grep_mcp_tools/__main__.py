"""Entry point for the grep-mcp-tools proxy server."""

from __future__ import annotations

import argparse
import asyncio
import sys

from grep_mcp_tools.config import ConfigError, load_config
from grep_mcp_tools.server import run_proxy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grep-mcp-tools",
        description="Proxy-layer MCP server: search/invoke/status over indexed upstream tools.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the JSON config file (mcpServers + optional settings).",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"grep-mcp-tools: config error: {exc}", file=sys.stderr)
        return 2

    asyncio.run(run_proxy(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
