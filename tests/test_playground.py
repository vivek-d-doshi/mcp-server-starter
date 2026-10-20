"""The Ollama playground, tested against a stub Ollama server (NOT a real model). Proves our loop, not model quality."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from mcp import Client, StdioServerParameters

from filelens import playground


class StubOllama:
    """First reply asks for a tool; the second reply answers using what the tool returned."""

    def __init__(self, tool_args):
        self.requests, self.tool_args = [], tool_args
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append(body)
                last = body["messages"][-1]
                if last["role"] == "tool":
                    msg = {"role": "assistant", "content": f"I read it. Tool said: {last['content'][:3000]}"}
                else:
                    msg = {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"type": "function", "function": {"index": 0, "name": "refer_file", "arguments": stub.tool_args}}
                        ],
                    }
                data = json.dumps({"message": msg}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def folders(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "a.txt").write_text("the answer is 42\n")
    (outside / "x.txt").write_text("outside data")
    return root, outside


def server_params(root):
    import sys

    return StdioServerParameters(
        command=sys.executable, args=["-m", "filelens", "--root", str(root)], env={"FILELENS_LOG_LEVEL": "WARNING"}
    )


async def run_question(root, stub, ask):
    async with Client(server_params(root), mode="legacy", elicitation_callback=playground.terminal_approval(ask)) as client:
        tools = playground.to_ollama_tools((await client.list_tools()).tools)
        msgs = [{"role": "system", "content": playground.SYSTEM}, {"role": "user", "content": "what is in the file?"}]
        out = await playground.answer(client, stub.url, "stub-model", msgs, tools, lambda s: None)
        return out, tools, msgs


async def test_tool_schema_is_valid_ollama_format_and_loop_completes(folders):
    root, _ = folders
    stub = StubOllama({"path": "a.txt"})
    try:
        out, tools, msgs = await run_question(root, stub, lambda prompt: "n")
    finally:
        stub.close()
    assert {t["function"]["name"] for t in tools} == {"fetch_file", "refer_file", "find_files", "list_allowed"}
    assert all(t["type"] == "function" and t["function"]["parameters"]["type"] == "object" for t in tools)
    assert "the answer is 42" in out  # the model's second turn saw the real file through FileLens
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert tool_msg["tool_name"] == "refer_file" and "FILELENS REFERENCE" in tool_msg["content"]


async def test_you_are_asked_in_the_terminal_for_outside_files(folders):
    root, outside = folders
    asked = []

    def ask_yes(prompt):
        asked.append(prompt)
        return "y"

    stub = StubOllama({"path": str(outside / "x.txt")})
    try:
        out, _, _ = await run_question(root, stub, ask_yes)
    finally:
        stub.close()
    assert asked and "outside data" in out

    stub = StubOllama({"path": str(outside / "x.txt")})
    try:
        out, _, _ = await run_question(root, stub, lambda p: "n")
    finally:
        stub.close()
    assert "outside data" not in out and "declined" in out


def test_unreachable_ollama_is_a_clear_message():
    with pytest.raises(SystemExit, match="Cannot reach Ollama"):
        playground.ollama_chat("http://127.0.0.1:9", {"model": "x", "messages": []}, timeout=2)
