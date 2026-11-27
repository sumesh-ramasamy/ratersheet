"""Write a methods text that states only what the tool did or measured.

Everything the tool cannot know (who the raters were, who held the key,
whether raters worked independently) is left as an explicit ``[AUTHOR: ...]``
placeholder, so that the text can never claim more than the records show.
Every sentence is built from a record in the study folder; where a step did
not happen, or a check did not run, the text says so instead of staying silent.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import __version__
from ._store import (
    COLLECT_REPORT_NAME,
    LEAK_REPORT_NAME,
    METHODS_NAME,
    PRIVATE_DIR,
    UNBLIND_RECORD_NAME,
    load_key,
    load_manifest,
    read_json,
    write_text,
)
from .errors import IntegrityError, RatersheetError

REPOSITORY_URL = "https://github.com/sumesh-ramasamy/ratersheet"

_LEAK_KINDS = {
    "source_name_in_text": "a source label appeared in a text or context shown to raters",
    "self_identification": (
        "a text or context contained a phrase by which a model names itself or its vendor"
    ),
    "length_separable": "the texts, or the contexts, of two sources differed markedly in length",
    "formatting_separable": (
        "the texts, or the contexts, of two sources differed markedly in their use of markup"
    ),
}
_COVERAGE_ENTRIES = (
    "n_labels",
    "n_labels_searched",
    "n_pairs",
    "n_pairs_compared",
    "min_label_chars",
    "min_items_per_source",
)
_REPORT_ENTRIES = (
    "created",
    "ok",
    "complete",
    "n_expected",
    "n_received",
    "per_rater",
    "problems",
)
_RUN_ENTRIES = ("unblinded_at", "complete", "n_expected", "n_received")

_CHECKLIST_TABLE = """\
| What the text above states | Reporting item it can help with |
|---|---|
| The number of raters for whom sheets were generated and, after collection, the number whose sheets and ratings were received; that the sheets identified rows only by random codes and had no source field | CHART item 7b (the number of evaluators, and whether they were blinded to the identity of the chatbot) |
| How the number of items was chosen: author to supply | CHART item 8 (how the sample size was determined) |
| The number of items and the numbers of raters named above; how they were chosen is for the author to supply | GRRAS item 6 (the numbers of raters and subjects, and how they were chosen) |
| Assignment, order of presentation, coded sheets, the key commitment and the check of returned sheets | GRRAS item 8 (the rating process, including blinding) |
| Independence of the ratings: author to supply | GRRAS item 9 (whether ratings were made independently) |

The reporting items are paraphrased here, not quoted. Check the wording against the published
CHART and GRRAS checklists. This table is an aid for the author; it is not a statement that
either checklist has been satisfied.
"""


def methods_text(study_dir: str | os.PathLike[str]) -> str:
    """Return the methods text for the study in ``study_dir`` as Markdown.

    Works at any stage after ``build``. Stages that have not happened yet are
    said not to have happened.
    """
    study_path = Path(study_dir)
    manifest = load_manifest(study_path)
    key = load_key(study_path, manifest)
    leak_codes, leak_coverage = _leak_record(study_path)
    report = _optional_record(
        study_path / COLLECT_REPORT_NAME,
        manifest,
        _REPORT_ENTRIES,
        "run 'ratersheet collect' again",
    )
    record = _optional_record(
        study_path / UNBLIND_RECORD_NAME,
        manifest,
        _RUN_ENTRIES,
        "it may have been edited. Restore it from a copy of the study folder: it is the only "
        "record of when the key was first used",
    )
    version = manifest["tool_version"]
    paragraphs = [
        f"# Methods text: {_one_line(manifest['title'])}",
        f"Drafted by ratersheet {__version__} ({REPOSITORY_URL}) from the records in the study "
        "folder. It states only what the tool did or measured. Each `[AUTHOR: ...]` marks "
        "something the tool cannot know; replace every one of them before using the text.",
        "## Text",
        _items_and_raters(manifest, key),
        _assignment(manifest, key),
        _blinding(manifest, version),
        _ratings_requested(manifest),
        _leak_check(manifest, leak_codes, leak_coverage),
        _collection(manifest, report),
        _unblinding(report, record),
        f"**Software.** The rating sheets, the key commitment and the leak check described "
        f"above were produced with ratersheet version {version} ({REPOSITORY_URL}).",
        "## Reporting items this text can help with",
        _CHECKLIST_TABLE.rstrip("\n"),
    ]
    return "\n\n".join(paragraphs) + "\n"


def write_methods(study_dir: str | os.PathLike[str]) -> Path:
    """Write the methods text to ``methods.md`` in the study folder; return its path."""
    path = Path(study_dir) / METHODS_NAME
    write_text(path, methods_text(study_dir))
    return path


def _optional_record(
    path: Path, manifest: dict, entries: tuple[str, ...], remedy: str
) -> dict | None:
    """Read a collect report or unblind record if it exists and belongs to this study.

    ``entries`` are the ones the text is built from. The tool always writes
    them, so a file without one has been edited; saying which entry, and what
    to do (``remedy``), is of more use to a researcher than the ``KeyError``
    the missing entry would end in.
    """
    if not path.exists():
        return None
    record = read_json(path, "the record")
    if record.get("key_sha256") != manifest["key_sha256"]:
        raise IntegrityError(
            f"{path} was written for a different key than the one in the manifest; it does not "
            "belong to this study. Move it out of the study folder (keep it: it is a record of "
            "an earlier build), then run 'ratersheet collect' again if it was the collect report."
        )
    missing = [entry for entry in entries if entry not in record]
    # An unblinding record holds the same entries once more for the first run,
    # which is the run the text reports.
    first = record.get("first")
    if isinstance(first, dict):
        missing += [f"first.{entry}" for entry in entries if entry not in first]
    if missing:
        raise RatersheetError(f"{path} is missing the entry {missing[0]!r}; {remedy}.")
    return record


def _leak_record(study_path: Path) -> tuple[list[str], dict | None]:
    """Return the code of every leak warning raised, and what the leak checks covered.

    Both come from the private leak report, not from the manifest: which cue
    separates the sources is itself a hint to a rater, so it is recorded only
    where raters cannot read it. The coverage is ``None`` when the report does
    not have one; the text then says nothing about what was covered.
    """
    path = study_path / PRIVATE_DIR / LEAK_REPORT_NAME
    if not path.is_file():
        raise RatersheetError(
            f"{path} does not exist. The methods text takes the outcome of the leak check from "
            "this file, which 'ratersheet build' wrote; restore it from a copy of the study "
            "folder."
        )
    report = read_json(path, "the leak report")
    warnings = report.get("warnings")
    coverage = report.get("coverage")
    well_formed = isinstance(warnings, list) and all(
        isinstance(warning, dict) and isinstance(warning.get("code"), str) for warning in warnings
    )
    if coverage is not None:
        well_formed = well_formed and isinstance(coverage, dict) and all(
            isinstance(coverage.get(entry), int) for entry in _COVERAGE_ENTRIES
        )
    if not well_formed:
        raise RatersheetError(
            f"{path} is not a leak report as 'ratersheet build' writes it; it may have been "
            "edited. Restore it from a copy of the study folder."
        )
    return [warning["code"] for warning in warnings], coverage


def _one_line(text: str) -> str:
    """Return ``text`` on one line.

    A line break in a title would end the Markdown heading early and turn the
    rest of the title into a paragraph of the methods text.
    """
    return " ".join(text.split())


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _was(n: int) -> str:
    return "was" if n == 1 else "were"


def _span(numbers: list[int], noun: str) -> str:
    """Describe a list of counts as one number or as a range."""
    low, high = min(numbers), max(numbers)
    return _count(low, noun) if low == high else f"between {low} and {high} {noun}s"


def _raters_of(k: int, n: int) -> str:
    """Name ``k`` of ``n`` raters in words that stay grammatical for one rater (``k`` >= 1)."""
    if n == 1:
        return "the one rater"
    if k < n:
        return f"{k} of the {n} raters"
    return "both raters" if n == 2 else f"all {n} raters"


def _share(received: int, expected: int) -> str:
    """Return a percentage that reads 100.0% only for all and 0.0% only for none.

    Plain rounding prints 1999 of 2000 as "100.0%" beside the words "not
    complete", so the rounded figure is kept away from both ends.
    """
    percent = 100 * received / expected
    if received < expected:
        percent = min(percent, 99.9)
    if received > 0:
        percent = max(percent, 0.1)
    return f"{percent:.1f}%"


def _items_and_raters(manifest: dict, key: dict) -> str:
    per_source = {source: 0 for source in key["sources"]}
    for item in key["items"]:
        per_source[item["source"]] += 1
    sources = "; ".join(f"{source}: {_count(n, 'item')}" for source, n in per_source.items())
    # Sheets generated, not raters who rated: who returned a sheet is only
    # known after collection, and is stated there.
    return (
        f"**Items and raters.** The study comprised {_count(manifest['n_items'], 'item')} from "
        f"{_count(manifest['n_sources'], 'source')} ({sources}). "
        "[AUTHOR: describe how the items were obtained and how their number was chosen.] "
        f"Rating sheets were generated for {_count(len(manifest['raters']), 'rater')}. "
        "[AUTHOR: describe who the raters were, their qualifications, and how the number of "
        "raters was chosen.]"
    )


def _assignment(manifest: dict, key: dict) -> str:
    n_raters = len(manifest["raters"])
    source_of = {item["item_id"]: item["source"] for item in key["items"]}
    per_rater_source = {
        (rater, source): 0 for rater in manifest["raters"] for source in key["sources"]
    }
    for assignment in key["assignments"]:
        per_rater_source[(assignment["rater_id"], source_of[assignment["item_id"]])] += 1
    total = _count(manifest["n_assignments"], "assignment")
    if n_raters == 1:
        crossing = f"The study had one rater, who was assigned every item ({total} in total)."
    elif manifest["raters_per_item"] == n_raters:
        crossing = (
            f"Every rater was assigned every item (a fully crossed design; {total} in total)."
        )
    else:
        crossing = (
            f"Each item was assigned to {manifest['raters_per_item']} of the {n_raters} raters "
            f"({total} in total; the design was not fully crossed)."
        )
    each = "The rater" if n_raters == 1 else "Each rater"
    per_rater = "" if n_raters == 1 else " separately for each rater"
    order = f"The order of the items was randomised{per_rater}."
    if manifest["group_by_case"]:
        order = (
            "Items that belonged to the same case were presented together; the order of the "
            f"cases, and of the items within each case, was randomised{per_rater}."
        )
    return (
        f"**Assignment and order.** {crossing} The assignment, the order of presentation and "
        "the codes were generated by the software from the study's random seed and a random "
        f"value that is kept in the unblinding key. {each} was assigned "
        f"{_span(list(manifest['per_rater'].values()), 'item')} in total and "
        f"{_span(list(per_rater_source.values()), 'item')} from any one source. {order}"
    )


def _blinding(manifest: dict, version: str) -> str:
    shown = "the text of each item"
    if manifest["has_context"]:
        shown += " and, where one was supplied, its context"
    # "Generated", not "received": the tool wrote the files and cannot know
    # whether they were sent. The key is said to match the manifest because
    # that is the comparison the tool made; the one with force is against a
    # sheet in a rater's hands, so the text tells the reader how to make it.
    generated = "A separate spreadsheet was generated for each rater"
    if len(manifest["raters"]) == 1:
        generated = "A spreadsheet was generated for the rater"
    return (
        f"**Blinding.** {generated} by ratersheet {version}. "
        f"The sheet showed {shown}, identified each row only by a random code, and "
        "had no field naming the source of an item. The file that links the codes to the items "
        "and their sources (the unblinding key) was written separately from the sheets. Its "
        f"SHA-256 hash ({manifest['key_sha256']}) was embedded in every sheet when the sheets "
        f"were generated ({manifest['created']}) and recorded in the study's manifest. Anyone "
        "who holds a sheet can check that the key is unchanged since then by comparing the "
        "SHA-256 hash of the key file with the hash on the sheet's Instructions page. When this "
        "text was drafted, the key file matched the hash recorded in the manifest. "
        "[AUTHOR: state who held the unblinding key and confirm that it was not shared with the "
        "raters.] "
        "[AUTHOR: state whether the raters worked independently of one another.]"
    )


def _scale_phrase(scale: dict) -> str:
    values = scale["values"]
    if scale["type"] == "ordinal":
        detail = f"ordinal, {values[0]} to {values[-1]}"
    else:
        detail = "nominal: " + ", ".join(str(value) for value in values)
    if not scale["required"]:
        detail += "; optional"
    return f"{scale['label']} ({detail})"


def _ratings_requested(manifest: dict) -> str:
    scales = "; ".join(_scale_phrase(scale) for scale in manifest["scales"])
    comment = " A free-text comment could be added for each item." if manifest["comments"] else ""
    return (
        f"**Ratings requested.** For each item the sheet asked for "
        f"{_count(len(manifest['scales']), 'rating')}: {scales}. Each rating cell offered its "
        f"allowed values as a drop-down list.{comment}"
    )


def _labels_searched(coverage: dict, where: str) -> str:
    """Say how many source labels were searched for, and why the others were not."""
    n, searched = coverage["n_labels"], coverage["n_labels_searched"]
    short = n - searched
    if short == 0:
        labels = "the source label" if n == 1 else f"each of the {n} source labels"
        return f"It searched {where} for {labels}."
    rule = (
        f"shorter than {coverage['min_label_chars']} characters, and labels that short are not "
        "searched for"
    )
    if searched == 0:
        labels, subject = "the source label", "it"
        if n > 1:
            labels, subject = f"any of the {n} source labels", "each"
        return f"It did not search {where} for {labels}: {subject} is {rule}."
    others = "one is" if short == 1 else f"{short} are"
    return (
        f"It searched {where} for {searched} of the {n} source labels; the other {others} "
        f"{rule}."
    )


def _pairs_compared(coverage: dict, has_context: bool) -> str:
    """Say how many pairs of sources were compared for length and markup, and why not all."""
    pairs, compared = coverage["n_pairs"], coverage["n_pairs_compared"]
    skipped = pairs - compared
    rule = f"a source with fewer than {coverage['min_items_per_source']} items is not compared"
    if pairs == 0:
        return (
            "The study had a single source, so there were no two sources to compare for length "
            "or markup."
        )
    if pairs == 1:
        scope = "between the two sources"
    elif compared == 0:
        scope = f"for any of the {pairs} pairs of sources"
    elif skipped == 0:
        scope = f"for each of the {pairs} pairs of sources"
    else:
        scope = f"for {compared} of the {pairs} pairs of sources"
    if compared == 0:
        what = "texts or contexts" if has_context else "texts"
        return f"It did not compare the length or the markup of the {what} {scope}, because {rule}."
    sentence = (
        "It compared the length of the texts, and their use of list, heading or emphasis "
        f"markup, {scope}"
    )
    if has_context:
        sentence += ", and did the same for the contexts"
    if skipped:
        sentence += (
            f"; the other {_count(skipped, 'pair')} {_was(skipped)} not compared, because {rule}"
        )
    return sentence + "."


def _leak_check(manifest: dict, codes: list[str], coverage: dict | None) -> str:
    """Describe the leak check from what ran, never from what the tool can do in general.

    A check that was skipped (a short label, a source with few items) is named
    as not made: "checked, no warnings" for a comparison that never ran would
    be the most misleading sentence this text could contain.
    """
    where = "the texts and contexts" if manifest["has_context"] else "the texts"
    if coverage is None:
        made = (
            "**Check for identifying content (leak check).** When the sheets were generated, "
            f"the software ran its leak check, which looks in {where} shown to raters for cues "
            "to the source of an item. The record of that check does not say which source "
            "labels were searched for or which pairs of sources were compared, so neither is "
            "stated here."
        )
        subject = "The check"
    else:
        made = (
            "**Check for identifying content (leak check).** When the sheets were generated, "
            f"the software looked in {where} shown to raters for cues to the source of an item. "
            f"{_labels_searched(coverage, where)} It searched them for a fixed list of English "
            "phrases by which a language model names itself or its vendor; a wording that is "
            f"not on the list is not found. {_pairs_compared(coverage, manifest['has_context'])}"
        )
        in_full = (
            coverage["n_labels_searched"] == coverage["n_labels"]
            and coverage["n_pairs_compared"] == coverage["n_pairs"]
        )
        subject = "These checks" if in_full else "The checks that were made"
    kinds: dict[str, int] = {}
    for code in codes:
        kinds[code] = kinds.get(code, 0) + 1
    if kinds:
        listed = "; ".join(
            f"{n} of kind `{code}` ({_LEAK_KINDS.get(code, 'see the leak report')})"
            for code, n in kinds.items()
        )
        outcome = (
            f"{subject} raised {_count(len(codes), 'warning')}: {listed}. "
            "[AUTHOR: for each leak warning, describe what was done about it.]"
        )
    else:
        outcome = f"{subject} raised no warnings."
    return (
        f"{made} {outcome} The check is automated and limited; it cannot show that no cue "
        "remained. "
        "[AUTHOR: describe any other steps taken to remove identifying content from the texts.]"
    )


def _collection(manifest: dict, report: dict | None) -> str:
    """Describe the last check of returned sheets, in whatever state it left the folder.

    The number of sheets comes from the report (a rater without a
    ``missing_workbook`` problem returned one), so the text never says that
    sheets were checked when none came back.
    """
    if report is None:
        return (
            "**Collection.** The study folder holds no report of the software's check of "
            "returned sheets, so no ratings are reported here."
        )
    ran = f"**Collection.** The software's check of returned sheets was run ({report['created']})"
    if not report["ok"]:
        errors = [p for p in report["problems"] if p["severity"] == "error"]
        codes = ", ".join(sorted({f"`{problem['code']}`" for problem in errors}))
        return (
            f"{ran} and found {_count(len(errors), 'error')} ({codes}), so no dataset was "
            "written. This text should be drafted again after the errors have been resolved."
        )

    # A rater with nothing assigned has nothing to return and is not counted.
    assigned = [rater for rater in manifest["raters"] if manifest["per_rater"][rater] > 0]
    missing = {p["rater_id"] for p in report["problems"] if p["code"] == "missing_workbook"}
    n_raters, n_returned = len(assigned), len(assigned) - len(missing)
    without_items = len(manifest["raters"]) - n_raters
    if without_items:
        ran = (
            f"**Collection.** Of the {len(manifest['raters'])} raters, {n_raters} "
            f"{_was(n_raters)} assigned items. The software's check of returned sheets was run "
            f"({report['created']})"
        )
    if n_returned == 0:
        nobody = "the one rater" if n_raters == 1 else f"any of the {n_raters} raters"
        sheets = f"{ran}, but it was given no sheet from {nobody}."
    else:
        compared = "its text was compared with a hash"
        if manifest["has_context"]:
            compared = "its text and context were compared with hashes"
        returned_by = _raters_of(n_returned, n_raters)
        if without_items:
            # "Both raters" beside "Of the 5 raters" needs saying which two.
            returned_by += f" who {_was(n_raters)} assigned items"
        sheets = (
            f"{ran} on the {'sheet' if n_returned == 1 else 'sheets'} returned by {returned_by}. "
            f"Each returned row was matched to its assignment by its code, {compared} recorded in "
            "the key when the sheets were generated, and each rating was checked against the "
            "allowed values of its scale. The check found no errors."
        )
        if missing:
            others = "rater" if len(missing) == 1 else f"{len(missing)} raters"
            sheets += f" No sheet was provided for the other {others}."

    expected, received = report["n_expected"], report["n_received"]
    if expected == 0:
        ratings = "No scale was required, so no rating was counted as expected or as received."
    else:
        n_required = sum(1 for scale in manifest["scales"] if scale["required"])
        n_rated = sum(1 for rater in assigned if report["per_rater"][rater]["received"] > 0)
        ratings = (
            f"Of the {_count(expected, 'expected rating')} "
            f"({_count(manifest['n_assignments'], 'assignment')}, each with "
            f"{_count(n_required, 'required scale')}), {received} ({_share(received, expected)}) "
            f"{_was(received)} received"
        )
        ratings += f", from {_raters_of(n_rated, n_raters)}." if n_rated else "."
    # Complete means every required rating and every rater's sheet. The sheets
    # are counted here as well, so that a report written before that rule
    # cannot make this text call a collection with a sheet outstanding complete.
    complete = report["complete"] and not missing
    return f"{sheets} {ratings} Collection was {'complete' if complete else 'not complete'}."


def _checked_after(first: dict, report: dict) -> bool:
    """True if the collect report is of a later check than the one the first unblinding used.

    Compared with the first unblinding, not the latest: every later run of the
    unblinding step would otherwise remove the sentence this decides, and with
    it the only statement that the dataset was checked after the key was used.

    The unblinding record keeps the run id of the report it read, so another
    id means another check. The times cannot say this: both are written to the
    second, and they are equal both when the key is used straight after a
    check and when a check is run straight after the key was used. They are
    compared only for a report or a record that has no run id.
    """
    used, current = first.get("collect_run_id"), report.get("run_id")
    if used is not None and current is not None:
        return used != current
    return report["created"] > first["unblinded_at"]


def _unblinding(report: dict | None, record: dict | None) -> str:
    if record is None:
        # Said in terms of what the folder shows: the tool cannot know whether
        # the key file was opened by other means.
        return (
            "**Unblinding.** The study folder holds no record that the software's unblinding "
            "step has been run."
        )
    # What matters for blinding is the first time the key was used.
    first = record.get("first") or record
    if first["complete"] and first["n_expected"] == 0:
        # With no required scale there are no expected ratings to count, so
        # the state is told by sheets: "0 of the 0 expected ratings" says nothing.
        state = (
            "after a sheet from every rater with assignments had been received and checked "
            "(no scale was required, so no rating was expected)"
        )
    elif first["complete"]:
        state = "after all expected ratings had been received and checked"
    elif first["n_expected"] == 0:
        state = (
            "before a sheet had been received from every rater with assignments; collection "
            "was not complete at that time"
        )
    else:
        state = (
            f"when {first['n_received']} of the {_count(first['n_expected'], 'expected rating')} "
            "had been received; collection was not complete at that time"
        )
    text = (
        "**Unblinding.** The codes were first linked back to the items and their sources with "
        f"the software's unblinding step ({first['unblinded_at']}), {state}. Before doing so the "
        "software confirmed that the key file still matched the hash recorded in the study's "
        "manifest when the sheets were generated."
    )
    times = record.get("times_unblinded", 1)
    if times > 1:
        text += (
            f" The unblinding step was run {times} times in all, most recently "
            f"({record['unblinded_at']})"
        )
        if record["n_expected"] == 0:
            text += f", when collection was {'complete' if record['complete'] else 'not complete'}."
        else:
            text += (
                f" with {record['n_received']} of the "
                f"{_count(record['n_expected'], 'expected rating')} received."
            )
    if report is not None and _checked_after(first, report):
        text += (
            f" The check of returned sheets reported above ({report['created']}) was run after "
            "the codes had first been linked to sources, that is, after the first unblinding. "
            "The software does not record whether any rating was added or changed between that "
            "check and the one before it. [AUTHOR: state whether any rating was added or "
            "changed after the first unblinding, and why.]"
        )
    return text
