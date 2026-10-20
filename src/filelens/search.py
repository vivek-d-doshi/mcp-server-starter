"""Find files by name, or by text inside text files. Bounded by depth, count and time. Never follows symlinks."""

from __future__ import annotations

import difflib
import fnmatch
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from filelens.sandbox import NOISE_DIRS, AccessError, Sandbox, denied_reason
from filelens.sniff import sniff

MAX_DEPTH = 12
MAX_ENTRIES = 200_000
GREP_MAX_BYTES = 2 * 1024 * 1024
TEXT_KINDS = {"text", "code", "markdown", "json", "yaml", "csv", "notebook"}


@dataclass
class Match:
    path: str
    size: int
    line: int | None = None
    snippet: str | None = None


@dataclass
class SearchResult:
    matches: list[Match] = field(default_factory=list)
    scanned: int = 0
    truncated: bool = False
    timed_out: bool = False
    names: list[str] = field(default_factory=list)  # for "did you mean" suggestions


def walk(sb: Sandbox, base: Path, deadline: float):
    """Yield (path, size) for regular, non-secret, non-symlink files. Skips noise folders and secret locations."""
    stack = [(base, 0)]
    seen = 0
    while stack:
        folder, depth = stack.pop()
        if time.monotonic() > deadline:
            return
        try:
            with os.scandir(folder) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            seen += 1
            if seen > MAX_ENTRIES:
                return
            path = Path(entry.path)
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH and entry.name not in NOISE_DIRS and not denied_reason(path, sb.s.extra_deny):
                        stack.append((path, depth + 1))
                elif entry.is_file(follow_symlinks=False) and not denied_reason(path, sb.s.extra_deny):
                    yield path, entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue


def _name_matches(query: str, name: str) -> bool:
    q, n = query.lower(), name.lower()
    if any(ch in q for ch in "*?["):
        return fnmatch.fnmatch(n, q)
    return q in n


def find_by_name(sb: Sandbox, query: str, bases: list[Path], max_results: int) -> SearchResult:
    deadline = time.monotonic() + sb.s.search_timeout_s
    result = SearchResult()
    for base in bases:
        for path, size in walk(sb, base, deadline):
            result.scanned += 1
            if len(result.names) < 5000:
                result.names.append(path.name)
            if _name_matches(query, path.name):
                if len(result.matches) >= max_results:
                    result.truncated = True
                    return result
                result.matches.append(Match(str(path), size))
        if time.monotonic() > deadline:
            result.timed_out = True
            break
    return result


def suggest(query: str, names: list[str], bases: list[Path], sb: Sandbox, limit: int = 5) -> list[str]:
    """'Did you mean' for a missing file: closest file names inside the allowed folders."""
    close = difflib.get_close_matches(query, list(dict.fromkeys(names)), n=limit, cutoff=0.6)
    if not close:
        return []
    found: list[str] = []
    for base in bases:
        for path, _ in walk(sb, base, time.monotonic() + 3):
            if path.name in close and str(path) not in found:
                found.append(str(path))
                if len(found) >= limit:
                    return found
    return found


def grep(sb: Sandbox, needle: str, bases: list[Path], max_results: int, glob: str | None = None) -> SearchResult:
    """Case-insensitive fixed-string search inside text files under the allowed folders. Not a regex, on purpose:
    a model-supplied regex can hang the server (ReDoS)."""
    deadline = time.monotonic() + sb.s.search_timeout_s
    result = SearchResult()
    needle_l = needle.lower()
    for base in bases:
        for path, size in walk(sb, base, deadline):
            if size > GREP_MAX_BYTES or (glob and not fnmatch.fnmatch(path.name.lower(), glob.lower())):
                continue
            result.scanned += 1
            try:
                data, _, actual = sb.read_file(path)
            except AccessError:
                continue
            if sniff(data, actual).kind not in TEXT_KINDS:
                continue
            for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if needle_l in line.lower():
                    if len(result.matches) >= max_results:
                        result.truncated = True
                        return result
                    result.matches.append(Match(str(actual), size, number, line.strip()[:200]))
        if time.monotonic() > deadline:
            result.timed_out = True
            break
    return result
