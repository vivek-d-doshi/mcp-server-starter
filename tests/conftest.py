import pytest

from notes_mcp.server import create_server
from notes_mcp.store import NoteStore


@pytest.fixture
def store(tmp_path):
    return NoteStore(tmp_path / "notes.json")


@pytest.fixture
def server(store):
    return create_server(store)
