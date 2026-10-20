"""Attack tests for the security boundary. If any of these fail, nothing else matters."""

import os
import stat

import pytest

from filelens.config import ConfigError, build_settings
from filelens.sandbox import AccessError, Sandbox, denied_reason


def code_of(sb, raw):
    """Run the full resolve + read path and return the error code, or 'ok'."""
    try:
        real = sb.resolve(raw)
        sb.read_file(real)
        return "ok"
    except AccessError as exc:
        return exc.code


# ---- normal use -------------------------------------------------------------------------------------------------
def test_reads_allowed_file_absolute_and_relative(sandbox, tree):
    _, root, _ = tree
    assert code_of(sandbox, str(root / "hello.txt")) == "ok"
    assert code_of(sandbox, "hello.txt") == "ok"  # relative = inside first root
    assert code_of(sandbox, "sub/deep.md") == "ok"


def test_missing_file_inside_root_is_not_found(sandbox):
    assert code_of(sandbox, "nope.txt") == "not_found"


# ---- traversal and symlinks -------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw", ["../outside/secret.txt", "sub/../../outside/secret.txt", "./../outside/secret.txt"])
def test_dotdot_cannot_escape(sandbox, raw):
    assert code_of(sandbox, raw) == "outside_allowed"


def test_absolute_outside_path(sandbox, tree):
    _, _, outside = tree
    assert code_of(sandbox, str(outside / "secret.txt")) == "outside_allowed"


def test_file_symlink_pointing_outside(sandbox, tree):
    _, root, outside = tree
    (root / "link.txt").symlink_to(outside / "secret.txt")
    assert code_of(sandbox, "link.txt") == "outside_allowed"


def test_folder_symlink_pointing_outside(sandbox, tree):
    _, root, outside = tree
    (root / "linkdir").symlink_to(outside, target_is_directory=True)
    assert code_of(sandbox, "linkdir/secret.txt") == "outside_allowed"


def test_symlink_chain_pointing_outside(sandbox, tree):
    _, root, outside = tree
    (root / "a").symlink_to(root / "b")
    (root / "b").symlink_to(outside / "secret.txt")
    assert code_of(sandbox, "a") == "outside_allowed"


def test_symlink_inside_root_to_inside_root_is_fine(sandbox, tree):
    _, root, _ = tree
    (root / "alias.txt").symlink_to(root / "hello.txt")
    assert code_of(sandbox, "alias.txt") == "ok"


def test_unicode_lookalike_dots_are_just_a_name(sandbox, tree):
    _, _, outside = tree
    assert code_of(sandbox, "‥/outside/secret.txt") == "not_found"  # TWO DOT LEADER is not ".."


@pytest.mark.parametrize("raw", ["", "   ", "a\x00b", "x" * 5000])
def test_invalid_paths(sandbox, raw):
    assert code_of(sandbox, raw) == "invalid_path"


# ---- secrets are denied even inside an allowed root -------------------------------------------------------------
@pytest.mark.parametrize(
    "rel",
    [".env", ".ENV", ".env.production", "id_rsa", "id_ed25519.pub", "server.pem", "tls.key", "prod.tfvars",
     ".ssh/config", ".SSH/known_hosts", ".aws/credentials", ".git/config", ".npmrc", "terraform.tfstate",
     "creds/.netrc", ".kube/config", "secrets.yaml"],
)  # fmt: skip
def test_secret_files_denied_inside_root(sandbox, tree, rel):
    _, root, _ = tree
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    assert code_of(sandbox, rel) == "denied_secret"


def test_env_example_is_allowed(sandbox, tree):
    _, root, _ = tree
    (root / ".env.example").write_text("KEY=changeme")
    assert code_of(sandbox, ".env.example") == "ok"


def test_denied_even_when_not_existing_so_no_existence_oracle(sandbox):
    assert code_of(sandbox, ".env") == "denied_secret"


def test_deny_wins_over_explicit_allow_file(tree):
    _, root, _ = tree
    secret = root / ".env"
    secret.write_text("TOKEN=abc")
    sb = Sandbox(build_settings([], files=[str(secret)]))
    assert code_of(sb, str(secret)) == "denied_secret"


def test_extra_deny_patterns(tree):
    _, root, _ = tree
    (root / "payroll.xlsx").write_text("x")
    sb = Sandbox(build_settings([str(root)], extra_deny=["payroll*"]))
    assert code_of(sb, "payroll.xlsx") == "denied_secret"


def test_denied_reason_unit():
    from pathlib import Path

    assert denied_reason(Path("/home/u/.ssh/id_rsa"))
    assert denied_reason(Path("/home/u/project/README.md")) is None


# ---- file types and size ----------------------------------------------------------------------------------------
def test_folder_is_refused(sandbox):
    assert code_of(sandbox, "sub") == "not_regular"


def test_fifo_is_refused_and_does_not_hang(sandbox, tree):
    _, root, _ = tree
    os.mkfifo(root / "pipe")
    assert code_of(sandbox, "pipe") == "not_regular"


def test_device_file_is_refused_even_if_granted(sandbox):
    from pathlib import Path

    dev = Path("/dev/zero")
    sandbox.grant_file(dev)
    with pytest.raises(AccessError) as exc:
        sandbox.read_file(dev)
    assert exc.value.code == "not_regular"
    assert stat.S_ISCHR(os.stat(dev).st_mode)


def test_too_large(tree):
    _, root, _ = tree
    (root / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    sb = Sandbox(build_settings([str(root)], max_file_mb=1))
    assert code_of(sb, "big.bin") == "too_large"


# ---- races: the file changes between the check and the open ------------------------------------------------------
def test_final_component_swapped_for_symlink(sandbox, tree):
    _, root, outside = tree
    real = sandbox.resolve("hello.txt")  # passes the check
    (root / "hello.txt").unlink()
    (root / "hello.txt").symlink_to(outside / "secret.txt")  # attacker swaps it
    with pytest.raises(AccessError) as exc:
        sandbox.read_file(real)
    assert exc.value.code == "unreadable"  # O_NOFOLLOW refuses it


def test_parent_folder_swapped_for_symlink(sandbox, tree):
    _, root, outside = tree
    (root / "dir").mkdir()
    (root / "dir" / "secret.txt").write_text("harmless")
    (outside / "secret.txt").write_text("TOP SECRET")
    real = sandbox.resolve("dir/secret.txt")  # passes the check
    (root / "dir" / "secret.txt").unlink()
    (root / "dir").rmdir()
    (root / "dir").symlink_to(outside, target_is_directory=True)  # parent folder swapped for a link out
    with pytest.raises(AccessError) as exc:
        sandbox.read_file(real)
    assert exc.value.code == "changed_during_open"  # caught by the re-check on the open descriptor


# ---- human approval grants --------------------------------------------------------------------------------------
def test_grant_allows_one_file_only(sandbox, tree):
    _, _, outside = tree
    (outside / "other.txt").write_text("other")
    real = sandbox.resolve_outside(str(outside / "secret.txt"))
    sandbox.grant_file(real)
    assert code_of(sandbox, str(outside / "secret.txt")) == "ok"
    assert code_of(sandbox, str(outside / "other.txt")) == "outside_allowed"


def test_resolve_outside_still_rejects_secrets(sandbox, tree):
    _, _, outside = tree
    with pytest.raises(AccessError) as exc:
        sandbox.resolve_outside(str(outside / ".env"))
    assert exc.value.code == "denied_secret"


# ---- configuration fails closed ---------------------------------------------------------------------------------
def test_no_roots_is_an_error():
    with pytest.raises(ConfigError):
        build_settings([])


@pytest.mark.parametrize("raw", ["/", "~"])
def test_broad_roots_rejected(raw):
    with pytest.raises(ConfigError):
        build_settings([raw])


def test_broad_root_needs_explicit_flag():
    assert build_settings(["~"], allow_broad_root=True).roots


def test_missing_root_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        build_settings([str(tmp_path / "nope")])


# ---- audit ------------------------------------------------------------------------------------------------------
def test_audit_log_has_decisions_but_no_contents(tree):
    _, root, _ = tree
    log = root.parent / "audit.jsonl"
    sb = Sandbox(build_settings([str(root)], audit_log=str(log)))
    sb.log("fetch_file", "hello.txt", "allowed", resolved=root / "hello.txt")
    sb.log("fetch_file", "../x", "denied", reason="outside")
    lines = log.read_text().splitlines()
    assert len(lines) == 2 and '"decision": "allowed"' in lines[0]
    assert "hello\nworld" not in log.read_text()
