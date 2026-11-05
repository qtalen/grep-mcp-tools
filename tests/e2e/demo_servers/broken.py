"""broken — stdio demo upstream that exits immediately on startup.

Used to verify the proxy fails soft when an upstream cannot start.
"""

from __future__ import annotations


def main() -> None:
    raise SystemExit(3)


if __name__ == "__main__":
    main()
