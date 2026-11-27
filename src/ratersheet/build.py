"""Build a study folder: the unblinding key, one workbook per rater, the manifest.

The key is written first and its SHA-256 goes into every workbook. Raters
therefore hold a commitment to the key before they rate anything, and the key
can later be shown to be the one that existed when the sheets were made.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from . import __version__
from ._store import (
    COLLECT_REPORT_NAME,
    COMMENTS_BLINDED_NAME,
    COMMENTS_NAME,
    FORMAT_VERSION,
    KEY_NAME,
    LEAK_REPORT_NAME,
    MANIFEST_NAME,
    METHODS_NAME,
    PRIVATE_DIR,
    PRIVATE_README_NAME,
    RATINGS_BLINDED_NAME,
    RATINGS_NAME,
    SHEETS_DIR,
    UNBLIND_RECORD_NAME,
    utc_now,
    write_json,
    write_text,
)
from .assign import Design, design_summary, make_design
from .config import Study
from .errors import RatersheetError
from .integrity import file_sha256, text_sha256
from .leaks import LeakWarning, check_leaks, leak_coverage
from .workbook import SheetRow, write_workbook

_NONCE_RE = re.compile(r"[0-9a-fA-F]{64}")
_TOP_LEVEL_OUTPUTS = (
    MANIFEST_NAME,  # first, so that a half-cleared folder is no longer a study folder
    COLLECT_REPORT_NAME,
    RATINGS_BLINDED_NAME,
    COMMENTS_BLINDED_NAME,
    RATINGS_NAME,
    COMMENTS_NAME,
    UNBLIND_RECORD_NAME,
    METHODS_NAME,
)
_PRIVATE_OUTPUTS = (KEY_NAME, LEAK_REPORT_NAME, PRIVATE_README_NAME)
_PRIVATE_README = (
    "This folder holds the unblinding key (key.json) and the full leak report of the study.\n"
    "Do not send it to raters, and do not store it where raters can read it.\n"
    "On macOS and Linux the folder, the key and the leak report are created readable by your\n"
    "account only. On Windows they have the permissions of the folder above.\n"
    "The study file and the items file name the sources as well and need the same care.\n"
)


@dataclass(frozen=True)
class BuildResult:
    """Where the outputs of a build are, and what was found on the way."""

    out_dir: Path
    sheets: dict[str, Path]  # rater_id -> workbook path
    key_path: Path
    manifest_path: Path
    key_sha256: str
    warnings: list[LeakWarning]
    design: Design  # who rates what in this build; it cannot be known before building


def build(
    study: Study,
    out_dir: str | os.PathLike[str],
    *,
    nonce: str | None = None,
    created: str | None = None,
    overwrite: bool = False,
) -> BuildResult:
    """Write the study folder for ``study`` into ``out_dir``.

    ``nonce`` is 64 random hex characters stored in the key. It makes the
    commitment hiding: without it, someone who knew the items could hash their
    guesses of the key and confirm one. It is also the secret from which who
    rates what, the order and the codes are drawn, so that none of them can be
    predicted from the seed; the design can be regenerated from the key, which
    records both. ``created`` is the timestamp recorded in the outputs; both
    default to fresh values.

    Without ``overwrite`` nothing that already exists is deleted or replaced:
    the build is refused if ``out_dir`` holds any file it would write. With
    ``overwrite`` those files are deleted first: the outputs of build, collect,
    unblind and methods by name, and every ``.xlsx`` file in ``sheets/``.
    """
    out = Path(out_dir)
    manifest_file = out / MANIFEST_NAME
    if nonce is None:
        nonce = secrets.token_hex(32)
    elif not _NONCE_RE.fullmatch(nonce):
        raise RatersheetError("nonce must be exactly 64 hexadecimal characters")
    nonce = nonce.lower()  # one spelling, so the key records exactly the secret that was used
    if created is None:
        created = utc_now()

    try:
        stale = _previous_outputs(out)
        if stale and not overwrite:
            raise RatersheetError(_refusal(out, stale))
        for path in stale:
            path.unlink(missing_ok=True)
        (out / SHEETS_DIR).mkdir(parents=True, exist_ok=True)
        (out / PRIVATE_DIR).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RatersheetError(f"cannot prepare the output folder {out}: {exc}") from None
    # chmod and not mkdir(mode=...): the folder may be left from an earlier build. Some file
    # systems (FAT, some network shares) have no permissions; the build must not fail there.
    with contextlib.suppress(OSError):
        os.chmod(out / PRIVATE_DIR, 0o700)

    design = make_design(study, secret=nonce)
    warnings = check_leaks(study)

    key_file = out / PRIVATE_DIR / KEY_NAME
    write_json(key_file, _key_object(study, design, nonce, created), private=True)
    key_sha256 = file_sha256(key_file)
    write_text(out / PRIVATE_DIR / PRIVATE_README_NAME, _PRIVATE_README)
    leak_report = _leak_report(study, warnings, created)
    write_json(out / PRIVATE_DIR / LEAK_REPORT_NAME, leak_report, private=True)

    sheets = _write_sheets(study, design, out, key_sha256)
    write_json(manifest_file, _manifest_object(study, design, sheets, key_sha256, created))
    return BuildResult(
        out_dir=out,
        sheets=sheets,
        key_path=key_file,
        manifest_path=manifest_file,
        key_sha256=key_sha256,
        warnings=warnings,
        design=design,
    )


def _previous_outputs(out: Path) -> list[Path]:
    """List the files in ``out`` that a build would delete or replace.

    These are what an earlier build, collect, unblind or methods left behind.
    A new build has a new key, so every older output would be orphaned or,
    worse, mistaken for part of the new study. The files are matched by name
    only, so some may be the user's own (a ``methods.md``, a spreadsheet kept
    in ``sheets/``); that is why a build never touches them unless it was
    told to overwrite.
    """
    candidates = [out / name for name in _TOP_LEVEL_OUTPUTS]
    candidates += [out / PRIVATE_DIR / name for name in _PRIVATE_OUTPUTS]
    # Any spelling of the extension: where file names ignore case, writing R1.xlsx would
    # replace an existing R1.XLSX.
    candidates += sorted((out / SHEETS_DIR).glob("*.[xX][lL][sS][xX]"))
    return [path for path in candidates if os.path.lexists(path)]


def _refusal(out: Path, existing: list[Path]) -> str:
    """Explain why a build without ``overwrite`` stops at files that already exist."""
    if out / MANIFEST_NAME in existing:
        return (
            f"{out} already holds a built study ({MANIFEST_NAME} exists). Building again would "
            "create a new key, and sheets already sent to raters could no longer be collected. "
            "Choose another output folder, or pass overwrite (--overwrite) to discard the old "
            "study."
        )
    n_more = len(existing) - 1
    more = "" if n_more == 0 else f" and {n_more} other file{'' if n_more == 1 else 's'}"
    return (
        f"{out} already contains {existing[0]}{more} that a build would delete or replace. "
        f"The folder has no {MANIFEST_NAME}, so it is not a complete study folder; if the "
        "files are left from an earlier study, building would lose its key, and sheets already "
        "sent to raters could no longer be collected. Nothing was changed. Choose another "
        "output folder, or pass overwrite (--overwrite) to delete these files, for example to "
        "repeat a build that stopped half-way."
    )


def _key_object(study: Study, design: Design, nonce: str, created: str) -> dict:
    """The unblinding key: which code is which item, and which source wrote it."""
    return {
        "format_version": FORMAT_VERSION,
        "tool_version": __version__,
        "created": created,
        "title": study.title,
        "seed": study.seed,
        "nonce": nonce,
        "sources": list(study.sources),
        "items": [
            {
                "item_id": item.item_id,
                "item_code": design.item_codes[item.item_id],
                "source": item.source,
                "case_id": item.case_id,
                "text_sha256": text_sha256(item.text),
                "context_sha256": None if item.context is None else text_sha256(item.context),
            }
            for item in study.items
        ],
        "assignments": [
            {
                "rating_id": assignment.rating_id,
                "rater_id": assignment.rater_id,
                "item_id": assignment.item_id,
                "position": assignment.position,
            }
            for assignment in design.assignments
        ],
    }


def _leak_report(study: Study, warnings: list[LeakWarning], created: str) -> dict:
    """The full leak warnings and what the checks could look at.

    Private because the warnings name items and sources, and because even
    their kinds tell a reader which cue separates the sources.
    """
    return {
        "tool_version": __version__,
        "created": created,
        "title": study.title,
        "coverage": asdict(leak_coverage(study)),
        "warnings": [
            {
                "code": warning.code,
                "message": warning.message,
                "item_ids": list(warning.item_ids),
                "sources": list(warning.sources),
            }
            for warning in warnings
        ],
    }


def _write_sheets(study: Study, design: Design, out: Path, key_sha256: str) -> dict[str, Path]:
    """Write one workbook per rater and return their paths."""
    item_by_id = {item.item_id: item for item in study.items}
    has_context = any(item.context is not None for item in study.items)
    sheets: dict[str, Path] = {}
    for rater_id in study.raters:
        rows = [
            SheetRow(
                rating_id=assignment.rating_id,
                text=item_by_id[assignment.item_id].text,
                context=item_by_id[assignment.item_id].context,
            )
            for assignment in design.assignments  # already in position order per rater
            if assignment.rater_id == rater_id
        ]
        path = out / SHEETS_DIR / f"{rater_id}.xlsx"
        write_workbook(
            path,
            title=study.title,
            instructions=study.instructions,
            rater_id=rater_id,
            rows=rows,
            scales=study.scales,
            comments=study.comments,
            key_sha256=key_sha256,
            has_context=has_context,
        )
        sheets[rater_id] = path
    return sheets


def _manifest_object(
    study: Study,
    design: Design,
    sheets: dict[str, Path],
    key_sha256: str,
    created: str,
) -> dict:
    """The shareable description of the study.

    It must never contain source labels, item ids, case ids, the seed or
    anything worked out from the sources, such as which leak warnings were
    raised: anyone, including a rater, may read it.
    """
    summary = design_summary(study, design)
    return {
        "format_version": FORMAT_VERSION,
        "tool": "ratersheet",
        "tool_version": __version__,
        "created": created,
        "title": study.title,
        "n_items": len(study.items),
        "n_sources": len(study.sources),
        "raters": list(study.raters),
        "raters_per_item": study.raters_per_item,
        "group_by_case": study.group_by_case,
        "comments": study.comments,
        "has_context": any(item.context is not None for item in study.items),
        "scales": [
            {
                "id": scale.id,
                "label": scale.label,
                "type": scale.type,
                "values": list(scale.values),
                "description": scale.description,
                "required": scale.required,
            }
            for scale in study.scales
        ],
        "per_rater": summary["per_rater"],
        "n_assignments": summary["n_assignments"],
        "key_sha256": key_sha256,
        "sheets": {
            rater_id: {"file": f"{SHEETS_DIR}/{path.name}", "sha256": file_sha256(path)}
            for rater_id, path in sheets.items()
        },
    }
