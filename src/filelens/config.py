"""Settings: which folders and files the AI may read, and the limits that apply."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MB = 1024 * 1024


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    roots: tuple[Path, ...]
    files: tuple[Path, ...] = ()
    extra_deny: tuple[str, ...] = ()
    max_file_bytes: int = 25 * MB
    max_output_chars: int = 40_000
    max_pdf_pages: int = 100
    max_sheet_rows: int = 50
    search_timeout_s: float = 10.0
    extract_timeout_s: float = 30.0
    audit_log: Path | None = None
    session_grants: set[Path] = field(default_factory=set, compare=False)
    session_search_dirs: set[Path] = field(default_factory=set, compare=False)


def _real(p: str | Path) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(p))))


def build_settings(
    roots: list[str],
    files: list[str] | None = None,
    *,
    extra_deny: list[str] | None = None,
    max_file_mb: int = 25,
    audit_log: str | None = None,
    allow_broad_root: bool = False,
) -> Settings:
    """Validate and normalise user input. Fails closed: a bad root is an error, never silently widened."""
    if not roots and not files:
        raise ConfigError("Give at least one --root folder or --allow-file. FileLens reads nothing by default.")
    home = _real("~")
    resolved_roots: list[Path] = []
    for raw in roots:
        root = _real(raw)
        if not root.is_dir():
            raise ConfigError(f"Root is not a folder: {raw}")
        if not allow_broad_root and (root == Path(root.anchor) or root == home or root in home.parents):
            raise ConfigError(
                f"Root {root} is too broad (the whole disk or your entire home folder). "
                "Pick a narrower folder, or pass --allow-broad-root if you really mean it."
            )
        resolved_roots.append(root)
    resolved_files: list[Path] = []
    for raw in files or []:
        f = _real(raw)
        if not f.is_file():
            raise ConfigError(f"Allowed file does not exist or is not a regular file: {raw}")
        resolved_files.append(f)
    if not 1 <= max_file_mb <= 200:
        raise ConfigError("--max-file-mb must be between 1 and 200")
    return Settings(
        roots=tuple(dict.fromkeys(resolved_roots)),
        files=tuple(dict.fromkeys(resolved_files)),
        extra_deny=tuple(extra_deny or ()),
        max_file_bytes=max_file_mb * MB,
        audit_log=Path(audit_log).expanduser() if audit_log else None,
    )
