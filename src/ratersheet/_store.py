"""The files of a study folder: their names, and how they are read and written.

Kept in one place so that every module agrees on where things live and so that
the key is only ever used after it has been checked against the manifest.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from .errors import IntegrityError, RatersheetError

MANIFEST_NAME = "manifest.json"
SHEETS_DIR = "sheets"
PRIVATE_DIR = "private"
KEY_NAME = "key.json"
LEAK_REPORT_NAME = "leak_report.json"
PRIVATE_README_NAME = "README.txt"
COLLECT_REPORT_NAME = "collect_report.json"
RATINGS_BLINDED_NAME = "ratings_blinded.csv"
COMMENTS_BLINDED_NAME = "comments_blinded.csv"
RATINGS_NAME = "ratings.csv"
COMMENTS_NAME = "comments.csv"
UNBLIND_RECORD_NAME = "unblind_record.json"
METHODS_NAME = "methods.md"

FORMAT_VERSION = 1

_MANIFEST_KEYS = (
    "tool_version",
    "created",
    "title",
    "n_items",
    "n_sources",
    "raters",
    "raters_per_item",
    "group_by_case",
    "comments",
    "has_context",
    "scales",
    "per_rater",
    "n_assignments",
    "key_sha256",
    "sheets",
)
_KEY_KEYS = ("title", "seed", "nonce", "sources", "items", "assignments")


def utc_now() -> str:
    """Return the current time as ISO 8601 UTC to the second."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open_owner_only(path: str, flags: int) -> int:
    """Open ``path`` so that a file created by the call is readable by its owner only."""
    return os.open(path, flags, 0o600)


def write_text(path: Path, text: str, *, private: bool = False) -> None:
    """Write ``text`` as UTF-8 with ``\\n`` newlines on every platform.

    ``private`` creates the file readable and writable by the owner only, for
    files that could unblind the study. It has no effect on Windows, and none
    on a file that already exists. ``open`` still chooses the flags, so the
    bytes written are the same with and without it.
    """
    opener = _open_owner_only if private else None
    with open(path, "w", encoding="utf-8", newline="\n", opener=opener) as handle:
        handle.write(text)


def write_json(path: Path, obj: object, *, private: bool = False) -> None:
    """Write ``obj`` as JSON in the one layout the tool uses everywhere.

    A fixed layout matters for ``key.json``: its hash is the commitment, so
    the bytes must not depend on dictionary order or platform. ``private`` is
    passed on to ``write_text``.
    """
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    write_text(path, text, private=private)


def parse_json_object(data: bytes, path: Path) -> dict:
    """Parse ``data`` as a JSON object, or explain what is wrong with ``path``."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise RatersheetError(f"{path} is not valid JSON ({exc})") from None
    if not isinstance(obj, dict):
        raise RatersheetError(f"{path} does not contain a JSON object")
    return obj


def read_bytes(path: Path, what: str) -> bytes:
    """Read a file the tool needs, naming what it is if it cannot be read."""
    try:
        return path.read_bytes()
    except OSError as exc:
        reason = "it does not exist" if isinstance(exc, FileNotFoundError) else str(exc)
        raise RatersheetError(f"cannot read {what} {path}: {reason}") from None


def read_json(path: Path, what: str) -> dict:
    """Read a JSON object from ``path``."""
    return parse_json_object(read_bytes(path, what), path)


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    """Write a CSV file as UTF-8 with ``\\n`` line endings."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def read_csv(path: Path, what: str, header: Sequence[str]) -> list[dict[str, str]]:
    """Read a CSV file written by the tool and check that it has ``header``."""
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            found = reader.fieldnames
    except FileNotFoundError:
        raise RatersheetError(f"cannot read {what} {path}: it does not exist") from None
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise RatersheetError(f"cannot read {what} {path}: {exc}") from None
    if found is None or list(found) != list(header):
        raise RatersheetError(
            f"{path} does not have the expected header {','.join(header)}; "
            "it was not written by this version of ratersheet or has been edited"
        )
    return rows


def _require_keys(obj: dict, keys: Sequence[str], path: Path) -> None:
    """Fail clearly, instead of with a KeyError later, if a file was hand-edited."""
    if obj.get("format_version") != FORMAT_VERSION:
        raise RatersheetError(
            f"{path} has format_version {obj.get('format_version')!r}; "
            f"this version of ratersheet reads format_version {FORMAT_VERSION}"
        )
    for key in keys:
        if key not in obj:
            raise RatersheetError(f"{path} is missing the entry {key!r}; it may have been edited")


def manifest_path(study_dir: str | os.PathLike[str]) -> Path:
    """Return where the manifest of a study folder lives."""
    return Path(study_dir) / MANIFEST_NAME


def key_path(study_dir: str | os.PathLike[str]) -> Path:
    """Return where the unblinding key of a study folder lives."""
    return Path(study_dir) / PRIVATE_DIR / KEY_NAME


def load_manifest(study_dir: str | os.PathLike[str]) -> dict:
    """Read ``manifest.json`` from a study folder."""
    path = manifest_path(study_dir)
    if not path.is_file():
        raise RatersheetError(
            f"{Path(study_dir)} is not a ratersheet study folder: it has no {MANIFEST_NAME} "
            "(pass the folder that 'ratersheet build' wrote)"
        )
    manifest = read_json(path, "the manifest")
    _require_keys(manifest, _MANIFEST_KEYS, path)
    return manifest


def load_key(study_dir: str | os.PathLike[str], manifest: dict) -> dict:
    """Read ``private/key.json`` and refuse it unless it matches the manifest.

    The bytes that are hashed are the bytes that are parsed, so the key that
    is used is exactly the key that was checked.
    """
    path = key_path(study_dir)
    data = read_bytes(path, "the unblinding key")
    actual = hashlib.sha256(data).hexdigest()
    expected = manifest["key_sha256"]
    if actual != expected:
        raise IntegrityError(
            f"{path} does not match the commitment in {manifest_path(study_dir)}: "
            f"the manifest records SHA-256 {expected} but the key file hashes to {actual}. "
            "The key or the manifest has been changed since the study was built."
        )
    key = parse_json_object(data, path)
    _require_keys(key, _KEY_KEYS, path)
    return key
