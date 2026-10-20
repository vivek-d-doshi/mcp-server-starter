"""Note storage. Pure Python, no MCP imports, so it is easy to test on its own.

Durability: writes go to a temp file and are renamed into place, so a crash mid-write cannot leave a half-written file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, Field

MAX_TITLE = 200
MAX_BODY = 10_000
MAX_TAGS = 10
MAX_TAG_LEN = 40
MAX_NOTES = 5_000


class Note(BaseModel):
    id: str
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    body: str = Field(min_length=1, max_length=MAX_BODY)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)


class NoteNotFoundError(KeyError):
    pass


class StoreFullError(RuntimeError):
    pass


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _load_sample() -> list[Note]:
    raw = resources.files("notes_mcp").joinpath("sample_notes.json").read_text(encoding="utf-8")
    return [Note.model_validate(n) for n in json.loads(raw)]


class NoteStore:
    """In-memory notes, optionally persisted to a JSON file.

    With no path, notes live only in memory: the bundled samples are loaded and anything you add is lost on exit.
    With a path, that file is the source of truth (created from the samples if missing).
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        if path is not None and path.exists():
            self._notes = [Note.model_validate(n) for n in json.loads(path.read_text(encoding="utf-8"))]
        else:
            self._notes = _load_sample()
            if path is not None:
                self._write()

    @classmethod
    def from_env(cls) -> NoteStore:
        value = os.environ.get("NOTES_FILE")
        return cls(Path(value).expanduser() if value else None)

    def get(self, note_id: str) -> Note:
        for note in self._notes:
            if note.id == note_id:
                return note
        raise NoteNotFoundError(note_id)

    def search(self, query: str, limit: int = 5) -> list[Note]:
        """Rank by how many query words appear; title and tag hits count more than body hits."""
        words = set(_tokens(query))
        if not words:
            return []
        scored: list[tuple[int, int, Note]] = []
        for index, note in enumerate(self._notes):
            title, body, tags = (
                set(_tokens(note.title)),
                set(_tokens(note.body)),
                set(_tokens(" ".join(note.tags))),
            )
            score = 3 * len(words & title) + 3 * len(words & tags) + len(words & body)
            if score:
                scored.append((-score, index, note))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [note for _, _, note in scored[:limit]]

    def add(self, title: str, body: str, tags: list[str] | None = None) -> Note:
        with self._lock:
            if len(self._notes) >= MAX_NOTES:
                raise StoreFullError(f"Store is full ({MAX_NOTES} notes)")
            clean_tags = [t.strip().lower()[:MAX_TAG_LEN] for t in (tags or []) if t.strip()]
            note = Note(id=f"n{self._next_number()}", title=title.strip(), body=body.strip(), tags=clean_tags)
            self._notes.append(note)
            self._write()
            return note

    def _next_number(self) -> int:
        numbers = [int(n.id[1:]) for n in self._notes if re.fullmatch(r"n\d+", n.id)]
        return max(numbers, default=0) + 1

    def _write(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([n.model_dump() for n in self._notes], indent=2)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
