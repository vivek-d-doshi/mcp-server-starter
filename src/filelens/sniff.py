"""Work out what a file really is. Magic bytes beat the extension: a renamed file must not fool the reader."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

LANGUAGES = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx",
    ".java": "java", ".kt": "kotlin", ".go": "go", ".rs": "rust", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".rb": "ruby", ".php": "php", ".swift": "swift", ".sh": "bash", ".zsh": "bash", ".sql": "sql",
    ".html": "html", ".css": "css", ".scss": "scss", ".toml": "toml", ".ini": "ini", ".xml": "xml", ".astro": "astro",
    ".vue": "vue", ".svelte": "svelte", ".r": "r", ".lua": "lua", ".dockerfile": "dockerfile", ".tf": "hcl",
}  # fmt: skip

# (magic prefix, kind, mime). Anything here that we cannot extract is reported as metadata only.
MAGIC = [
    (b"%PDF-", "pdf", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image", "image/png"),
    (b"\xff\xd8\xff", "image", "image/jpeg"),
    (b"GIF8", "image", "image/gif"),
    (b"SQLite format 3\x00", "binary", "application/vnd.sqlite3"),
    (b"\x7fELF", "binary", "application/x-executable"),
    (b"\xcf\xfa\xed\xfe", "binary", "application/x-mach-binary"),
    (b"\xca\xfe\xba\xbe", "binary", "application/x-mach-binary"),
    (b"\x1f\x8b", "archive", "application/gzip"),
    (b"Rar!", "archive", "application/vnd.rar"),
    (b"7z\xbc\xaf\x27\x1c", "archive", "application/x-7z-compressed"),
]


@dataclass(frozen=True)
class Kind:
    kind: str  # text, code, markdown, json, yaml, csv, notebook, pdf, docx, xlsx, image, archive, binary, pptx
    mime: str
    language: str | None = None
    note: str | None = None  # shown to the model, e.g. extension/content mismatch

    @property
    def extractable(self) -> bool:
        return self.kind in {"text", "code", "markdown", "json", "yaml", "csv", "notebook", "pdf", "docx", "xlsx"}


def _zip_kind(data: bytes) -> Kind:
    try:
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    except zipfile.BadZipFile:
        return Kind("archive", "application/zip", note="Looks like a zip but is corrupt.")
    if "word/document.xml" in names:
        return Kind("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    if "xl/workbook.xml" in names:
        return Kind("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if "ppt/presentation.xml" in names:
        return Kind("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation")
    return Kind("archive", "application/zip")


def _looks_like_text(header: bytes) -> bool:
    if b"\x00" in header[:8192]:
        return header[:2] in (b"\xff\xfe", b"\xfe\xff")  # UTF-16 BOM
    sample = header[:8192]
    if not sample:
        return True
    printable = sum(1 for b in sample if b in (9, 10, 13) or 32 <= b < 127 or b >= 128)
    return printable / len(sample) > 0.95


def sniff(data: bytes, path: Path) -> Kind:
    ext = path.suffix.lower()
    header = data[:8192]
    if header.startswith(b"PK\x03\x04"):
        found = _zip_kind(data)
    else:
        found = None
        for magic, kind, mime in MAGIC:
            if header.startswith(magic):
                found = Kind(kind, mime)
                break
        if found is None and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            found = Kind("image", "image/webp")
    if found is not None:
        expected = {"pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx"}.get(found.kind)
        if expected and ext and ext != expected:
            return Kind(
                found.kind,
                found.mime,
                note=f"File extension is '{ext}' but the content is {found.kind}. Treated as {found.kind}.",
            )
        return found

    if not _looks_like_text(header):
        return Kind("binary", "application/octet-stream", note="Binary content with no known format.")
    if ext in {".md", ".markdown", ".mdx"}:
        return Kind("markdown", "text/markdown", "markdown")
    if ext in {".json", ".jsonl", ".geojson"}:
        return Kind("json", "application/json", "json")
    if ext in {".yaml", ".yml"}:
        return Kind("yaml", "application/yaml", "yaml")
    if ext in {".csv", ".tsv"}:
        return Kind("csv", "text/tab-separated-values" if ext == ".tsv" else "text/csv")
    if ext == ".ipynb":
        return Kind("notebook", "application/x-ipynb+json", "json")
    if ext in LANGUAGES:
        return Kind("code", "text/plain", LANGUAGES[ext])
    if path.name.lower() in {"dockerfile", "makefile"}:
        return Kind("code", "text/plain", path.name.lower())
    if header.startswith(b"#!"):
        return Kind("code", "text/plain", "bash")
    if ext in {".docx", ".xlsx", ".pdf"}:
        return Kind(
            "text", "text/plain", note=f"Extension is '{ext}' but the content is plain text, not a real {ext[1:]} file."
        )
    return Kind("text", "text/plain")
