"""The security boundary. Everything that touches the disk goes through here.

Rules (see SECURITY.md for the threat model):
  1. Read-only. There is no write, move, delete or execute code path anywhere in FileLens.
  2. A path is only readable if its fully resolved real path (symlinks followed) is inside an allowed root,
     or is a single file the user allowed. `..` and symlinks cannot escape.
  3. Secret-looking files are denied even inside an allowed root. Deny always wins over allow.
  4. After opening, the file is checked again from the open file descriptor, so a swap between check and open
     (a race) cannot smuggle a different file in.
  5. Only regular files, under a size cap. No devices, pipes, sockets or directories.

POSIX only (macOS, Linux): the descriptor re-check uses F_GETPATH or /proc.
"""

from __future__ import annotations

import fcntl
import fnmatch
import json
import logging
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

from filelens.config import Settings

audit = logging.getLogger("filelens.audit")

DENY_DIRS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".password-store", "keychains", ".docker"}
DENY_NAMES = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.kdbx",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials",
    "credentials", "credentials.json", "secrets.json", "secrets.yml", "secrets.yaml",
    "terraform.tfstate", "terraform.tfstate.backup", "*.tfvars", "login data", "cookies", "web data",
]  # fmt: skip
DENY_ALLOW_EXCEPTIONS = {".env.example", ".env.sample", ".env.template"}
DENY_SUFFIXES = [
    ".git/config", ".config/gcloud", ".config/gh/hosts.yml", "library/keychains", "library/cookies",
    "library/application support/google/chrome", "library/application support/firefox",
]  # fmt: skip
NOISE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".tox", ".cache"}


class AccessError(Exception):
    """A refused or failed access. `code` is stable and machine-readable; `message` is safe to show the model."""

    def __init__(self, code: str, message: str, path: Path | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path


def denied_reason(path: Path, extra: tuple[str, ...] = ()) -> str | None:
    """Return why a path looks like a secret, or None. Case-insensitive; checks every path component."""
    parts = [p.lower() for p in path.parts]
    name = parts[-1] if parts else ""
    if name in DENY_ALLOW_EXCEPTIONS:
        return None
    for part in parts[:-1] + [name]:
        if part in DENY_DIRS:
            return f"inside a credentials folder ({part})"
    for pattern in [*DENY_NAMES, *(p.lower() for p in extra)]:
        if fnmatch.fnmatch(name, pattern):
            return f"matches the secret-file pattern '{pattern}'"
    joined = "/".join(parts)
    for suffix in DENY_SUFFIXES:
        if joined.endswith(suffix) or f"/{suffix}/" in joined or joined.endswith("/" + suffix):
            return f"is a known credentials location ({suffix})"
    return None


def _fd_path(fd: int) -> Path:
    """The real path of an open file, as the kernel sees it right now."""
    if hasattr(fcntl, "F_GETPATH"):  # macOS
        raw = fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024)
        return Path(os.path.realpath(os.fsdecode(raw.split(b"\0", 1)[0])))
    link = f"/proc/self/fd/{fd}"  # Linux
    if os.path.exists("/proc/self/fd"):
        return Path(os.path.realpath(os.readlink(link)))
    raise AccessError("unsupported_platform", "Cannot verify the open file on this platform, so access is refused.")


class Sandbox:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    # ---- scope -------------------------------------------------------------------------------------------------
    def in_scope(self, real: Path) -> bool:
        if real in self.s.files or real in self.s.session_grants:
            return True
        return any(real == root or real.is_relative_to(root) for root in self.s.roots)

    def search_scope(self) -> list[Path]:
        return [*self.s.roots, *self.s.session_search_dirs]

    def grant_file(self, real: Path) -> None:
        """A human approved this one file for this session. Deny-list still applies at read time."""
        self.s.session_grants.add(real)

    def grant_search_dir(self, real: Path) -> None:
        self.s.session_search_dirs.add(real)

    # ---- path resolution ---------------------------------------------------------------------------------------
    def resolve(self, raw: str) -> Path:
        """Turn what the model typed into a real path, or raise AccessError. Does not touch file contents."""
        if not isinstance(raw, str) or not raw.strip() or "\x00" in raw or len(raw) > 4096:
            raise AccessError("invalid_path", "The path is empty, too long, or contains invalid characters.")
        candidate = Path(os.path.expanduser(raw.strip()))
        if not candidate.is_absolute():
            base = self.s.roots[0] if self.s.roots else self.s.files[0].parent
            candidate = base / candidate
        real = Path(os.path.realpath(candidate))  # follows symlinks, collapses ..
        for p in (Path(os.path.abspath(candidate)), real):
            why = denied_reason(p, self.s.extra_deny)
            if why:
                raise AccessError("denied_secret", f"Access refused: this path {why}. FileLens never reads secrets.", real)
        if not self.in_scope(real):
            raise AccessError(
                "outside_allowed",
                f"{real} is outside the folders FileLens may read. The user must allow it (add --root or --allow-file).",
                real,
            )
        return real

    def resolve_outside(self, raw: str) -> Path:
        """Like resolve() but returns paths outside scope too (still rejects secrets). Used to ask the human."""
        try:
            return self.resolve(raw)
        except AccessError as exc:
            if exc.code == "outside_allowed" and exc.path is not None:
                return exc.path
            raise

    # ---- opening -----------------------------------------------------------------------------------------------
    def read_file(self, real: Path) -> tuple[bytes, os.stat_result, Path]:
        """Open without following a final symlink, verify from the descriptor, then read at most max_file_bytes."""
        try:
            fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except FileNotFoundError:
            raise AccessError("not_found", f"No such file: {real}", real) from None
        except IsADirectoryError:
            raise AccessError(
                "not_regular", f"{real} is a folder, not a file. Use find_files to look inside it.", real
            ) from None
        except OSError as exc:  # ELOOP (symlink swap), EACCES, ...
            raise AccessError("unreadable", f"Cannot open {real}: {exc.strerror}", real) from None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                kind = "a folder" if stat.S_ISDIR(st.st_mode) else "not a regular file (device, pipe or socket)"
                raise AccessError("not_regular", f"{real} is {kind}.", real)
            actual = _fd_path(fd)
            why = denied_reason(actual, self.s.extra_deny)
            if why:
                raise AccessError("denied_secret", f"Access refused: this path {why}.", actual)
            if not self.in_scope(actual):
                raise AccessError(
                    "changed_during_open", "The file changed while it was being opened. Access refused.", actual
                )
            if st.st_size > self.s.max_file_bytes:
                raise AccessError(
                    "too_large",
                    f"File is {st.st_size:,} bytes; the limit is {self.s.max_file_bytes:,}. Ask the user to raise --max-file-mb.",
                    actual,
                )
            with os.fdopen(fd, "rb", closefd=False) as handle:
                data = handle.read(self.s.max_file_bytes + 1)
            if len(data) > self.s.max_file_bytes:
                raise AccessError("too_large", "File grew past the size limit while reading.", actual)
            return data, st, actual
        finally:
            os.close(fd)

    # ---- audit -------------------------------------------------------------------------------------------------
    def log(self, tool: str, requested: str, decision: str, *, resolved: Path | None = None, reason: str = "") -> None:
        """One line per access decision. Never logs file contents."""
        event = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "tool": tool,
            "requested": requested[:300],
            "resolved": str(resolved) if resolved else None,
            "decision": decision,
            "reason": reason,
        }
        audit.info("%s", json.dumps(event, ensure_ascii=False))
        if self.s.audit_log:
            try:
                self.s.audit_log.parent.mkdir(parents=True, exist_ok=True)
                with open(self.s.audit_log, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError:
                audit.warning("could not write audit log %s", self.s.audit_log)


def now_ms() -> float:
    return time.monotonic() * 1000
