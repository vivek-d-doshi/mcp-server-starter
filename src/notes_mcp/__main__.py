"""Entry point: `python -m notes_mcp` (stdio) or `--transport streamable-http`.

Over stdio, stdout carries the protocol. Never print() to stdout here; log to stderr only.
"""

from __future__ import annotations

import argparse
import logging
import sys

from notes_mcp.server import create_server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="notes-mcp", description="Notes MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="streamable-http only")
    parser.add_argument("--port", type=int, default=8000, help="streamable-http only")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    server = create_server()
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
