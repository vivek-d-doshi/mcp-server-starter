"""Type detection and extraction, using real generated files (PDF, Word, Excel), not mocks."""

import io
import json
import zipfile

import pytest

from conftest import make_docx, make_pdf, make_xlsx
from filelens.config import build_settings
from filelens.core import load
from filelens.extract import ExtractError
from filelens.sandbox import Sandbox
from filelens.sniff import sniff


@pytest.fixture
def box(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    return root, Sandbox(build_settings([str(root)]))


def read(box, name, data, **kw):
    root, sb = box
    f = root / name
    f.write_bytes(data if isinstance(data, bytes) else data.encode())
    return load(sb, sb.resolve(str(f)), **kw)


def test_plain_text_with_line_numbers_and_window(box):
    out = read(box, "a.txt", "one\ntwo\nthree\nfour\n", start_line=2, max_lines=2, number=True)
    assert out.kind.kind == "text"
    assert out.extraction.text == "    2| two\n    3| three"
    assert out.extraction.truncated and "start_line=4" in out.extraction.continue_hint


def test_code_language_from_extension(box):
    assert read(box, "x.py", "print(1)\n").kind.language == "python"
    assert read(box, "x.ts", "let a = 1\n").kind.language == "typescript"


def test_shebang_script_is_code(box):
    assert read(box, "run", "#!/bin/sh\necho hi\n").kind.kind == "code"


def test_markdown_json_yaml(box):
    assert read(box, "r.md", "# T\n").kind.kind == "markdown"
    js = read(box, "d.json", json.dumps({"a": 1, "b": [1, 2, 3]}))
    assert "object with 2 keys" in js.extraction.meta["structure"]
    ym = read(box, "c.yaml", "name: x\nitems:\n  - 1\n  - 2\n")
    assert "1 document" in ym.extraction.meta["structure"]


def test_invalid_json_and_yaml_are_warnings_not_crashes(box):
    assert any("Invalid JSON" in w for w in read(box, "bad.json", "{oops").extraction.warnings)
    assert any("Invalid YAML" in w for w in read(box, "bad.yaml", "a: [unclosed").extraction.warnings)


def test_yaml_cannot_build_python_objects(box):
    evil = "x: !!python/object/apply:os.system ['echo pwned']\n"
    out = read(box, "evil.yaml", evil)  # safe_load must refuse the tag, not execute it
    assert any("Invalid YAML" in w for w in out.extraction.warnings)


def test_csv_table_and_counts(box):
    out = read(box, "t.csv", "name,age\nA,1\nB,2\nC|x,3\n")
    assert out.kind.kind == "csv" and out.extraction.meta["rows"] == "3 data rows"
    assert "| name | age |" in out.extraction.text and "C\\|x" in out.extraction.text


def test_notebook(box):
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Hi"]},
            {"cell_type": "code", "source": ["1+1"], "outputs": [{"text": ["2"]}]},
        ]
    }
    out = read(box, "n.ipynb", json.dumps(nb))
    assert out.kind.kind == "notebook" and "[output of cell 2]" in out.extraction.text


def test_pdf_pages_and_metadata(box):
    out = read(box, "d.pdf", make_pdf(["First page text", "Second page text"]))
    assert out.kind.kind == "pdf" and out.extraction.units_total == 2
    assert "[page 2]" in out.extraction.text and "Second page text" in out.extraction.text


def test_pdf_without_text_warns_about_scans(box):
    out = read(box, "blank.pdf", make_pdf(["", ""]))
    assert any("No extractable text" in w for w in out.extraction.warnings)


def test_encrypted_pdf_is_refused_politely(box):
    with pytest.raises(ExtractError, match="password"):
        read(box, "locked.pdf", make_pdf(["secret"], password="pw"))


def test_corrupt_pdf(box):
    with pytest.raises(ExtractError):
        read(box, "bad.pdf", b"%PDF-1.4\nnot really a pdf")


def test_docx_headings_lists_tables(box):
    out = read(box, "d.docx", make_docx())
    text = out.extraction.text
    assert out.kind.kind == "docx" and "# Title" in text and "- bullet one" in text and "| Name | Qty |" in text


def test_xlsx_sheets(box):
    out = read(box, "s.xlsx", make_xlsx())
    assert out.kind.kind == "xlsx" and "[sheet Sales" in out.extraction.text and "| Item | Price |" in out.extraction.text


def test_zip_bomb_is_refused(box):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * (300 * 1024 * 1024))
    with pytest.raises(ExtractError, match="unsafe size"):
        read(box, "bomb.docx", buf.getvalue())


def test_extension_lies_content_wins(box):
    out = read(box, "report.txt", make_pdf(["real pdf"]))
    assert out.kind.kind == "pdf" and any("extension is '.txt'" in w for w in out.extraction.warnings)
    fake = read(box, "fake.pdf", "just text, not a pdf\n")
    assert fake.kind.kind == "text" and any("not a real pdf" in w for w in fake.extraction.warnings)


def test_images_and_binaries_return_metadata_only(box):
    png = read(box, "p.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    assert png.kind.kind == "image" and png.extraction.text == "" and png.extraction.warnings
    assert read(box, "x.bin", bytes(range(256)) * 4).kind.kind == "binary"


def test_encodings(box):
    assert "héllo" in read(box, "u8.txt", "héllo".encode()).extraction.text
    assert any("Latin-1" in w for w in read(box, "l1.txt", b"caf\xe9\n").extraction.warnings)
    assert "hi" in read(box, "u16.txt", "hi\n".encode("utf-16")).extraction.text


def test_empty_file(box):
    assert read(box, "empty.txt", b"").extraction.text == ""


def test_output_cap_truncates_with_hint(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    (root / "big.txt").write_text("\n".join(f"line {i}" for i in range(5000)))
    sb = Sandbox(build_settings([str(root)]))
    out = load(sb, sb.resolve("big.txt"), number=True)
    assert out.extraction.truncated and "start_line=" in out.extraction.continue_hint
    assert len(out.extraction.text) <= sb.s.max_output_chars


def test_sniff_unit():
    from pathlib import Path

    assert sniff(b"%PDF-1.7", Path("x.bin")).kind == "pdf"
    assert sniff(b"hello", Path("x.unknown")).kind == "text"
