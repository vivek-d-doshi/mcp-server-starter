import json

import pytest

from notes_mcp.store import MAX_NOTES, NoteNotFoundError, NoteStore, StoreFullError


def test_loads_samples_and_creates_file(tmp_path):
    path = tmp_path / "notes.json"
    store = NoteStore(path)
    assert path.exists()
    assert store.get("n1").title.startswith("Why evals")


def test_in_memory_when_no_path():
    store = NoteStore(None)
    added = store.add("Temp", "Not persisted")
    assert store.get(added.id).title == "Temp"


def test_search_ranks_title_and_tag_above_body():
    store = NoteStore(None)
    results = store.search("evals")
    assert [n.id for n in results][:2] == ["n1", "n5"]  # tagged and titled "evals"


def test_search_empty_and_no_match():
    store = NoteStore(None)
    assert store.search("   ") == []
    assert store.search("zzzxqv") == []


def test_search_limit():
    assert len(NoteStore(None).search("the", limit=2)) <= 2


def test_get_missing():
    with pytest.raises(NoteNotFoundError):
        NoteStore(None).get("nope")


def test_add_persists_and_reloads(tmp_path):
    path = tmp_path / "notes.json"
    note = NoteStore(path).add("  Hello  ", " body ", [" A ", "", "b"])
    assert (note.title, note.body, note.tags) == ("Hello", "body", ["a", "b"])
    reloaded = NoteStore(path)
    assert reloaded.get(note.id).title == "Hello"


def test_ids_do_not_collide_after_reload(tmp_path):
    path = tmp_path / "notes.json"
    first = NoteStore(path).add("A", "a")
    second = NoteStore(path).add("B", "b")
    assert first.id != second.id


def test_write_is_atomic_no_tmp_left(tmp_path):
    path = tmp_path / "notes.json"
    NoteStore(path).add("A", "a")
    assert [p.name for p in tmp_path.iterdir()] == ["notes.json"]
    assert isinstance(json.loads(path.read_text()), list)


def test_store_full(tmp_path):
    store = NoteStore(None)
    store._notes = store._notes * (MAX_NOTES // len(store._notes) + 1)
    with pytest.raises(StoreFullError):
        store.add("x", "y")
