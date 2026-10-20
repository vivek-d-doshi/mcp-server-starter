"""Riskiest part: does it work as a real subprocess over stdio and HTTP, the way a real client runs it?"""

import socket
import subprocess
import sys
import time

import pytest
from mcp import Client, StdioServerParameters


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_stdio_subprocess_round_trip(tmp_path):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "notes_mcp"], env={"NOTES_FILE": str(tmp_path / "n.json")}
    )
    async with Client(params) as client:
        result = await client.call_tool("search_notes", {"query": "rollout"})
        assert result.structured_content["result"][0]["id"] == "n5"
        await client.call_tool("add_note", {"title": "From stdio", "body": "persisted"})
    assert "From stdio" in (tmp_path / "n.json").read_text()


async def test_streamable_http_round_trip(tmp_path):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "notes_mcp", "--transport", "streamable-http", "--port", str(port)],
        env={"NOTES_FILE": str(tmp_path / "n.json"), "PATH": ""},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            if proc.poll() is not None:
                pytest.fail(f"server exited early: {proc.stderr.read().decode()[:500]}")
            time.sleep(0.2)
        else:
            pytest.fail("server did not start")
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            result = await client.call_tool("get_note", {"note_id": "n2"})
            assert not result.is_error
    finally:
        proc.terminate()
        proc.wait(timeout=10)
