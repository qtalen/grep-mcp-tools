"""docs — SSE demo upstream with six documentation-domain tools.

Mirrors audit.py's shape; delete_document carries a destructive hint
and one description plants the word "region" (region export demo).
"""

from __future__ import annotations

import argparse

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer("docs")


@mcp.tool()
def list_documents(scope: str = "all") -> str:
    """List documents available in the docs library."""
    return f"docs in scope {scope!r}: 12 documents listed"


@mcp.tool()
def read_document(doc_id: str) -> str:
    """Read the full content of a document by id."""
    return f"document {doc_id!r}: contents rendered (3 sections)"


@mcp.tool()
def search_docs(query: str) -> str:
    """Search the docs library for pages matching a query."""
    return f"docs search for {query!r}: 4 matching pages"


@mcp.tool()
def upload_document(name: str, content: str = "") -> str:
    """Upload a new document into the docs library."""
    return f"document {name!r} uploaded ({len(content)} chars)"


@mcp.tool(annotations=ToolAnnotations(destructive_hint=True))
def delete_document(doc_id: str) -> str:
    """Delete a document from the library (irreversible side effects)."""
    return f"document {doc_id!r} deleted"


@mcp.tool()
def export_docs(region_id: str) -> str:
    """Export the docs bundle for a region."""
    return f"region {region_id}: 9 docs exported"


def main() -> None:
    parser = argparse.ArgumentParser(description="docs demo upstream (SSE)")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    mcp.run(transport="sse", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
