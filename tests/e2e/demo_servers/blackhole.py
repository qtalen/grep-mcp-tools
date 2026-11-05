"""blackhole — accepts TCP connections and never answers (B03).

Bare sockets only (no ``mcp`` import): the proxy can establish the TCP
connection, but no MCP handshake response ever arrives, so the upstream must
time out instead of hanging the startup path. Each accepted connection is held
open by a daemon thread that drains input and never sends a byte.
"""

from __future__ import annotations

import argparse
import socket
import threading


def _hold(conn: socket.socket) -> None:
    """Drain the connection until the peer closes it; never write back."""
    try:
        while conn.recv(65536):
            pass
    except OSError:
        pass
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="blackhole demo upstream (accepts, never answers)"
    )
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", args.port))
        listener.listen()
        try:
            while True:
                conn, _addr = listener.accept()
                threading.Thread(target=_hold, args=(conn,), daemon=True).start()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
