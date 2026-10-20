"""Text, code, Markdown, JSON, YAML, CSV, notebooks. Plain-text families."""

from __future__ import annotations

import csv
import io
import json

import yaml

from filelens.extract import ExtractError, Extraction


def decode(data: bytes) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace"), warnings
    try:
        return data.decode("utf-8-sig"), warnings
    except UnicodeDecodeError:
        warnings.append("Not valid UTF-8; decoded as Latin-1, so some characters may be wrong.")
        return data.decode("latin-1"), warnings


def _window(lines: list[str], start: int, max_lines: int | None, max_chars: int, number: bool) -> Extraction:
    total = len(lines)
    start = max(1, start)
    first = start - 1
    chosen: list[str] = []
    used = 0
    last = first
    limit_lines = max_lines if max_lines else total
    for idx in range(first, min(total, first + limit_lines)):
        rendered = f"{idx + 1:>5}| {lines[idx]}" if number else lines[idx]
        if used + len(rendered) + 1 > max_chars and chosen:
            break
        chosen.append(rendered)
        used += len(rendered) + 1
        last = idx + 1
    truncated = last < total
    ex = Extraction("\n".join(chosen), "text", "line", total, (start, last) if chosen else None, truncated=truncated)
    if truncated:
        ex.continue_hint = f"Call again with start_line={last + 1} to continue ({total - last} more lines)."
    return ex


def extract_text(data: bytes, *, start_line: int, max_lines: int | None, max_chars: int, number: bool) -> Extraction:
    text, warnings = decode(data)
    ex = _window(text.splitlines(), start_line, max_lines, max_chars, number)
    ex.warnings += warnings
    return ex


def _shape(value: object, depth: int = 0) -> str:
    if isinstance(value, dict):
        keys = list(value)[:12]
        more = f", +{len(value) - 12} more" if len(value) > 12 else ""
        return f"object with {len(value)} keys ({', '.join(map(str, keys))}{more})"
    if isinstance(value, list):
        inner = f" of {_shape(value[0], depth + 1)}" if value and depth < 1 else ""
        return f"array of {len(value)} items{inner}"
    return type(value).__name__


def extract_json(data: bytes, **kw) -> Extraction:
    ex = extract_text(data, **kw)
    ex.extractor = "json"
    text, _ = decode(data)
    try:
        parsed = json.loads(text)
        ex.meta["structure"] = _shape(parsed)
    except json.JSONDecodeError as exc:
        if text.lstrip().startswith("{") and "\n{" in text:
            ex.meta["structure"] = "JSON Lines (one JSON value per line)"
        else:
            ex.warnings.append(f"Invalid JSON ({exc.msg} at line {exc.lineno}); showing raw text.")
    return ex


def extract_yaml(data: bytes, **kw) -> Extraction:
    ex = extract_text(data, **kw)
    ex.extractor = "yaml"
    text, _ = decode(data)
    try:
        docs = list(yaml.safe_load_all(text))  # safe_load only: never builds arbitrary objects
        first = docs[0] if docs else None
        ex.meta["structure"] = f"{len(docs)} document(s); first is {_shape(first)}"
    except yaml.YAMLError as exc:
        ex.warnings.append(f"Invalid YAML ({str(exc).splitlines()[0]}); showing raw text.")
    return ex


def extract_csv(data: bytes, *, tsv: bool, max_rows: int, max_chars: int) -> Extraction:
    text, warnings = decode(data)
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter="\t" if tsv else ","))
    except csv.Error as exc:
        raise ExtractError(f"CSV could not be parsed: {exc}") from None
    if not rows:
        return Extraction("", "csv", "row", 0, None, warnings=["File is empty."])
    header, body = rows[0], rows[1:]
    shown = body[:max_rows]
    md = ["| " + " | ".join(c.replace("|", "\\|") for c in header) + " |", "|" + "---|" * len(header)]
    md += ["| " + " | ".join(c.replace("|", "\\|") for c in r) + " |" for r in shown]
    out = "\n".join(md)
    truncated = len(body) > len(shown) or len(out) > max_chars
    ex = Extraction(
        out[:max_chars], "csv", "row", len(body), (1, len(shown)) if shown else None, truncated=truncated, warnings=warnings
    )
    ex.meta["columns"] = f"{len(header)}: {', '.join(header[:20])}"
    ex.meta["rows"] = f"{len(body)} data rows"
    if truncated:
        ex.continue_hint = (
            f"Showing the first {len(shown)} of {len(body)} rows. Ask the user for a narrower question or a smaller file."
        )
    return ex


def extract_notebook(data: bytes, *, max_chars: int) -> Extraction:
    text, _ = decode(data)
    try:
        nb = json.loads(text)
        cells = nb["cells"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ExtractError("Not a valid Jupyter notebook.") from None
    out: list[str] = []
    for i, cell in enumerate(cells, 1):
        src = "".join(cell.get("source", []))
        out.append(f"[cell {i} · {cell.get('cell_type', '?')}]\n{src}")
        for o in cell.get("outputs", [])[:2]:
            body = "".join(o.get("text", [])) or "".join(o.get("data", {}).get("text/plain", []))
            if body:
                out.append(f"[output of cell {i}]\n{body[:1000]}")
    joined = "\n\n".join(out)
    ex = Extraction(joined[:max_chars], "notebook", "cell", len(cells), (1, len(cells)), truncated=len(joined) > max_chars)
    ex.meta["cells"] = str(len(cells))
    return ex
