"""methods_text read state by state: every sentence about collection and unblinding must be true.

Each test puts a study folder into one state (nothing returned, a sheet missing, errors,
no required scale, collected again after the key was used, ...) and checks what the text
then says, and what it must no longer say.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from conftest import (
    DEFAULT_SCALES,
    SRC_A,
    SRC_B,
    Built,
    Filled,
    edit_cell,
    fill_workbooks,
    make_built,
    make_item_rows,
)

from ratersheet import IntegrityError, RatersheetError, collect, methods_text, unblind

T_COLLECT_1 = "2026-12-01T09:00:00Z"
T_UNBLIND_1 = "2026-12-01T10:00:00Z"
T_COLLECT_2 = "2026-12-05T09:00:00Z"
T_UNBLIND_2 = "2026-12-05T10:00:00Z"

ALL_OPTIONAL = [dict(scale, required=False) for scale in DEFAULT_SCALES]


def paragraph(built: Built, name: str) -> str:
    found = [p for p in methods_text(built.out).split("\n\n") if p.startswith(f"**{name}")]
    assert len(found) == 1, f"expected one paragraph headed {name!r}"
    return found[0]


def edit_json(path: Path, change: Callable[[dict], Any]) -> None:
    obj = json.loads(path.read_text(encoding="utf-8"))
    change(obj)
    path.write_text(json.dumps(obj), encoding="utf-8")


def empty_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "nothing_returned"
    folder.mkdir()
    return folder


def optional_study(tmp_path: Path) -> Built:
    return make_built(tmp_path, rows=make_item_rows({SRC_A: 5, SRC_B: 5}), scales=ALL_OPTIONAL)


# ---- collection -------------------------------------------------------------------------------


def test_before_any_collection_no_ratings_are_reported(built: Built) -> None:
    text = paragraph(built, "Collection")
    assert "holds no report" in text and "no ratings are reported" in text
    assert "checked" not in text and "received" not in text


def test_complete_collection_names_the_sheets_and_the_raters(built: Built, filled: Filled) -> None:
    result = collect(built.out, filled.dir, created=T_COLLECT_1)
    assert result.ok and result.complete
    text = paragraph(built, "Collection")
    assert f"was run ({T_COLLECT_1}) on the sheets returned by all 3 raters." in text
    assert "its text and context were compared with hashes recorded in the key" in text
    assert "The check found no errors." in text
    assert f"{result.n_received} (100.0%) were received, from all 3 raters." in text
    assert text.endswith("Collection was complete.")


def test_a_missing_sheet_is_counted(built: Built, tmp_path: Path) -> None:
    fill = fill_workbooks(built.out, tmp_path / "partial", skip_raters=("R3",))
    result = collect(built.out, fill.dir)
    assert result.ok and not result.complete
    text = paragraph(built, "Collection")
    assert "on the sheets returned by 2 of the 3 raters." in text
    assert "No sheet was provided for the other rater." in text
    assert f"{result.n_received} (" in text and "were received, from 2 of the 3 raters." in text
    assert text.endswith("Collection was not complete.")


def test_nothing_returned_is_not_called_checked(built: Built, tmp_path: Path) -> None:
    result = collect(built.out, empty_folder(tmp_path))
    assert result.ok and result.n_received == 0
    text = paragraph(built, "Collection")
    assert "it was given no sheet from any of the 3 raters" in text
    assert "returned sheets were checked" not in text
    assert "Each returned row" not in text and "found no errors" not in text
    assert f"Of the {result.n_expected} expected ratings" in text
    assert "0 (0.0%) were received." in text
    assert text.endswith("Collection was not complete.")


def test_a_sheet_returned_blank_is_a_sheet_but_not_a_rating(built: Built, tmp_path: Path) -> None:
    fill = fill_workbooks(built.out, tmp_path / "returned", comments=False)
    blank = fill_workbooks(built.out, tmp_path / "blank", value_fn=lambda scale, rid: None, comments=False)
    fill.paths["R3"].write_bytes(blank.paths["R3"].read_bytes())
    result = collect(built.out, fill.dir)
    assert result.ok and not result.complete
    text = paragraph(built, "Collection")
    assert "on the sheets returned by all 3 raters." in text
    assert "were received, from 2 of the 3 raters." in text
    assert text.endswith("Collection was not complete.")


def test_one_rating_was_received_in_the_singular(tmp_path: Path) -> None:
    b = make_built(
        tmp_path, rows=make_item_rows({SRC_A: 1, SRC_B: 1}), raters=["R1"], scales=[DEFAULT_SCALES[0]]
    )
    fill = fill_workbooks(b.out, tmp_path / "returned")
    edit_cell(fill.paths["R1"], 1, "accuracy", None)
    result = collect(b.out, fill.dir)
    assert (result.n_received, result.n_expected) == (1, 2)
    text = paragraph(b, "Collection")
    assert "on the sheet returned by the one rater." in text, "one rater returns one sheet"
    assert "its text was compared with a hash recorded in the key" in text
    assert "(2 assignments, each with 1 required scale), 1 (50.0%) was received, from the one rater." in text
    assert "were received" not in text


@pytest.mark.parametrize(
    ("received", "expected", "shown"),
    [(1999, 2000, "99.9%"), (4998, 5000, "99.9%"), (1, 2001, "0.1%"), (1998, 2000, "99.9%")],
)
def test_percentage_never_rounds_to_all_or_none(
    built: Built, filled: Filled, received: int, expected: int, shown: str
) -> None:
    collect(built.out, filled.dir)

    def almost(report: dict) -> None:
        report.update(n_received=received, n_expected=expected, complete=False)

    edit_json(built.out / "collect_report.json", almost)
    text = paragraph(built, "Collection")
    assert f"{received} ({shown}) w" in text
    assert "100.0%" not in text and "(0.0%)" not in text
    assert text.endswith("Collection was not complete.")


def test_collection_with_errors_writes_no_claim_of_a_clean_check(built: Built, filled: Filled) -> None:
    edit_cell(filled.paths["R1"], 0, "text", "edited")
    assert not collect(built.out, filled.dir, created=T_COLLECT_1).ok
    text = paragraph(built, "Collection")
    assert f"was run ({T_COLLECT_1}) and found 1 error (`text_changed`), so no dataset was written" in text
    assert "found no errors" not in text and "received" not in text and "complete" not in text


def test_a_rater_without_assignments_is_not_counted_as_missing(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 1, SRC_B: 1}), raters=["R1", "R2", "R3"], rpi=1)
    idle = [rater for rater, n in b.manifest["per_rater"].items() if n == 0]
    assert len(idle) == 1, "two items, one rater each: one of three raters has nothing to rate"
    fill = fill_workbooks(b.out, tmp_path / "returned", skip_raters=tuple(idle))
    result = collect(b.out, fill.dir)
    assert result.ok and result.complete
    text = paragraph(b, "Collection")
    assert text.startswith("**Collection.** Of the 3 raters, 2 were assigned items.")
    assert "on the sheets returned by both raters who were assigned items." in text
    assert text.endswith("Collection was complete.")


def test_some_of_the_raters_with_items_are_counted_among_those_with_items(tmp_path: Path) -> None:
    b = make_built(
        tmp_path, rows=make_item_rows({SRC_A: 2, SRC_B: 1}), raters=["R1", "R2", "R3", "R4"], rpi=1
    )
    busy = [rater for rater, n in b.manifest["per_rater"].items() if n > 0]
    assert len(busy) == 3, "three items, one rater each: one of four raters has nothing to rate"
    fill = fill_workbooks(b.out, tmp_path / "returned", skip_raters=(busy[0],))
    collect(b.out, fill.dir)
    text = paragraph(b, "Collection")
    assert "on the sheets returned by 2 of the 3 raters who were assigned items." in text
    assert "No sheet was provided for the other rater." in text


def test_every_rater_with_items_needs_no_such_clause(built: Built, filled: Filled) -> None:
    collect(built.out, filled.dir)
    assert "who were assigned items" not in paragraph(built, "Collection")


# ---- no required scale ------------------------------------------------------------------------


def test_no_required_scale_and_nothing_returned_is_not_complete(tmp_path: Path) -> None:
    b = optional_study(tmp_path)
    collect(b.out, empty_folder(tmp_path))
    text = paragraph(b, "Collection")
    assert "it was given no sheet from any of the 3 raters" in text
    assert "No scale was required, so no rating was counted as expected or as received." in text
    assert "Each returned row" not in text and "found no errors" not in text
    assert text.endswith("Collection was not complete.")


def test_no_required_scale_and_some_sheets_returned_is_not_complete(tmp_path: Path) -> None:
    b = optional_study(tmp_path)
    fill = fill_workbooks(b.out, tmp_path / "returned", skip_raters=("R2", "R3"))
    collect(b.out, fill.dir)
    text = paragraph(b, "Collection")
    assert "on the sheet returned by 1 of the 3 raters." in text
    assert "No sheet was provided for the other 2 raters." in text
    assert "No scale was required" in text
    assert text.endswith("Collection was not complete.")


def test_no_required_scale_and_every_sheet_returned_is_complete(tmp_path: Path) -> None:
    b = optional_study(tmp_path)
    fill = fill_workbooks(b.out, tmp_path / "returned")
    collect(b.out, fill.dir)
    text = paragraph(b, "Collection")
    assert "on the sheets returned by all 3 raters." in text
    assert "No scale was required" in text
    assert text.endswith("Collection was complete.")


def test_a_report_that_calls_itself_complete_with_a_sheet_missing_is_not_believed(tmp_path: Path) -> None:
    """A report written under the old rule (complete when 0 of 0 were received) must not be echoed."""
    b = optional_study(tmp_path)
    fill = fill_workbooks(b.out, tmp_path / "returned", skip_raters=("R3",))
    collect(b.out, fill.dir)
    edit_json(b.out / "collect_report.json", lambda report: report.update(complete=True))
    assert paragraph(b, "Collection").endswith("Collection was not complete.")


def test_unblinding_with_no_required_scale_is_told_by_sheets(tmp_path: Path) -> None:
    b = optional_study(tmp_path)
    fill = fill_workbooks(b.out, tmp_path / "one", skip_raters=("R2", "R3"))
    collect(b.out, fill.dir, created=T_COLLECT_1)
    unblind(b.out, allow_incomplete=True, unblinded_at=T_UNBLIND_1)
    text = paragraph(b, "Unblinding")
    assert "before a sheet had been received from every rater" in text
    assert "collection was not complete at that time" in text
    assert "0 of the 0" not in text and "all expected ratings" not in text

    fill = fill_workbooks(b.out, tmp_path / "all")
    collect(b.out, fill.dir, created=T_COLLECT_2)
    unblind(b.out, unblinded_at=T_UNBLIND_2)
    text = paragraph(b, "Unblinding")
    assert "before a sheet had been received from every rater" in text, "the first unblinding stays"
    assert f"run 2 times in all, most recently ({T_UNBLIND_2}), when collection was complete." in text
    assert "0 of the 0" not in text


def test_complete_unblinding_with_no_required_scale(tmp_path: Path) -> None:
    b = optional_study(tmp_path)
    fill = fill_workbooks(b.out, tmp_path / "all")
    collect(b.out, fill.dir, created=T_COLLECT_1)
    unblind(b.out, unblinded_at=T_UNBLIND_1)
    text = paragraph(b, "Unblinding")
    assert "after a sheet from every rater with assignments had been received and checked" in text
    assert "no scale was required, so no rating was expected" in text
    assert "all expected ratings" not in text


# ---- unblinding -------------------------------------------------------------------------------


def test_no_unblinding_record(built: Built, filled: Filled) -> None:
    collect(built.out, filled.dir)
    assert "holds no record that the software's unblinding step has been run" in paragraph(built, "Unblinding")


def test_first_unblinding_after_complete_collection(built: Built, filled: Filled) -> None:
    collect(built.out, filled.dir, created=T_COLLECT_1)
    unblind(built.out, unblinded_at=T_UNBLIND_1)
    text = paragraph(built, "Unblinding")
    assert f"({T_UNBLIND_1}), after all expected ratings had been received and checked." in text
    assert "after the first unblinding" not in text, "nothing was collected after the key was used"
    assert "[AUTHOR" not in text


def test_running_unblind_twice_without_collecting_again_adds_no_recollection_note(
    built: Built, filled: Filled
) -> None:
    collect(built.out, filled.dir, created=T_COLLECT_1)
    unblind(built.out, unblinded_at=T_UNBLIND_1)
    unblind(built.out, unblinded_at=T_UNBLIND_2)
    text = paragraph(built, "Unblinding")
    assert "run 2 times in all" in text
    assert "after the first unblinding" not in text


def test_recollection_after_the_first_unblinding_stays_in_the_text(built: Built, filled: Filled) -> None:
    """A second unblind must not remove the only sentence saying the data were checked again."""
    collect(built.out, filled.dir, created=T_COLLECT_1)
    unblind(built.out, unblinded_at=T_UNBLIND_1)
    collect(built.out, filled.dir, created=T_COLLECT_2)
    note = (
        f"The check of returned sheets reported above ({T_COLLECT_2}) was run after the codes had "
        "first been linked to sources, that is, after the first unblinding."
    )
    assert note in paragraph(built, "Unblinding"), "after collecting again"
    unblind(built.out, unblinded_at=T_UNBLIND_2)
    text = paragraph(built, "Unblinding")
    assert note in text, "after unblinding again"
    assert "does not record whether any rating was added or changed" in text
    assert "[AUTHOR: state whether any rating was added or changed after the first unblinding" in text
    assert f"({T_UNBLIND_1}), after all expected ratings had been received and checked." in text
    assert f"run 2 times in all, most recently ({T_UNBLIND_2})" in text


def test_early_unblinding_then_complete_collection_and_second_unblinding(
    built: Built, tmp_path: Path
) -> None:
    partial = fill_workbooks(built.out, tmp_path / "partial", skip_raters=("R3",))
    first = collect(built.out, partial.dir, created=T_COLLECT_1)
    unblind(built.out, allow_incomplete=True, unblinded_at=T_UNBLIND_1)
    early = (
        f"when {first.n_received} of the {first.n_expected} expected ratings had been received; "
        "collection was not complete at that time"
    )
    assert early in paragraph(built, "Unblinding")

    full = fill_workbooks(built.out, tmp_path / "full")
    second = collect(built.out, full.dir, created=T_COLLECT_2)
    assert second.complete
    unblind(built.out, unblinded_at=T_UNBLIND_2)
    text = paragraph(built, "Unblinding")
    assert early in text, "the first use of the key stays on record"
    assert f"with {second.n_received} of the {second.n_expected} expected ratings received." in text
    assert "after the first unblinding" in text
    assert paragraph(built, "Collection").endswith("Collection was complete.")


def test_failed_recollection_after_unblinding_is_still_reported(built: Built, filled: Filled) -> None:
    collect(built.out, filled.dir, created=T_COLLECT_1)
    unblind(built.out, unblinded_at=T_UNBLIND_1)
    edit_cell(filled.paths["R1"], 0, "text", "edited")
    assert not collect(built.out, filled.dir, created=T_COLLECT_2).ok
    assert "so no dataset was written" in paragraph(built, "Collection")
    text = paragraph(built, "Unblinding")
    assert T_UNBLIND_1 in text and "after the first unblinding" in text


# ---- collected again after the key was used: told by run, not by the clock ---------------------

RECOLLECTED = "was run after the codes had first been linked to sources, that is, after the first unblinding."


def test_recollection_in_the_same_clock_second_as_the_first_unblinding_is_reported(
    built: Built, filled: Filled
) -> None:
    """Commands run back to back share a second; the times then say nothing about their order."""
    collect(built.out, filled.dir, created=T_COLLECT_1)
    unblind(built.out, unblinded_at=T_COLLECT_1)
    assert RECOLLECTED not in paragraph(built, "Unblinding"), "the check came before the key was used"
    collect(built.out, filled.dir, created=T_COLLECT_1)
    assert RECOLLECTED in paragraph(built, "Unblinding"), "after collecting again"
    unblind(built.out, unblinded_at=T_COLLECT_1)
    text = paragraph(built, "Unblinding")
    assert RECOLLECTED in text, "after unblinding again"
    assert "[AUTHOR: state whether any rating was added or changed after the first unblinding" in text


def test_recollection_is_reported_when_the_clock_was_set_back(built: Built, filled: Filled) -> None:
    collect(built.out, filled.dir, created=T_COLLECT_2)
    unblind(built.out, unblinded_at=T_UNBLIND_2)
    collect(built.out, filled.dir, created=T_COLLECT_1)
    assert RECOLLECTED in paragraph(built, "Unblinding")


def test_no_recollection_is_reported_when_the_clock_was_set_back_before_unblinding(
    built: Built, filled: Filled
) -> None:
    """The report is dated after the unblinding, but it is the one the unblinding used."""
    collect(built.out, filled.dir, created=T_COLLECT_2)
    unblind(built.out, unblinded_at=T_UNBLIND_1)
    assert RECOLLECTED not in paragraph(built, "Unblinding")


def test_the_run_of_the_first_unblinding_is_kept_on_record(built: Built, filled: Filled) -> None:
    """What the comparison rests on: the record names the check that the first unblinding used."""
    collect(built.out, filled.dir, created=T_COLLECT_1)
    first_run = json.loads((built.out / "collect_report.json").read_text(encoding="utf-8"))["run_id"]
    unblind(built.out, unblinded_at=T_UNBLIND_1)
    collect(built.out, filled.dir, created=T_COLLECT_2)
    unblind(built.out, unblinded_at=T_UNBLIND_2)
    record = json.loads((built.out / "unblind_record.json").read_text(encoding="utf-8"))
    report = json.loads((built.out / "collect_report.json").read_text(encoding="utf-8"))
    assert record["first"]["collect_run_id"] == first_run != report["run_id"]


def without_run_ids(built: Built, *, report: bool = True, record: bool = True) -> None:
    """Make the records look as an earlier version of the tool wrote them."""
    if report:
        edit_json(built.out / "collect_report.json", lambda r: r.pop("run_id"))
    if record:

        def strip(r: dict) -> None:
            r.pop("collect_run_id")
            r["first"].pop("collect_run_id")

        edit_json(built.out / "unblind_record.json", strip)


@pytest.mark.parametrize(("report", "record"), [(True, True), (True, False), (False, True)])
def test_records_without_a_run_id_are_compared_by_time(
    built: Built, filled: Filled, report: bool, record: bool
) -> None:
    collect(built.out, filled.dir, created=T_COLLECT_1)
    unblind(built.out, unblinded_at=T_UNBLIND_1)
    without_run_ids(built, report=report, record=record)
    assert RECOLLECTED not in paragraph(built, "Unblinding"), "checked before the key was used"

    collect(built.out, filled.dir, created=T_COLLECT_2)
    without_run_ids(built, report=report, record=False)
    assert RECOLLECTED in paragraph(built, "Unblinding"), "checked after the key was used"


# ---- a record that lacks an entry is refused in words, not with a KeyError ----------------------

REPORT_ENTRIES = ["created", "ok", "complete", "n_expected", "n_received", "per_rater", "problems"]
RUN_ENTRIES = ["unblinded_at", "complete", "n_expected", "n_received"]


@pytest.mark.parametrize("entry", REPORT_ENTRIES)
def test_a_collect_report_without_an_entry_is_refused(built: Built, filled: Filled, entry: str) -> None:
    collect(built.out, filled.dir)
    edit_json(built.out / "collect_report.json", lambda report: report.pop(entry))
    with pytest.raises(RatersheetError) as caught:
        methods_text(built.out)
    message = str(caught.value)
    assert str(built.out / "collect_report.json") in message
    assert f"is missing the entry {entry!r}" in message
    assert "run 'ratersheet collect' again" in message


@pytest.mark.parametrize("entry", RUN_ENTRIES)
@pytest.mark.parametrize("first", [False, True], ids=["latest_run", "first_run"])
def test_an_unblinding_record_without_an_entry_is_refused(
    built: Built, filled: Filled, entry: str, first: bool
) -> None:
    collect(built.out, filled.dir)
    unblind(built.out)
    edit_json(
        built.out / "unblind_record.json",
        lambda record: (record["first"] if first else record).pop(entry),
    )
    with pytest.raises(RatersheetError) as caught:
        methods_text(built.out)
    message = str(caught.value)
    assert str(built.out / "unblind_record.json") in message
    assert f"is missing the entry {'first.' + entry if first else entry!r}" in message
    assert "Restore it from a copy of the study folder" in message
    assert "collect" not in message, "collecting again does not bring back when the key was first used"


def test_an_unblinding_record_from_before_first_was_kept_is_still_read(
    built: Built, filled: Filled
) -> None:
    collect(built.out, filled.dir, created=T_COLLECT_1)
    unblind(built.out, unblinded_at=T_UNBLIND_1)
    edit_json(built.out / "unblind_record.json", lambda record: record.pop("first"))
    assert f"({T_UNBLIND_1}), after all expected ratings had been received" in paragraph(built, "Unblinding")


def test_a_record_of_another_key_is_reported_as_that_before_its_entries_are_looked_at(
    built: Built, filled: Filled
) -> None:
    collect(built.out, filled.dir)

    def other_study(report: dict) -> None:
        report["key_sha256"] = "0" * 64
        del report["per_rater"]

    edit_json(built.out / "collect_report.json", other_study)
    with pytest.raises(IntegrityError, match="different key"):
        methods_text(built.out)


def test_methods_command_ends_with_an_error_line_for_a_record_without_an_entry(
    built: Built, filled: Filled, capsys: pytest.CaptureFixture[str]
) -> None:
    from ratersheet.cli import main

    collect(built.out, filled.dir)
    edit_json(built.out / "collect_report.json", lambda report: report.pop("per_rater"))
    assert main(["methods", str(built.out)]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ") and "is missing the entry 'per_rater'" in captured.err
    assert "Traceback" not in captured.err
    assert not (built.out / "methods.md").exists()
