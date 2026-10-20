"""Minimal client: starts the server over stdio and calls each tool. Run: python examples/client.py"""

import asyncio
import sys

from mcp import Client, StdioServerParameters


async def main() -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "notes_mcp"],
        env={"NOTES_LOG_LEVEL": "WARNING"},  # quiet the server log
    )
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
        print("tools:", ", ".join(t.name for t in tools))

        found = await client.call_tool("search_notes", {"query": "evals", "limit": 2})
        for note in found.structured_content["result"]:
            print(f"- {note['id']}: {note['title']}")

        note = await client.call_tool("get_note", {"note_id": "n1"})
        print("\nget_note n1 ->", note.structured_content["title"])

        bad = await client.call_tool("get_note", {"note_id": "nope"})
        print("unknown id  ->", "expected error:" if bad.is_error else "ok:", bad.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
