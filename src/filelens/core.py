"""The read pipeline: check -> read once -> identify the type -> extract. Pure functions, no MCP in here."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from filelens.extract import ExtractError, Extraction, documents, textual
from filelens.sandbox import Sandbox
from filelens.sniff import Kind, sniff


@dataclass
class Loaded:
    path: Path
    kind: Kind
    size: int
    sha256: str
    modified: str
    extraction: Extraction

    @property
    def ref_id(self) -> str:
        return "fr_" + self.sha256[:8]


def _extract(
    kind: Kind, data: bytes, path: Path, sb: Sandbox, start_line: int, max_lines: int | None, number: bool
) -> Extraction:
    s = sb.s
    text_kw = dict(start_line=start_line, max_lines=max_lines, max_chars=s.max_output_chars, number=number)
    match kind.kind:
        case "text" | "code" | "markdown":
            return textual.extract_text(data, **text_kw)
        case "json":
            return textual.extract_json(data, **text_kw)
        case "yaml":
            return textual.extract_yaml(data, **text_kw)
        case "csv":
            return textual.extract_csv(
                data, tsv=path.suffix.lower() == ".tsv", max_rows=s.max_sheet_rows * 4, max_chars=s.max_output_chars
            )
        case "notebook":
            return textual.extract_notebook(data, max_chars=s.max_output_chars)
        case "pdf":
            return documents.extract_pdf(data, max_pages=s.max_pdf_pages, max_chars=s.max_output_chars)
        case "docx":
            return documents.extract_docx(data, max_chars=s.max_output_chars)
        case "xlsx":
            return documents.extract_xlsx(data, max_rows=s.max_sheet_rows, max_chars=s.max_output_chars)
        case _:
            why = {
                "image": "Images are not read (no OCR or vision in FileLens). Only metadata is returned.",
                "archive": "Archives are not opened. Only metadata is returned.",
                "pptx": "PowerPoint files are not supported yet. Only metadata is returned.",
            }.get(kind.kind, "Binary file: no extractor. Only metadata is returned.")
            return Extraction("", "none", warnings=[why])


def load(sb: Sandbox, real: Path, *, start_line: int = 1, max_lines: int | None = None, number: bool = False) -> Loaded:
    """Read an already-resolved path safely and extract it. Raises AccessError or ExtractError (both model-safe)."""
    data, st, actual = sb.read_file(real)
    kind = sniff(data, actual)
    try:
        extraction = _extract(kind, data, actual, sb, start_line, max_lines, number)
    except ExtractError:
        raise
    if kind.note:
        extraction.warnings.insert(0, kind.note)
    modified = datetime.fromtimestamp(st.st_mtime, UTC).isoformat(timespec="seconds")
    return Loaded(actual, kind, len(data), hashlib.sha256(data).hexdigest(), modified, extraction)
