"""Verify the key commitment, and link the blinded ratings back to their sources.

Unblinding is refused unless the last collection check passed, and it is
recorded, so that the methods text can say when the key was first used and
whether collection was complete at that moment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from ._store import (
    COLLECT_REPORT_NAME,
    COMMENTS_BLINDED_NAME,
    COMMENTS_NAME,
    RATINGS_BLINDED_NAME,
    RATINGS_NAME,
    UNBLIND_RECORD_NAME,
    load_key,
    load_manifest,
    read_csv,
    read_json,
    utc_now,
    write_csv,
    write_json,
)
from .collect import COMMENTS_BLINDED_HEADER, RATINGS_BLINDED_HEADER
from .errors import IntegrityError, RatersheetError
from .integrity import file_sha256
from .workbook import read_workbook

RATINGS_HEADER = (
    "item_id",
    "case_id",
    "source",
    "rater_id",
    "scale",
    "value",
    "item_code",
    "rating_id",
    "position",
)
COMMENTS_HEADER = ("item_id", "case_id", "source", "rater_id", "rating_id", "comment")
_RUN_ENTRIES = ("unblinded_at", "complete", "n_expected", "n_received", "collect_run_id")
# The blinded files, each with the report entry that holds the hash of the
# file as the collection check wrote it.
_BLINDED_HASH_ENTRIES = (
    (RATINGS_BLINDED_NAME, "ratings_blinded_sha256"),
    (COMMENTS_BLINDED_NAME, "comments_blinded_sha256"),
)


@dataclass(frozen=True)
class UnblindResult:
    """Where the unblinded files are, and whether collection was complete."""

    ratings_path: Path
    comments_path: Path | None
    record_path: Path
    complete: bool


def verify_key(study_dir: str | os.PathLike[str]) -> str:
    """Check ``private/key.json`` against the manifest and return its SHA-256.

    Raises ``IntegrityError`` if the key is not the one the manifest commits to.
    """
    manifest = load_manifest(study_dir)
    load_key(study_dir, manifest)
    return manifest["key_sha256"]


def verify_sheet(study_dir: str | os.PathLike[str], sheet_path: str | os.PathLike[str]) -> bool:
    """Return True if the commitment embedded in the sheet is this study's."""
    manifest = load_manifest(study_dir)
    return read_workbook(sheet_path).meta["key_sha256"] == manifest["key_sha256"]


def unblind(
    study_dir: str | os.PathLike[str],
    *,
    allow_incomplete: bool = False,
    unblinded_at: str | None = None,
) -> UnblindResult:
    """Write ``ratings.csv`` (and ``comments.csv``) with item ids and sources."""
    study_path = Path(study_dir)
    manifest = load_manifest(study_path)
    key = load_key(study_path, manifest)
    report = _checked_report(study_path, manifest, allow_incomplete)

    item_of = {entry["item_id"]: entry for entry in key["items"]}
    item_of_rating = {
        entry["rating_id"]: item_of[entry["item_id"]] for entry in key["assignments"]
    }

    # Every row is joined before anything is written, so a blinded file that
    # does not match the key leaves no partial output behind.
    blinded_path = study_path / RATINGS_BLINDED_NAME
    rating_rows = [
        _identity(row, item_of_rating, blinded_path)
        + (row["rater_id"], row["scale"], row["value"])
        + (row["item_code"], row["rating_id"], row["position"])
        for row in read_csv(blinded_path, "the blinded ratings", RATINGS_BLINDED_HEADER)
    ]
    blinded_comments_path = study_path / COMMENTS_BLINDED_NAME
    comment_rows = None
    if blinded_comments_path.exists():
        comment_rows = [
            _identity(row, item_of_rating, blinded_comments_path)
            + (row["rater_id"], row["rating_id"], row["comment"])
            for row in read_csv(
                blinded_comments_path, "the blinded comments", COMMENTS_BLINDED_HEADER
            )
        ]

    # The record is read, checked and written before any source reaches the
    # disk: it is the only evidence of when the key was first used, so there
    # must never be an unblinded file that the record does not know about.
    record_path = study_path / UNBLIND_RECORD_NAME
    this_run = {
        "unblinded_at": unblinded_at if unblinded_at is not None else utc_now(),
        "complete": report["complete"],
        "n_expected": report["n_expected"],
        "n_received": report["n_received"],
        # Which collection check this unblinding followed. Kept in "first", it
        # lets the methods text say whether the sheets were checked again
        # after the key was first used, which clock times cannot settle when
        # two steps fall in the same second. None for a report without one.
        "collect_run_id": report.get("run_id"),
    }
    first, earlier_runs = _earlier_unblinding(record_path, manifest)
    write_json(
        record_path,
        {
            **this_run,
            "tool_version": __version__,
            "key_sha256": manifest["key_sha256"],
            "first": first if first is not None else this_run,
            "times_unblinded": earlier_runs + 1,
        },
    )
    ratings_path = study_path / RATINGS_NAME
    write_csv(ratings_path, RATINGS_HEADER, rating_rows)
    comments_path = None
    if comment_rows is not None:
        comments_path = study_path / COMMENTS_NAME
        write_csv(comments_path, COMMENTS_HEADER, comment_rows)
    return UnblindResult(
        ratings_path=ratings_path,
        comments_path=comments_path,
        record_path=record_path,
        complete=report["complete"],
    )


def _earlier_unblinding(record_path: Path, manifest: dict) -> tuple[dict | None, int]:
    """Return the first unblinding on record for this key, and how many runs there were.

    What matters for a blinded study is the first time the key was used, so a
    later run must not overwrite that. A record written for another key
    belongs to an earlier build and is ignored.
    """
    if not record_path.exists():
        return None, 0
    earlier = read_json(record_path, "the unblinding record")
    if earlier.get("key_sha256") != manifest["key_sha256"]:
        return None, 0
    first = earlier.get("first")
    if not isinstance(first, dict):
        first = {name: earlier.get(name) for name in _RUN_ENTRIES}
    times = earlier.get("times_unblinded")
    return first, times if isinstance(times, int) and times > 0 else 1


def _checked_report(study_path: Path, manifest: dict, allow_incomplete: bool) -> dict:
    """Return the collect report if it permits unblinding, else say why not."""
    report_path = study_path / COLLECT_REPORT_NAME
    if not report_path.exists():
        raise RatersheetError(
            f"{report_path} does not exist; run 'ratersheet collect' on the returned sheets "
            "before unblinding"
        )
    report = read_json(report_path, "the collect report")
    for entry in ("ok", "complete", "key_sha256", "n_expected", "n_received"):
        if entry not in report:
            raise RatersheetError(
                f"{report_path} is missing the entry {entry!r}; run 'ratersheet collect' again"
            )
    if report["key_sha256"] != manifest["key_sha256"]:
        raise IntegrityError(
            f"{report_path} was written for a different key than the one in the manifest; "
            "run 'ratersheet collect' again for this study"
        )
    if report["ok"] is not True:
        raise RatersheetError(
            f"the last collection check found errors (see {report_path}); "
            "resolve them and run 'ratersheet collect' again before unblinding"
        )
    if not (study_path / RATINGS_BLINDED_NAME).exists():
        raise RatersheetError(
            f"{study_path / RATINGS_BLINDED_NAME} does not exist; run 'ratersheet collect' again"
        )
    _require_collected_files(study_path, report)
    if report["complete"] is not True and not allow_incomplete:
        raise RatersheetError(
            f"collection is incomplete: {_what_is_missing(report)}. Collect what is missing "
            "first, or pass allow_incomplete (--allow-incomplete) to unblind anyway; the record "
            "will say it was incomplete."
        )
    return report


def _require_collected_files(study_path: Path, report: dict) -> None:
    """Refuse blinded files that are not the ones the last collection check wrote.

    The report says the ratings were received and checked; that is only true
    of the bytes the check wrote. A file shortened or edited since then would
    otherwise be unblinded, and described in the methods text, as checked.
    A report without the hashes (written before they existed) is not checked.
    """
    for name, entry in _BLINDED_HASH_ENTRIES:
        expected = report.get(entry)
        if not isinstance(expected, str):
            continue
        path = study_path / name
        if not path.is_file():
            raise IntegrityError(
                f"{path} is missing, although the last collection check wrote it; "
                "run 'ratersheet collect' again"
            )
        if file_sha256(path) != expected:
            raise IntegrityError(
                f"{path} is not the file the last collection check wrote: it has been "
                "changed since. Run 'ratersheet collect' again; the ratings are taken "
                "from the returned sheets, not from this file."
            )


def _what_is_missing(report: dict) -> str:
    """Say what an incomplete collection still lacks: ratings, whole sheets, or both.

    The count of ratings alone can read "0 of 0" (no scale is required) or be
    full while a rater's sheet is still outstanding, so the raters without a
    sheet are named from the report's own ``missing_workbook`` problems.
    """
    problems = report.get("problems")
    without_sheet = [
        str(problem.get("rater_id"))
        for problem in (problems if isinstance(problems, list) else [])
        if isinstance(problem, dict) and problem.get("code") == "missing_workbook"
    ]
    parts = []
    if report["n_received"] != report["n_expected"] or not without_sheet:
        parts.append(
            f"{report['n_received']} of {report['n_expected']} expected ratings were received"
        )
    if without_sheet:
        parts.append(f"no sheet has been returned by {', '.join(without_sheet)}")
    return " and ".join(parts)


def _identity(
    row: dict[str, str], item_of_rating: dict[str, dict], source_path: Path
) -> tuple[str, str, str]:
    """Return (item_id, case_id, source) for one blinded row.

    The row is looked up by its rating id and its item code is compared with
    the key, so a blinded file that belongs to another study is refused
    instead of being joined to the wrong items.
    """
    item = item_of_rating.get(row["rating_id"])
    if item is None or item["item_code"] != row["item_code"]:
        raise IntegrityError(
            f"{source_path} does not match the key: rating id {row['rating_id']!r} with item "
            f"code {row['item_code']!r} is not in the key. Run 'ratersheet collect' again."
        )
    return (item["item_id"], item["case_id"] or "", item["source"])
