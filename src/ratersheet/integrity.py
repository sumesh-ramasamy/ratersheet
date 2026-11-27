"""Hashes used to commit to the key and to detect edited texts."""

from __future__ import annotations

import hashlib
import os
import unicodedata

_CHUNK_BYTES = 1 << 16


def normalize_text(s: str) -> str:
    """Return ``s`` in the form that is hashed.

    Spreadsheet programs may change how a text is stored without changing what
    a rater reads: composed versus decomposed accents, Windows versus Unix line
    endings, surrounding blank space. Normalising these away keeps such
    harmless differences from being reported as an edited text.
    """
    composed = unicodedata.normalize("NFC", s)
    return composed.replace("\r\n", "\n").replace("\r", "\n").strip()


def text_sha256(s: str) -> str:
    """Return the lowercase hex SHA-256 of ``normalize_text(s)`` as UTF-8."""
    return hashlib.sha256(normalize_text(s).encode("utf-8")).hexdigest()


def file_sha256(path: str | os.PathLike[str]) -> str:
    """Return the lowercase hex SHA-256 of the bytes of the file at ``path``."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
