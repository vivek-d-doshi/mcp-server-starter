"""Drive the server through a real MCP client, in memory (no subprocess, no LLM)."""

from mcp import Client


async def test_lists_expected_surface(server):
    async with Client(server) as client:
        assert {t.name for t in (await client.list_tools()).tools} == {"search_notes", "get_note", "add_note"}
        assert {p.name for p in (await client.list_prompts()).prompts} == {"summarize_note"}


async def test_tool_schema_is_typed(server):
    async with Client(server) as client:
        tool = next(t for t in (await client.list_tools()).tools if t.name == "search_notes")
        props = tool.input_schema["properties"]
        assert props["limit"]["maximum"] == 20
        assert tool.input_schema["required"] == ["query"]


async def test_search_returns_structured_notes(server):
    async with Client(server) as client:
        result = await client.call_tool("search_notes", {"query": "mcp tools"})
        assert not result.is_error
        assert result.structured_content["result"][0]["id"] == "n2"


async def test_get_note_and_missing(server):
    async with Client(server) as client:
        ok = await client.call_tool("get_note", {"note_id": "n1"})
        assert not ok.is_error and ok.structured_content["id"] == "n1"
        missing = await client.call_tool("get_note", {"note_id": "zzz"})
        assert missing.is_error and "search_notes" in missing.content[0].text


async def test_add_then_search(server):
    async with Client(server) as client:
        added = await client.call_tool(
            "add_note", {"title": "Idempotency keys", "body": "Retries need them.", "tags": ["backend"]}
        )
        assert not added.is_error
        found = await client.call_tool("search_notes", {"query": "idempotency"})
        assert found.structured_content["result"][0]["title"] == "Idempotency keys"


async def test_invalid_arguments_are_rejected_not_crashed(server):
    async with Client(server) as client:
        for args in ({"query": ""}, {"query": "x", "limit": 999}, {"query": "x", "limit": "many"}, {}):
            assert (await client.call_tool("search_notes", args)).is_error
        too_long = await client.call_tool("add_note", {"title": "t", "body": "b" * 10_001})
        assert too_long.is_error


async def test_resource_and_prompt(server):
    async with Client(server) as client:
        resource = await client.read_resource("notes://n3")
        assert "hybrid" in resource.contents[0].text.lower()
        prompt = await client.get_prompt("summarize_note", {"note_id": "n3"})
        assert "two sentences" in prompt.messages[0].content.text


async def test_missing_resource_and_prompt_fail_cleanly(server):
    async with Client(server) as client:
        for call in (
            client.read_resource("notes://nope"),
            client.get_prompt("summarize_note", {"note_id": "nope"}),
        ):
            try:
                await call
            except Exception as exc:  # protocol-level error, not a crash
                assert "No note with id 'nope'" in str(exc)
            else:
                raise AssertionError("expected an error")


async def test_store_full_is_a_clean_tool_error(server, store):
    from notes_mcp.store import MAX_NOTES

    store._notes = store._notes * (MAX_NOTES // len(store._notes) + 1)
    async with Client(server) as client:
        result = await client.call_tool("add_note", {"title": "x", "body": "y"})
        assert result.is_error and "full" in result.content[0].text
