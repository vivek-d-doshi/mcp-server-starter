"""The MCP server: 3 tools, 1 resource, 1 prompt. Tool signatures are the schema the model sees."""

from __future__ import annotations

from typing import Annotated

from mcp import MCPError
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp_types import INVALID_PARAMS
from pydantic import Field

from notes_mcp.store import (
    MAX_BODY,
    MAX_TAG_LEN,
    MAX_TAGS,
    MAX_TITLE,
    Note,
    NoteNotFoundError,
    NoteStore,
    StoreFullError,
)


def _as_text(note: Note) -> str:
    tags = ", ".join(note.tags) or "none"
    return f"# {note.title}\n\n{note.body}\n\nTags: {tags}\nId: {note.id}"


def create_server(store: NoteStore | None = None) -> MCPServer:
    """Build a server around a store. Passing a store makes tests deterministic."""
    store = store or NoteStore.from_env()
    mcp = MCPServer("notes-mcp")

    @mcp.tool()
    def search_notes(
        query: Annotated[str, Field(min_length=1, max_length=200, description="Words to look for")],
        limit: Annotated[int, Field(ge=1, le=20, description="Maximum results")] = 5,
    ) -> list[Note]:
        """Search notes by keyword. Title and tag matches rank above body matches."""
        return store.search(query, limit)

    @mcp.tool()
    def get_note(
        note_id: Annotated[str, Field(min_length=1, max_length=20, description="Id such as 'n1'")],
    ) -> Note:
        """Fetch one note by id."""
        try:
            return store.get(note_id)
        except NoteNotFoundError:
            # ToolError = anticipated failure: the model sees the message. Any other exception is a crash and is hidden.
            raise ToolError(f"No note with id '{note_id}'. Use search_notes to find ids.") from None

    @mcp.tool()
    def add_note(
        title: Annotated[str, Field(min_length=1, max_length=MAX_TITLE)],
        body: Annotated[str, Field(min_length=1, max_length=MAX_BODY)],
        tags: Annotated[list[Annotated[str, Field(max_length=MAX_TAG_LEN)]], Field(max_length=MAX_TAGS)] = [],  # noqa: B006
    ) -> Note:
        """Add a note. Persisted only if the server was started with NOTES_FILE set."""
        try:
            return store.add(title, body, tags)
        except StoreFullError as exc:
            raise ToolError(str(exc)) from None

    @mcp.resource("notes://{note_id}")
    def note_resource(note_id: str) -> str:
        """A note rendered as Markdown."""
        try:
            return _as_text(store.get(note_id))
        except NoteNotFoundError:
            raise ResourceNotFoundError(f"No note with id '{note_id}'") from None

    @mcp.prompt()
    def summarize_note(note_id: str) -> str:
        """Ask the model to summarize one note in two sentences."""
        try:
            note = store.get(note_id)
        except NoteNotFoundError:
            # Prompts have no ToolError equivalent; a protocol error with INVALID_PARAMS keeps the message visible.
            raise MCPError(INVALID_PARAMS, f"No note with id '{note_id}'") from None
        return f"Summarize this note in two sentences for a busy engineer.\n\n{_as_text(note)}"

    return mcp
