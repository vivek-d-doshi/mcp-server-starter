"""The real thing: FileLens as a subprocess, driven over stdio and HTTP, plus the command-line safety checks."""

import os
import socket
import subprocess
import sys
import time

import pytest
from mcp import Client, StdioServerParameters


def stdio(*args):
    return StdioServerParameters(
        command=sys.executable, args=["-m", "filelens", *args], env={"FILELENS_LOG_LEVEL": "WARNING"}
    )


@pytest.fixture
def folders(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "a.txt").write_text("hello from the sandbox\n")
    (outside / "secret.txt").write_text("nope")
    (root / "link").symlink_to(outside / "secret.txt")
    return root, outside


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_stdio_subprocess_reads_inside_and_blocks_outside(folders, mode):
    root, outside = folders
    async with Client(stdio("--root", str(root)), mode=mode) as client:
        ok = await client.call_tool("refer_file", {"path": "a.txt"})
        assert "hello from the sandbox" in ok.content[0].text
        for attack in ("../outside/secret.txt", str(outside / "secret.txt"), "link"):
            r = await client.call_tool("fetch_file", {"path": attack})
            assert r.is_error, attack
            assert "nope" not in r.content[0].text


async def test_audit_log_records_decisions_without_contents(folders, tmp_path):
    root, _ = folders
    log = tmp_path / "audit.jsonl"
    async with Client(stdio("--root", str(root), "--audit-log", str(log))) as client:
        await client.call_tool("fetch_file", {"path": "a.txt"})
        await client.call_tool("fetch_file", {"path": "../outside/secret.txt"})
    text = log.read_text()
    assert '"decision": "allowed"' in text and '"decision": "denied"' in text
    assert "hello from the sandbox" not in text


def run_cli(*args, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, "-m", "filelens", *args], capture_output=True, text=True, timeout=20, cwd=cwd, env=env
    )


def test_cli_refuses_broad_roots_and_missing_folders(tmp_path):
    for bad in ("/", os.path.expanduser("~"), str(tmp_path / "nope")):
        r = run_cli("--root", bad)
        assert r.returncode == 2 and "filelens:" in r.stderr, bad
        assert r.stdout == ""  # stdout is the protocol channel and must stay clean


def test_cli_with_no_roots_defaults_to_current_folder_and_says_so(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-m", "filelens"],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(1.5)
        proc.stdin.close()
        err = proc.stderr.read()
    finally:
        proc.kill()
        proc.wait()
    assert "using the current folder" in err


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_streamable_http_round_trip(folders):
    root, _ = folders
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "filelens", "--root", str(root), "--transport", "streamable-http", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )  # fmt: skip
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            if proc.poll() is not None:
                pytest.fail(f"server exited early: {proc.stderr.read().decode()[:400]}")
            time.sleep(0.2)
        else:
            pytest.fail("server did not start")
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            ok = await client.call_tool("fetch_file", {"path": "a.txt"})
            assert ok.structured_content["status"] == "ok"
            assert (await client.call_tool("fetch_file", {"path": "../outside/secret.txt"})).is_error
    finally:
        proc.terminate()
        proc.wait(timeout=10)
