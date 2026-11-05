"""mutable — Streamable HTTP demo upstream whose roster is driven by a file (B03).

Six tools ``m0``..``m5`` are registered at import time. A daemon thread polls a
JSON state file (``{"tools": ["m0", "m2"]}``) every 50 ms and syncs the
registered set to match it, so a test can add/remove tools without restarting
the upstream (D-17 main path, decoupled from upstream death).

Missing files and malformed JSON are ignored for that round; names outside the
fixed ``m0``..``m5`` set are ignored.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Callable

from mcp.server import MCPServer

mcp = MCPServer("mutable")


def m0() -> str:
    """Demo tool m0 (roster driven by the mutable state file)."""
    return "m0: ok"


def m1() -> str:
    """Demo tool m1 (roster driven by the mutable state file)."""
    return "m1: ok"


def m2() -> str:
    """Demo tool m2 (roster driven by the mutable state file)."""
    return "m2: ok"


def m3() -> str:
    """Demo tool m3 (roster driven by the mutable state file)."""
    return "m3: ok"


def m4() -> str:
    """Demo tool m4 (roster driven by the mutable state file)."""
    return "m4: ok"


def m5() -> str:
    """Demo tool m5 (roster driven by the mutable state file)."""
    return "m5: ok"


_TOOLS: dict[str, Callable[[], str]] = {
    "m0": m0,
    "m1": m1,
    "m2": m2,
    "m3": m3,
    "m4": m4,
    "m5": m5,
}

for _name, _fn in _TOOLS.items():
    mcp.add_tool(_fn, name=_name)

_POLL_INTERVAL = 0.05


def _sync_once(state_path: Path, registered: set[str]) -> None:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        desired = {str(name) for name in payload["tools"]}
    except (OSError, ValueError, KeyError, TypeError):
        return  # missing file / bad JSON / wrong shape: ignore this round

    for name in _TOOLS:
        if name in desired:
            if name in registered:
                continue
            try:
                mcp.add_tool(_TOOLS[name], name=name)
            except Exception:  # noqa: BLE001 - keep the roster stable, retry next round
                continue
            registered.add(name)
        elif name in registered:
            try:
                mcp.remove_tool(name)
            except Exception:  # noqa: BLE001 - already gone, nothing to sync
                pass
            finally:
                registered.discard(name)


def _sync_loop(state_path: Path) -> None:
    registered: set[str] = set(_TOOLS)
    while True:
        time.sleep(_POLL_INTERVAL)
        _sync_once(state_path, registered)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="mutable demo upstream (Streamable HTTP, file-driven roster)"
    )
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--state", required=True, help="path to the JSON state file")
    args = parser.parse_args()

    threading.Thread(
        target=_sync_loop, args=(Path(args.state),), daemon=True
    ).start()
    mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
