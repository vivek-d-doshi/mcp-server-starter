"""Run: filelens --root ~/projects [--root DIR] [--allow-file FILE]. stdout is the protocol channel: log to stderr only."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from filelens.config import ConfigError, build_settings
from filelens.server import create_server


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="filelens", description="Read-only, sandboxed file reader for AI assistants (MCP).")
    p.add_argument("--root", action="append", default=[], metavar="DIR", help="folder the AI may read (repeatable)")
    p.add_argument(
        "--allow-file", action="append", default=[], metavar="FILE", help="a single file the AI may read (repeatable)"
    )
    p.add_argument(
        "--deny",
        action="append",
        default=[],
        metavar="PATTERN",
        help="extra file-name pattern to never read, e.g. 'payroll*'",
    )
    p.add_argument("--max-file-mb", type=int, default=25)
    p.add_argument("--audit-log", metavar="PATH", help="append one JSON line per access decision (never file contents)")
    p.add_argument(
        "--allow-broad-root", action="store_true", help="permit / or your whole home folder as a root (not recommended)"
    )
    p.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=os.environ.get("FILELENS_LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    env_roots = [r for r in os.environ.get("FILELENS_ROOTS", "").split(os.pathsep) if r]
    roots = args.root or env_roots
    if not roots and not args.allow_file:
        roots = [os.getcwd()]  # launched from a project folder by Claude Code / Cursor: that folder is the natural scope
        print(f"filelens: no --root given, using the current folder: {roots[0]}", file=sys.stderr)
    try:
        settings = build_settings(
            roots, args.allow_file, extra_deny=args.deny, max_file_mb=args.max_file_mb,
            audit_log=args.audit_log, allow_broad_root=args.allow_broad_root,
        )  # fmt: skip
    except ConfigError as exc:
        print(f"filelens: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    server = create_server(settings)
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
