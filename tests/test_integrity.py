"""normalize_text, text_sha256, file_sha256."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

import pytest

from ratersheet import file_sha256, normalize_text, text_sha256


def test_normalize_nfc() -> None:
    decomposed = "café"
    assert normalize_text(decomposed) == "café"
    assert unicodedata.is_normalized("NFC", normalize_text(decomposed))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a\r\nb", "a\nb"),
        ("a\rb", "a\nb"),
        ("a\nb", "a\nb"),
        ("a\r\n\r\nb", "a\n\nb"),
        ("a\r\rb", "a\n\nb"),
        ("a\r\nb\rc\nd", "a\nb\nc\nd"),
    ],
)
def test_normalize_line_endings(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  hello  ", "hello"),
        ("\n\nhello\n", "hello"),
        ("\t hello \r\n", "hello"),
        ("  two  words  ", "two  words"),  # interior whitespace is kept
        ("a \n b", "a \n b"),
    ],
)
def test_normalize_strips_ends_only(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_normalize_empty_and_blank() -> None:
    assert normalize_text("") == ""
    assert normalize_text(" \r\n\t ") == ""


def test_normalize_idempotent() -> None:
    s = "  é line1\r\nline2\r  "
    once = normalize_text(s)
    assert normalize_text(once) == once


def test_text_sha256_is_lowercase_hex_of_normalized_utf8() -> None:
    s = "  Café\r\nnaïve  "
    expected = hashlib.sha256(normalize_text(s).encode("utf-8")).hexdigest()
    got = text_sha256(s)
    assert got == expected
    assert re.fullmatch(r"[0-9a-f]{64}", got)


def test_windows_and_unix_line_endings_hash_the_same() -> None:
    assert text_sha256("one\r\ntwo\r\nthree") == text_sha256("one\ntwo\nthree")
    assert text_sha256("one\rtwo") == text_sha256("one\ntwo")


def test_surrounding_whitespace_does_not_change_hash() -> None:
    assert text_sha256("  abc \n") == text_sha256("abc")


def test_nfc_and_nfd_hash_the_same() -> None:
    assert text_sha256("café") == text_sha256("café")


def test_real_edits_change_the_hash() -> None:
    base = text_sha256("The dose is 5 mg.")
    assert text_sha256("The dose is 6 mg.") != base
    assert text_sha256("The  dose is 5 mg.") != base  # interior whitespace counts
    assert text_sha256("the dose is 5 mg.") != base


def test_non_ascii_text_hash() -> None:
    s = "Dosis: 5 mg — 日本語"
    assert text_sha256(s) == hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_file_sha256_hashes_bytes(tmp_path: Path) -> None:
    p = tmp_path / "f.bin"
    data = b"\x00\x01line\r\nendings stay\r\n" + bytes(range(256))
    p.write_bytes(data)
    assert file_sha256(p) == hashlib.sha256(data).hexdigest()
    assert file_sha256(str(p)) == hashlib.sha256(data).hexdigest()


def test_file_sha256_changes_with_one_byte(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    p.write_bytes(b"abc")
    before = file_sha256(p)
    p.write_bytes(b"abd")
    assert file_sha256(p) != before
