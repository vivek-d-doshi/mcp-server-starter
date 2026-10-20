# notes-mcp

A small MCP server in Python: **3 typed tools, 1 resource, 1 prompt**, tested through a real MCP client, over stdio and HTTP.
It is a starter you can read in ten minutes and copy.

| | |
|---|---|
| Tools | `search_notes(query, limit)` · `get_note(note_id)` · `add_note(title, body, tags)` |
| Resource | `notes://{note_id}` (a note as Markdown) |
| Prompt | `summarize_note(note_id)` |
| Transports | stdio (default), streamable HTTP |
| SDK | official [`mcp`](https://github.com/modelcontextprotocol/python-sdk) v2 (`MCPServer`, `Client`) |

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                      # 21 tests
python examples/client.py      # starts the server over stdio and calls each tool
```

Real output of the example client:

```
tools: search_notes, get_note, add_note
- n1: Why evals come before prompts
- n5: Staged rollouts for prompt changes

get_note n2 -> MCP tools should be small and typed
unknown id  -> error: Error executing tool get_note: No note with id 'nope'. Use search_notes to find ids.
```

## Use it from Claude Code

```bash
claude mcp add notes -- /absolute/path/to/.venv/bin/python -m notes_mcp
```

Then ask: "search my notes for evals". To keep what `add_note` writes, add `-e NOTES_FILE=~/notes.json`.
Without `NOTES_FILE` the server loads the bundled samples in memory and forgets additions on exit.

HTTP instead of stdio: `python -m notes_mcp --transport streamable-http --port 8000` (endpoint `/mcp`, binds to `127.0.0.1`).

## Design choices worth reading

- **Narrow, typed tools.** Argument limits live in the signature (`limit` 1–20, body ≤ 10,000 chars, ≤ 10 tags), so the SDK
  turns them into the JSON Schema the model sees and rejects bad input before your code runs.
- **Anticipated errors are visible, crashes are not.** Raising `ToolError` shows the model a useful message
  ("use `search_notes` to find ids"). Any other exception is treated as a crash: the model sees only
  `Error executing tool <name>` and the traceback stays in the server log. Resources use `ResourceNotFoundError`;
  prompts use an `MCPError` with `INVALID_PARAMS`.
- **stdout belongs to the protocol.** Over stdio, logging goes to stderr only. A stray `print()` would corrupt the stream.
- **Storage is separate from MCP** (`store.py` has no MCP imports), so it is unit-tested on its own.
  Writes are atomic (temp file + rename), IDs survive reloads, and the store has a size cap.
- **The riskiest part is tested for real.** `tests/test_transports.py` launches the server as a subprocess and talks to it
  over stdio and over HTTP, the way an actual client does. No LLM is needed, so CI is deterministic.

## Layout

```
src/notes_mcp/   server.py (MCP surface) · store.py (notes) · __main__.py (CLI) · sample_notes.json
tests/           store, server (in-memory client), transports (real subprocess)
examples/        client.py
Dockerfile       non-root; stdio by default
.github/         CI: ruff + pytest on Python 3.11 to 3.13
```

## Not included (on purpose)

Authentication and OAuth, remote hosting, a real database, rate limiting. Add them when a real deployment needs them;
none are required to learn the protocol surface. If you expose HTTP beyond localhost, put auth in front of it first.
