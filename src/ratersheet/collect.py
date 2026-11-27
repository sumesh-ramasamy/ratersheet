"""Validate the workbooks raters returned and write the blinded dataset.

Returned workbooks are untrusted. Every row is matched to its assignment by
its rating id, the texts are compared with the hashes in the key, and every
rating is checked against its scale. A dataset is written only if no error
was found, so a problem cannot slip silently into the analysis.
"""

from __future__ import annotations

import os
import re
import secrets
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__
from ._store import (
    COLLECT_REPORT_NAME,
    COMMENTS_BLINDED_NAME,
    COMMENTS_NAME,
    RATINGS_BLINDED_NAME,
    RATINGS_NAME,
    load_key,
    load_manifest,
    utc_now,
    write_csv,
    write_json,
)
from .config import Scale
from .errors import RatersheetError
from .integrity import file_sha256, text_sha256
from .workbook import ReturnedRow, ReturnedWorkbook, read_workbook, sheet_columns

RATINGS_BLINDED_HEADER = ("item_code", "rater_id", "scale", "value", "rating_id", "position")
COMMENTS_BLINDED_HEADER = ("item_code", "rater_id", "rating_id", "comment")

_INTEGER_RE = re.compile(r"[+-]?[0-9]{1,18}")
_SHA256_HEX_RE = re.compile(r"[0-9A-Fa-f]{64}")
# A spreadsheet program treats a CSV field that starts with one of these as a
# formula. Comments are the only free text a rater controls, so they are the
# only place where this matters.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
# Apostrophes a keyboard or a spreadsheet program may put where the study file
# has the plain one.
_APOSTROPHES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'"})
# Folder entries that are never a returned sheet: "~$name.xlsx" is the lock
# file Excel keeps beside an open workbook, and a leading dot marks a hidden
# file, such as the "._name.xlsx" companion macOS writes on non-Mac volumes.
_SKIPPED_NAME_PREFIXES = ("~$", ".")
_BLINDED_NAMES = (RATINGS_BLINDED_NAME, COMMENTS_BLINDED_NAME)
_UNBLINDED_NAMES = (RATINGS_NAME, COMMENTS_NAME)


@dataclass(frozen=True)
class Problem:
    """One thing that is wrong with, or missing from, the returned workbooks."""

    severity: str  # "error" or "warning"
    code: str
    message: str
    file: str | None  # file name (not full path)
    rater_id: str | None
    rating_id: str | None
    scale: str | None


@dataclass(frozen=True)
class CollectResult:
    """The outcome of one ``collect`` run."""

    problems: tuple[Problem, ...]
    n_expected: int  # assignments x required scales
    n_received: int  # of those, how many have a valid value
    # Every required rating was received and every rater who has assignments
    # returned a usable workbook. The second half matters when no scale is
    # required: then no rating is expected, yet sheets can still be outstanding.
    complete: bool
    ok: bool  # no problem of severity "error"
    ratings_path: Path | None
    comments_path: Path | None
    report_path: Path
    # Names of the dataset files this run deleted because they were stale: the
    # unblinded files of an earlier check and, after a failed check, the
    # blinded ones. The caller has to tell the user, who may still count on them.
    removed: tuple[str, ...]
    # How many returned files were examined, usable or not. Zero means that
    # nothing was found to check, which is not the same as nothing being wrong.
    n_files_read: int


@dataclass(frozen=True)
class _Reference:
    """What the study expects, looked up from the manifest and the key."""

    scales: tuple[Scale, ...]
    assignment_of: dict[str, dict]  # rating_id -> assignment entry of the key
    item_of: dict[str, dict]  # item_id -> item entry of the key
    incomplete_severity: str  # severity of the "something is still missing" problems


@dataclass
class _Gathered:
    """What has been accepted from the returned workbooks so far."""

    problems: list[Problem] = field(default_factory=list)
    values: dict[tuple[str, str], int | str] = field(default_factory=dict)  # (rating_id, scale_id)
    comments: dict[str, str] = field(default_factory=dict)  # rating_id -> comment

    def add(
        self,
        code: str,
        message: str,
        *,
        severity: str = "error",
        file: str | None = None,
        rater_id: str | None = None,
        rating_id: str | None = None,
        scale: str | None = None,
    ) -> None:
        self.problems.append(Problem(severity, code, message, file, rater_id, rating_id, scale))


def collect(
    study_dir: str | os.PathLike[str],
    returned: str | os.PathLike[str] | Iterable[str | os.PathLike[str]],
    *,
    require_complete: bool = False,
    created: str | None = None,
) -> CollectResult:
    """Check the returned workbooks against the study in ``study_dir``.

    ``returned`` is a path or several paths; each is a workbook or a folder of
    workbooks. Missing workbooks and empty required ratings are warnings,
    unless ``require_complete`` makes them errors. ``created`` is the time
    written into the report (default: now).
    """
    study_path = Path(study_dir)
    manifest = load_manifest(study_path)
    key = load_key(study_path, manifest)
    reference = _Reference(
        scales=_manifest_scales(manifest),
        assignment_of={entry["rating_id"]: entry for entry in key["assignments"]},
        item_of={entry["item_id"]: entry for entry in key["items"]},
        incomplete_severity="error" if require_complete else "warning",
    )
    gathered = _Gathered()
    files = _returned_files(returned)
    usable, unusable = _sort_workbooks(files, manifest, reference, gathered)

    for rater_id in manifest["raters"]:
        expected = [entry for entry in key["assignments"] if entry["rater_id"] == rater_id]
        if rater_id in usable:
            _check_workbook(usable[rater_id], rater_id, expected, reference, gathered)
        elif expected and rater_id not in unusable:
            gathered.add(
                "missing_workbook",
                f"no workbook was provided for rater {rater_id}",
                severity=reference.incomplete_severity,
                rater_id=rater_id,
            )

    required = [scale.id for scale in reference.scales if scale.required]
    per_rater = {
        rater_id: _completeness(rater_id, key["assignments"], required, gathered)
        for rater_id in manifest["raters"]
    }
    n_expected = sum(counts["expected"] for counts in per_rater.values())
    n_received = sum(counts["received"] for counts in per_rater.values())
    ok = all(problem.severity != "error" for problem in gathered.problems)
    all_returned = all(
        rater_id in usable
        for rater_id in manifest["raters"]
        if any(entry["rater_id"] == rater_id for entry in key["assignments"])
    )
    complete = n_received == n_expected and all_returned

    # The report is what permits unblinding, so it must never describe another
    # dataset than the one beside it. The old report goes first and the new one
    # is written last: a run that fails part-way leaves no report at all.
    report_path = study_path / COLLECT_REPORT_NAME
    report_path.unlink(missing_ok=True)
    ratings_path, comments_path, removed = _write_dataset(
        study_path, manifest, key, gathered, ok
    )
    write_json(
        report_path,
        {
            "created": created if created is not None else utc_now(),
            # Two runs can share a clock second, so the time cannot say whether
            # the sheets were checked again after an unblinding; this can.
            "run_id": secrets.token_hex(8),
            # With these, unblinding can refuse blinded files that are no
            # longer the ones this run checked and wrote.
            "ratings_blinded_sha256": _sha256_if_written(ratings_path),
            "comments_blinded_sha256": _sha256_if_written(comments_path),
            "tool_version": __version__,
            "key_sha256": manifest["key_sha256"],
            "n_expected": n_expected,
            "n_received": n_received,
            "complete": complete,
            "ok": ok,
            "require_complete": require_complete,
            "per_rater": per_rater,
            "problems": [asdict(problem) for problem in gathered.problems],
        },
    )
    return CollectResult(
        problems=tuple(gathered.problems),
        n_expected=n_expected,
        n_received=n_received,
        complete=complete,
        ok=ok,
        ratings_path=ratings_path,
        comments_path=comments_path,
        report_path=report_path,
        removed=removed,
        n_files_read=len(files),
    )


# --- finding and sorting the files ------------------------------------------


def _manifest_scales(manifest: dict) -> tuple[Scale, ...]:
    return tuple(
        Scale(
            id=entry["id"],
            label=entry["label"],
            type=entry["type"],
            values=tuple(entry["values"]),
            description=entry["description"],
            required=entry["required"],
        )
        for entry in manifest["scales"]
    )


def _returned_files(
    returned: str | os.PathLike[str] | Iterable[str | os.PathLike[str]],
) -> list[Path]:
    """Expand ``returned`` into workbook paths, each listed once.

    Folders are scanned, not recursively, for ``*.xlsx``; lock files and
    hidden files are passed over (see ``_SKIPPED_NAME_PREFIXES``). A file that
    is named explicitly is always read, whatever it is called.
    """
    given = [returned] if isinstance(returned, (str, os.PathLike)) else list(returned)
    files: list[Path] = []
    for entry in given:
        path = Path(entry)
        if path.is_dir():
            files.extend(
                sorted(
                    child
                    for child in path.iterdir()
                    if child.is_file()
                    and child.suffix.lower() == ".xlsx"
                    and not child.name.startswith(_SKIPPED_NAME_PREFIXES)
                )
            )
        elif path.is_file():
            files.append(path)
        else:
            raise RatersheetError(f"{path} does not exist; give a returned workbook or a folder")
    unique: dict[Path, Path] = {}
    for path in files:
        unique.setdefault(path.resolve(), path)
    return list(unique.values())


def _show(value: object) -> str:
    """Show an untrusted value safely and briefly in a message."""
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _shown_commitment(value: str) -> str:
    """Show the key commitment a returned sheet carries.

    A real one is printed in full, because the reader has to compare it with
    the commitments of other builds; anything else is untrusted text.
    """
    return value if _SHA256_HEX_RE.fullmatch(value) else _show(value)


def _sort_workbooks(
    files: list[Path], manifest: dict, reference: _Reference, gathered: _Gathered
) -> tuple[dict[str, ReturnedWorkbook], set[str]]:
    """Read the files and return the one usable workbook per rater.

    Also returns the raters whose files were read and reported as unusable, so
    that the caller does not go on to say that they sent nothing: raters whose
    sheet has an altered description, and raters who sent more than one file
    (none of those is used, because the tool cannot know which one the rater
    meant).
    """
    # The column description comes from the manifest, not from the returned
    # file: a sheet whose hidden description was edited could otherwise swap
    # two scales, point the text check at another column or drop the comments.
    study_columns = sheet_columns(
        reference.scales, has_context=manifest["has_context"], comments=manifest["comments"]
    )
    by_rater: dict[str, list[ReturnedWorkbook]] = {}
    unusable: set[str] = set()
    for path in files:
        try:
            workbook = read_workbook(path)
        except RatersheetError as exc:
            gathered.add("not_a_ratersheet", str(exc), file=path.name)
            continue
        meta = workbook.meta
        if meta["key_sha256"] != manifest["key_sha256"]:
            gathered.add(
                "wrong_study",
                f"{path.name}: this sheet belongs to another study or to an earlier build of "
                f"this one (its key commitment is {_shown_commitment(meta['key_sha256'])})",
                file=path.name,
            )
        elif meta["rater_id"] not in manifest["raters"]:
            gathered.add(
                "unknown_rater",
                f"{path.name}: the sheet says it is for rater {_show(meta['rater_id'])}, "
                "who is not a rater of this study",
                file=path.name,
            )
        elif meta["columns"] != study_columns:
            unusable.add(meta["rater_id"])
            gathered.add(
                "not_a_ratersheet",
                f"{path.name}: the columns recorded in the sheet do not match the columns "
                "of this study; the hidden sheet has been altered",
                file=path.name,
                rater_id=meta["rater_id"],
            )
        else:
            by_rater.setdefault(meta["rater_id"], []).append(workbook)

    usable: dict[str, ReturnedWorkbook] = {}
    for rater_id, workbooks in by_rater.items():
        if len(workbooks) == 1:
            usable[rater_id] = workbooks[0]
            continue
        unusable.add(rater_id)
        for extra in workbooks[1:]:
            gathered.add(
                "duplicate_workbook",
                f"{extra.path.name}: a second file for rater {rater_id} (the first is "
                f"{workbooks[0].path.name}); none of this rater's files was used. "
                "Provide exactly one file per rater.",
                file=extra.path.name,
                rater_id=rater_id,
            )
    return usable, unusable


# --- checking one workbook ----------------------------------------------------


def _check_workbook(
    workbook: ReturnedWorkbook,
    rater_id: str,
    expected: list[dict],
    reference: _Reference,
    gathered: _Gathered,
) -> None:
    """Check every row of one rater's workbook and gather what is valid.

    Rows are matched by rating id, not by position, so a rater who sorted or
    reordered the rows has done no harm.
    """
    file = workbook.path.name
    seen: set[str] = set()
    for row in workbook.rows:
        where = f"{file}, row {row.row_number}"
        rating_id = _own_rating_id(row, rater_id, where, file, reference, gathered)
        if rating_id is None:
            continue
        tags = {"file": file, "rater_id": rater_id, "rating_id": rating_id}
        if rating_id in seen:
            gathered.add(
                "duplicate_rating_id",
                f"{where}: the rating id {rating_id} appears more than once in this file; "
                "only its first row was read",
                **tags,
            )
            continue
        seen.add(rating_id)
        where = f"{where} (rating id {rating_id})"
        item = reference.item_of[reference.assignment_of[rating_id]["item_id"]]
        _check_texts(row, item, where, tags, gathered)
        _check_values(row, where, tags, reference, gathered)
        if row.comment is not None:
            gathered.comments[rating_id] = row.comment
    for entry in expected:
        if entry["rating_id"] not in seen:
            gathered.add(
                "missing_row",
                f"{file}: the row with rating id {entry['rating_id']} is missing "
                f"(it was row {entry['position'] + 1} of the sheet that was sent)",
                file=file,
                rater_id=rater_id,
                rating_id=entry["rating_id"],
            )


def _own_rating_id(
    row: ReturnedRow,
    rater_id: str,
    where: str,
    file: str,
    reference: _Reference,
    gathered: _Gathered,
) -> str | None:
    """Return the row's rating id if it is one of this rater's, else report why not."""
    rating_id = row.rating_id
    if rating_id is None:
        gathered.add(
            "unknown_rating_id",
            f"{where} has content but no rating id",
            file=file,
            rater_id=rater_id,
        )
        return None
    assignment = reference.assignment_of.get(rating_id)
    if assignment is None:
        gathered.add(
            "unknown_rating_id",
            f"{where} has the rating id {_show(rating_id)}, which is not part of this study",
            file=file,
            rater_id=rater_id,
            rating_id=rating_id,
        )
        return None
    if assignment["rater_id"] != rater_id:
        gathered.add(
            "wrong_rater",
            f"{where}: the rating id {rating_id} belongs to the sheet of another rater, "
            f"not to {rater_id}",
            file=file,
            rater_id=rater_id,
            rating_id=rating_id,
        )
        return None
    return rating_id


def _check_texts(
    row: ReturnedRow, item: dict, where: str, tags: dict[str, str], gathered: _Gathered
) -> None:
    """Report a text or context that is no longer what was sent to the rater.

    A cell that now holds a formula counts as changed even if the formula
    reads like the original text: it is no longer plain text.
    """
    cells = (
        ("text", row.text, item["text_sha256"]),
        ("context", row.context, item["context_sha256"] or text_sha256("")),
    )
    for name, found, expected_sha256 in cells:
        if name in row.formula_headers:
            reason = f"the {name} cell now holds a formula"
        elif text_sha256(found or "") != expected_sha256:
            reason = f"the {name} is not the {name} that was sent to the rater"
        else:
            continue
        gathered.add(f"{name}_changed", f"{where}: {reason}", **tags)


def _check_values(
    row: ReturnedRow,
    where: str,
    tags: dict[str, str],
    reference: _Reference,
    gathered: _Gathered,
) -> None:
    """Accept the valid ratings of one row and report the others."""
    for scale in reference.scales:
        raw = row.values.get(scale.id)
        allowed = ", ".join(str(value) for value in scale.values)
        if scale.label in row.formula_headers:
            gathered.add(
                "invalid_value",
                f"{where}: {scale.label!r} holds a formula ({_show(raw)}); formulas are never "
                f"evaluated. Enter one of: {allowed}",
                scale=scale.id,
                **tags,
            )
        elif _is_missing(raw):
            if scale.required:
                gathered.add(
                    "missing_value",
                    f"{where}: {scale.label!r} is empty",
                    severity=reference.incomplete_severity,
                    scale=scale.id,
                    **tags,
                )
        else:
            value = _valid_value(scale, raw)
            if value is None:
                gathered.add(
                    "invalid_value",
                    f"{where}: {_show(raw)} is not an allowed value for {scale.label!r}; "
                    f"allowed: {allowed}",
                    scale=scale.id,
                    **tags,
                )
            else:
                gathered.values[(tags["rating_id"], scale.id)] = value


def _is_missing(raw: object) -> bool:
    """An empty cell or a cell with only white space holds no rating."""
    return raw is None or (isinstance(raw, str) and not raw.strip())


def _whole_number(raw: object) -> int | None:
    """Return the integer a cell holds, however the spreadsheet stored it.

    Spreadsheet programs store a typed 4 as the number 4, as 4.0 or, in a cell
    formatted as text, as "4". A boolean is not a number here, although Python
    would treat ``True`` as 1.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() else None
    if isinstance(raw, str) and _INTEGER_RE.fullmatch(raw.strip()):
        return int(raw.strip())
    return None


def _valid_value(scale: Scale, raw: object) -> int | str | None:
    """Return the rating in the scale's own form, or ``None`` if it is not allowed."""
    if scale.type == "ordinal":
        number = _whole_number(raw)
        return number if number in scale.values else None
    if isinstance(raw, str):
        wanted = raw.strip()
    elif _whole_number(raw) is not None:
        # A nominal value that looks like a number ("0", "1") comes back as a
        # number from a spreadsheet program that ignores the text format the
        # nominal cells are built with.
        wanted = str(_whole_number(raw))
    else:
        return None
    for value in scale.values:
        if isinstance(value, str) and value.casefold() == wanted.casefold():
            return value
    # No value has this spelling. Accept a spelling that differs only in ways
    # the rater cannot see or did not choose, but only if it then fits exactly
    # one value, so that the looser comparison can never pick between two.
    close = [
        value
        for value in scale.values
        if isinstance(value, str) and _loose(value) == _loose(wanted)
    ]
    return close[0] if len(close) == 1 else None


def _loose(text: str) -> str:
    """Return ``text`` without the differences a spreadsheet program introduces.

    The text check already ignores composed versus decomposed accents; a
    nominal value typed by a rater deserves the same. Typographic apostrophes
    and runs of white space (a non-breaking space included) are folded as well.
    """
    composed = unicodedata.normalize("NFC", text).translate(_APOSTROPHES)
    return " ".join(composed.split()).casefold()


# --- completeness and outputs -------------------------------------------------


def _completeness(
    rater_id: str, assignments: list[dict], required: list[str], gathered: _Gathered
) -> dict[str, int]:
    """Count the required ratings expected from, and received from, one rater."""
    rating_ids = [entry["rating_id"] for entry in assignments if entry["rater_id"] == rater_id]
    received = sum(
        1
        for rating_id in rating_ids
        for scale_id in required
        if (rating_id, scale_id) in gathered.values
    )
    return {"expected": len(rating_ids) * len(required), "received": received}


def _csv_safe(text: str) -> str:
    """Make free text from a rater safe to open in a spreadsheet program.

    A leading apostrophe is the conventional way to say "this is text": the
    spreadsheet then shows the comment instead of running it as a formula.
    """
    return "'" + text if text.startswith(_FORMULA_TRIGGERS) else text


def _sha256_if_written(path: Path | None) -> str | None:
    """Return the hash of a dataset file of this run, or ``None`` if it wrote none."""
    return file_sha256(path) if path is not None else None


def _remove_stale(study_path: Path, names: Iterable[str]) -> list[str]:
    """Delete the named files of the study folder; return the names that existed."""
    removed = []
    for name in names:
        try:
            (study_path / name).unlink()
        except FileNotFoundError:
            continue
        removed.append(name)
    return removed


def _write_dataset(
    study_path: Path, manifest: dict, key: dict, gathered: _Gathered, ok: bool
) -> tuple[Path | None, Path | None, tuple[str, ...]]:
    """Write the blinded CSV files, or remove them if the check failed.

    No dataset older than the report may sit beside it. The unblinded files
    are therefore removed on every run (``unblind`` writes them again from the
    new blinded files), and after a failed check the blinded files are removed
    too. The caller has already removed the old report and writes the new one
    only after this has succeeded. The unblinding record is never removed: it
    is the only evidence of when the key was first used, and the methods text
    depends on it.

    Returns the two paths written (``None`` for a file that was not) and the
    names of the files that were deleted, so that the user can be told.
    """
    removed = _remove_stale(study_path, _UNBLINDED_NAMES)
    if not ok:
        removed += _remove_stale(study_path, _BLINDED_NAMES)
        return None, None, tuple(removed)

    code_of = {entry["item_id"]: entry["item_code"] for entry in key["items"]}
    rater_order = {rater_id: index for index, rater_id in enumerate(manifest["raters"])}
    assignments = sorted(
        key["assignments"], key=lambda entry: (rater_order[entry["rater_id"]], entry["position"])
    )
    rating_rows = []
    comment_rows = []
    for entry in assignments:
        rating_id, rater_id = entry["rating_id"], entry["rater_id"]
        item_code = code_of[entry["item_id"]]
        for scale in manifest["scales"]:
            value = gathered.values.get((rating_id, scale["id"]), "")
            rating_rows.append(
                (item_code, rater_id, scale["id"], value, rating_id, entry["position"])
            )
        if rating_id in gathered.comments:
            comment = _csv_safe(gathered.comments[rating_id])
            comment_rows.append((item_code, rater_id, rating_id, comment))

    ratings_path = study_path / RATINGS_BLINDED_NAME
    write_csv(ratings_path, RATINGS_BLINDED_HEADER, rating_rows)
    comments_path = None
    if manifest["comments"]:
        comments_path = study_path / COMMENTS_BLINDED_NAME
        write_csv(comments_path, COMMENTS_BLINDED_HEADER, comment_rows)
    return ratings_path, comments_path, tuple(removed)
