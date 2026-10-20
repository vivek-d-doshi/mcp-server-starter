# Security model

FileLens gives an AI read access to files. The design assumes the AI, and the files it reads, may be hostile.

## Assets and attackers

| | |
|---|---|
| **Protect** | Files outside the folders you allowed. Secrets (keys, tokens, `.env`, cloud credentials) anywhere. Your machine's stability. |
| **Attacker 1** | A model that has been manipulated (prompt injection) into asking for things it should not. |
| **Attacker 2** | A malicious file inside an allowed folder, crafted to confuse the model or the parser. |
| **Attacker 3** | Someone able to create files or links inside an allowed folder (a symlink, a swap). |
| **Out of scope** | An attacker who already controls your account or the machine; a malicious MCP client; physical access. |

## Controls

| Threat | Control | Where | Test |
|---|---|---|---|
| Path traversal (`..`, absolute paths) | `realpath` first, then compare to resolved roots | `Sandbox.resolve` | `test_dotdot_cannot_escape`, `test_absolute_outside_path` |
| Symlink escape (file, folder, chain) | Same check on the resolved path | `Sandbox.resolve` | `test_file_symlink_pointing_outside`, `test_folder_symlink_pointing_outside`, `test_symlink_chain_pointing_outside` |
| Check-then-open race | `O_NOFOLLOW`, then re-verify the path of the open descriptor (`F_GETPATH` / `/proc`) | `Sandbox.read_file` | `test_final_component_swapped_for_symlink`, `test_parent_folder_swapped_for_symlink` |
| Secrets inside an allowed folder | Hard deny-list; deny always beats allow, including human approval | `denied_reason` | `test_secret_files_denied_inside_root`, `test_deny_wins_over_explicit_allow_file`, `test_human_approval_never_unlocks_secrets` |
| Existence oracle outside the folders | Identical response whether or not the path exists | `server.outside` | `test_outside_paths_leak_nothing_about_existence` |
| Devices, FIFOs, folders | Regular files only; opened non-blocking | `Sandbox.read_file` | `test_fifo_is_refused_and_does_not_hang`, `test_device_file_is_refused_even_if_granted` |
| Resource exhaustion | File size cap, output cap, page cap, search depth/count/time limits, extraction timeout | `config`, `search`, `server.run_blocking` | `test_too_large`, `test_output_cap_truncates_with_hint` |
| Zip bombs | Uncompressed size and ratio check before parsing | `extract.documents._check_zip` | `test_zip_bomb_is_refused` |
| Unsafe deserialisation | YAML `safe_load` only; nothing is executed or imported from files | `extract.textual` | `test_yaml_cannot_build_python_objects` |
| ReDoS | Text search is fixed-string, never a model-supplied regex | `search.grep` | by design |
| Self-approval by the model | Approval is a client-side human prompt; unreachable client means refuse | `server.confirm` | `test_traversal_without_a_human_is_refused`, `test_newest_protocol_has_no_back_channel_so_it_fails_safe` |
| Approval widening | A one-file approval covers that file only; a names-only search approval never allows content search | `Sandbox.grant_file`, `find_files` | `test_grant_allows_one_file_only`, `test_find_outside_names_needs_approval_and_content_never` |
| Forged content boundary | Random per-call nonce, re-rolled if it appears in the content | `render.render_reference` | `test_reference_boundary_cannot_be_forged_by_file_contents` |
| Misconfiguration | Fails closed: no roots, missing folder, or `/` / home as a root is an error | `config.build_settings` | `test_broad_roots_rejected`, `test_cli_refuses_broad_roots_and_missing_folders` |
| Accountability | One audit line per decision, never file contents | `Sandbox.log` | `test_audit_log_records_decisions_without_contents` |

The sandbox tests were validated by mutation: each defence was removed in turn and the suite was confirmed to fail.

## Known limitations (not defended)

- **Prompt injection cannot be fully prevented.** FileLens reduces it (data labelling, unforgeable boundary) and bounds the damage (the model can read only what you allowed, never secrets).
- **Hard links.** A file hard-linked into an allowed folder looks like a normal file there. Someone who can already create hard links in your folder can expose that file's contents.
- **Parser vulnerabilities.** pypdf, python-docx and openpyxl parse untrusted bytes. A bug in them is a bug in FileLens. Keep them updated and, for untrusted inputs, run in a container.
- **Weak clients.** An MCP client that auto-approves permission questions defeats the human check for files outside the folders. It still cannot unlock secrets.
- **Case-insensitive file systems (macOS default).** Scope checks are case-sensitive and fail closed: a path typed with the wrong case may be refused. Use the case shown by `list_allowed`.
- **Metadata inside allowed folders.** File names and sizes in allowed folders are visible to the model by design.
- **Platforms.** macOS and Linux only. Windows is not supported.
- **Extraction timeouts** abandon the worker thread; they do not kill it.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private "Report a vulnerability" on the repository, or message the maintainer on LinkedIn.
