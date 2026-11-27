"""Rules about what survives a re-run: the unblinding record, stale datasets, report times.

These rules were settled during integration, after the first draft of the contract:
the record of the first unblinding is evidence and must never be lost or overwritten,
and no unblinded dataset may be older than the collection report beside it.
"""

from __future__ import annotations

import json

from conftest import CREATED, SRC_B, Built, Filled, edit_cell, study_from_texts

from ratersheet import check_leaks, collect, methods_text, unblind

FIRST = "2026-11-27T09:30:00Z"
LATER = "2026-11-28T10:00:00Z"


def _record(built: Built) -> dict:
    return json.loads((built.out / "unblind_record.json").read_text(encoding="utf-8"))


def test_failing_collect_keeps_the_unblinding_record(built: Built, filled: Filled) -> None:
    assert collect(built.out, filled.dir).ok
    unblind(built.out, unblinded_at=FIRST)
    edit_cell(filled.paths["R1"], 0, "text", "tampered text")
    assert not collect(built.out, filled.dir).ok
    assert (built.out / "unblind_record.json").exists(), "the record of unblinding was erased"
    assert not (built.out / "ratings.csv").exists()
    assert FIRST in methods_text(built.out)


def test_second_unblinding_keeps_the_first_on_record(built: Built, filled: Filled) -> None:
    assert collect(built.out, filled.dir).ok
    unblind(built.out, unblinded_at=FIRST)
    assert collect(built.out, filled.dir).ok
    unblind(built.out, unblinded_at=LATER)
    record = _record(built)
    assert record["unblinded_at"] == LATER
    assert record["first"]["unblinded_at"] == FIRST
    assert record["times_unblinded"] == 2
    text = methods_text(built.out)
    assert FIRST in text and LATER in text and "2 times" in text


def test_first_incomplete_unblinding_stays_on_record(built: Built, filled: Filled) -> None:
    """Opening the key early cannot be hidden by unblinding again once collection is complete."""
    original = edit_and_remember(filled, "R1", 0, "accuracy")
    assert collect(built.out, filled.dir).ok
    unblind(built.out, allow_incomplete=True, unblinded_at=FIRST)
    edit_cell(filled.paths["R1"], 0, "accuracy", original)
    result = collect(built.out, filled.dir)
    assert result.ok and result.complete
    unblind(built.out, unblinded_at=LATER)
    record = _record(built)
    assert record["complete"] is True
    assert record["first"]["complete"] is False
    assert "collection was not complete at that time" in methods_text(built.out)


def edit_and_remember(filled: Filled, rater: str, row: int, scale: str) -> object:
    from conftest import get_cell

    original = get_cell(filled.paths[rater], row, scale)
    edit_cell(filled.paths[rater], row, scale, None)
    return original


def test_any_collect_run_removes_the_unblinded_files(built: Built, filled: Filled) -> None:
    assert collect(built.out, filled.dir).ok
    unblind(built.out)
    assert (built.out / "ratings.csv").exists()
    assert collect(built.out, filled.dir).ok
    assert not (built.out / "ratings.csv").exists(), "ratings.csv is older than the new report"
    assert not (built.out / "comments.csv").exists()
    assert (built.out / "ratings_blinded.csv").exists()


def test_collect_report_time_can_be_fixed(built: Built, filled: Filled) -> None:
    collect(built.out, filled.dir, created=CREATED)
    report = json.loads((built.out / "collect_report.json").read_text(encoding="utf-8"))
    assert report["created"] == CREATED


def test_model_family_names_match_their_variants() -> None:
    for variant in ("GPT-4o", "gpt-4.1", "GPT-5-mini", "GPT-3.5"):
        texts = [f"Answer from {variant} follows."] + [f"Plain synthetic sentence number {i}." for i in range(5)]
        study = study_from_texts({"srcone": texts, SRC_B: [f"Other plain sentence {i}." for i in range(6)]})
        codes = [warning.code for warning in check_leaks(study)]
        assert "self_identification" in codes, f"{variant} was not flagged"


def test_cli_says_why_a_collection_with_every_counted_rating_is_incomplete(tmp_path, capsys) -> None:
    """With no required scale, 0 of 0 ratings is not 'complete' while a sheet is outstanding."""
    from conftest import fill_workbooks, make_built

    from ratersheet.cli import main

    optional = [{"id": "tone", "label": "Tone", "type": "ordinal", "min": 1, "max": 3, "required": False}]
    b = make_built(tmp_path, scales=optional)
    fill = fill_workbooks(b.out, tmp_path / "returned", skip_raters=(b.study.raters[-1],))
    assert main(["collect", str(b.out), str(fill.dir)]) == 0
    out = capsys.readouterr().out
    assert "incomplete: at least one rater's sheet has not been returned" in out


def test_items_file_with_a_nul_character_is_a_config_error(tmp_path) -> None:
    import pytest
    from conftest import write_study

    from ratersheet import ConfigError, load_study

    path = write_study(tmp_path, items_text="item_id,source,text\na1,srcone,bad\x00text\na2,srcone,fine\n")
    with pytest.raises(ConfigError):
        load_study(path)


# ---- which collection check each unblinding followed ---------------------------------------------


def _run_id(built: Built) -> str:
    return json.loads((built.out / "collect_report.json").read_text(encoding="utf-8"))["run_id"]


def test_first_unblinding_keeps_the_id_of_the_check_it_followed(built: Built, filled: Filled) -> None:
    """Both steps can fall in one clock second, so the ids, not the times, tell the checks apart."""
    same_second = FIRST
    assert collect(built.out, filled.dir, created=same_second).ok
    first_check = _run_id(built)
    unblind(built.out, unblinded_at=same_second)
    assert _record(built)["collect_run_id"] == first_check == _record(built)["first"]["collect_run_id"]

    assert collect(built.out, filled.dir, created=same_second).ok
    second_check = _run_id(built)
    assert second_check != first_check
    assert _record(built)["first"]["collect_run_id"] == first_check, "collect must not touch the record"
    unblind(built.out, unblinded_at=same_second)
    record = _record(built)
    assert record["collect_run_id"] == second_check
    assert record["first"]["collect_run_id"] == first_check, "the first unblinding followed the first check"
    assert record["times_unblinded"] == 2


def test_record_without_a_first_entry_carries_its_check_id_into_first(built: Built, filled: Filled) -> None:
    assert collect(built.out, filled.dir).ok
    unblind(built.out, unblinded_at=FIRST)
    first_check = _run_id(built)
    path = built.out / "unblind_record.json"
    flat = {name: value for name, value in _record(built).items() if name != "first"}
    path.write_text(json.dumps(flat), encoding="utf-8")
    assert collect(built.out, filled.dir).ok
    unblind(built.out, unblinded_at=LATER)
    first = _record(built)["first"]
    assert first["collect_run_id"] == first_check and first["unblinded_at"] == FIRST
