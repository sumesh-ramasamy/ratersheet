"""collect: round trip, reordering, every problem code, value rules, outputs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

import ratersheet
from ratersheet import (
    CollectResult,
    IntegrityError,
    Problem,
    RatersheetError,
    collect,
    file_sha256,
    unblind,
)
from conftest import (
    DEFAULT_BUILD_KWARGS,
    OPTIONAL_SCALES,
    SEED,
    SRC_A,
    SRC_B,
    Built,
    Filled,
    clear_row,
    codes,
    default_value,
    edit_cell,
    edit_meta,
    expected_scale_count,
    fill_workbooks,
    get_cell,
    make_built,
    make_item_rows,
    problems_with,
    read_csv,
    read_csv_dicts,
    reorder_rows,
)

RATINGS_HEADER = ["item_code", "rater_id", "scale", "value", "rating_id", "position"]
COMMENTS_HEADER = ["item_code", "rater_id", "rating_id", "comment"]
STALE = ["ratings_blinded.csv", "comments_blinded.csv", "ratings.csv", "comments.csv"]


def expected_ratings_rows(b: Built, values: dict[str, dict[str, Any]]) -> list[list[str]]:
    items = {i["item_id"]: i for i in b.key["items"]}
    order = {r: i for i, r in enumerate(b.manifest["raters"])}
    asg = sorted(b.key["assignments"], key=lambda a: (order[a["rater_id"]], a["position"]))
    rows: list[list[str]] = []
    for a in asg:
        for sc in b.manifest["scales"]:
            v = values.get(a["rating_id"], {}).get(sc["id"])
            rows.append(
                [items[a["item_id"]]["item_code"], a["rater_id"], sc["id"], "" if v is None else str(v),
                 a["rating_id"], str(a["position"])]
            )
    return rows


def n_expected(b: Built) -> int:
    return len(b.key["assignments"]) * expected_scale_count(b.manifest)


# ---- clean round trip ------------------------------------------------------------------------


def test_clean_round_trip(built: Built, filled: Filled) -> None:
    res = collect(built.out, filled.dir)
    assert isinstance(res, CollectResult)
    assert res.problems == (), [(p.code, p.message) for p in res.problems]
    assert res.ok is True and res.complete is True
    assert res.n_expected == 28 * 2 == n_expected(built)
    assert res.n_received == res.n_expected
    assert Path(res.report_path) == built.out / "collect_report.json"
    assert Path(res.ratings_path) == built.out / "ratings_blinded.csv"
    assert Path(res.comments_path) == built.out / "comments_blinded.csv"


def test_ratings_csv_header_order_and_values(built: Built, filled: Filled) -> None:
    res = collect(built.out, filled.dir)
    header, rows = read_csv(res.ratings_path)
    assert header == RATINGS_HEADER
    assert rows == expected_ratings_rows(built, filled.values)
    assert len(rows) == 28 * 2


def test_comments_csv(built: Built, filled: Filled) -> None:
    res = collect(built.out, filled.dir)
    header, rows = read_csv(res.comments_path)
    assert header == COMMENTS_HEADER
    items = {i["item_id"]: i["item_code"] for i in built.key["items"]}
    asg = {a["rating_id"]: a for a in built.key["assignments"]}
    expected = sorted(
        [items[asg[rid]["item_id"]], asg[rid]["rater_id"], rid, text] for rid, text in filled.comments.items()
    )
    assert filled.comments, "the helper should have written some comments"
    assert sorted(rows) == expected


def test_no_comments_csv_when_comments_are_disabled(tmp_path: Path) -> None:
    kw = dict(DEFAULT_BUILD_KWARGS, comments=False)
    b = make_built(tmp_path, **kw)
    fill = fill_workbooks(b.out, tmp_path / "ret")
    res = collect(b.out, fill.dir)
    assert res.ok and res.comments_path is None
    assert not (b.out / "comments_blinded.csv").exists()


def test_report_json(built: Built, filled: Filled) -> None:
    collect(built.out, filled.dir)
    rep = json.loads((built.out / "collect_report.json").read_text(encoding="utf-8"))
    assert set(rep) == {
        "created", "tool_version", "key_sha256", "n_expected", "n_received", "complete", "ok",
        "require_complete", "per_rater", "problems",
        "run_id", "ratings_blinded_sha256", "comments_blinded_sha256",
    }
    assert rep["key_sha256"] == built.manifest["key_sha256"] == file_sha256(built.out / "private" / "key.json")
    assert rep["tool_version"] == ratersheet.__version__
    assert rep["n_expected"] == rep["n_received"] == 56
    assert rep["complete"] is True and rep["ok"] is True and rep["require_complete"] is False
    assert rep["problems"] == []
    assert set(rep["per_rater"]) == {"R1", "R2", "R3"}
    for r, e in rep["per_rater"].items():
        assert e == {"expected": built.manifest["per_rater"][r] * 2, "received": built.manifest["per_rater"][r] * 2}


def test_collect_is_repeatable(built: Built, filled: Filled) -> None:
    collect(built.out, filled.dir)
    first = (built.out / "ratings_blinded.csv").read_bytes()
    collect(built.out, filled.dir)
    assert (built.out / "ratings_blinded.csv").read_bytes() == first


def test_collect_does_not_need_the_study_files(built: Built, filled: Filled) -> None:
    shutil.rmtree(built.folder)
    assert collect(built.out, filled.dir).ok


def test_csv_files_use_lf_newlines_and_utf8(built: Built, filled: Filled) -> None:
    collect(built.out, filled.dir)
    for name in ("ratings_blinded.csv", "comments_blinded.csv"):
        raw = (built.out / name).read_bytes()
        raw.decode("utf-8")
        assert b"\r" not in raw, name


def test_blinded_csvs_contain_no_source_item_or_case_ids(built: Built, filled: Filled) -> None:
    collect(built.out, filled.dir)
    needles = {i.source for i in built.study.items} | {i.item_id for i in built.study.items}
    needles |= {i.case_id for i in built.study.items if i.case_id}
    for name in ("ratings_blinded.csv", "comments_blinded.csv"):
        text = (built.out / name).read_text(encoding="utf-8")
        for n in needles:
            assert n not in text, f"{name} contains {n!r}"


# ---- reordering and how files are given -----------------------------------------------------


def test_rows_reordered_by_the_rater_still_collect(built: Built, filled: Filled) -> None:
    baseline = collect(built.out, filled.dir)
    base_csv = baseline.ratings_path.read_bytes()
    base_comments = sorted(read_csv(baseline.comments_path)[1])
    n1, n2 = built.manifest["per_rater"]["R1"], built.manifest["per_rater"]["R2"]
    reorder_rows(filled.paths["R1"], list(reversed(range(n1))))
    reorder_rows(filled.paths["R2"], [(i + 3) % n2 for i in range(n2)])
    res = collect(built.out, filled.dir)
    assert res.problems == (), [(p.code, p.rating_id) for p in res.problems]
    assert res.ok and res.complete
    assert res.ratings_path.read_bytes() == base_csv, "CSV order must follow the key, not the sheet"
    assert sorted(read_csv(res.comments_path)[1]) == base_comments


def test_returned_may_be_a_list_of_files_and_folders(built: Built, filled: Filled, tmp_path: Path) -> None:
    extra = tmp_path / "second"
    extra.mkdir()
    moved = shutil.move(str(filled.paths["R3"]), str(extra / "R3.xlsx"))
    res = collect(built.out, [filled.dir, Path(moved)])
    assert res.ok and res.complete
    res2 = collect(built.out, iter([str(filled.paths["R1"]), str(filled.paths["R2"]), moved]))
    assert res2.ok and res2.complete


def test_folder_scan_skips_lock_files_other_files_and_subfolders(built: Built, filled: Filled) -> None:
    (filled.dir / "~$R1.xlsx").write_bytes(b"\x00 lock file")
    (filled.dir / "notes.txt").write_text("not a workbook", encoding="utf-8")
    sub = filled.dir / "nested"
    sub.mkdir()
    shutil.copy(filled.paths["R1"], sub / "R1_nested.xlsx")
    res = collect(built.out, filled.dir)
    assert res.problems == (), [(p.code, p.file) for p in res.problems]
    assert res.ok and res.complete


def test_single_file_gives_missing_workbook_for_the_rest(built: Built, filled: Filled) -> None:
    res = collect(built.out, filled.paths["R1"])
    assert res.ok and not res.complete
    assert sorted(p.rater_id for p in problems_with(res, "missing_workbook")) == ["R2", "R3"]


def test_str_paths_are_accepted(built: Built, filled: Filled) -> None:
    assert collect(str(built.out), str(filled.dir)).ok


# ---- integrity of the study folder -----------------------------------------------------------


def test_collect_refuses_when_key_was_edited(built: Built, filled: Filled) -> None:
    key = built.out / "private" / "key.json"
    key.write_text(key.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(IntegrityError):
        collect(built.out, filled.dir)
    assert not (built.out / "ratings_blinded.csv").exists()


def test_collect_on_a_missing_study_folder(tmp_path: Path, filled: Filled) -> None:
    with pytest.raises(RatersheetError):
        collect(tmp_path / "does-not-exist", filled.dir)


def test_collect_without_manifest(built: Built, filled: Filled) -> None:
    (built.out / "manifest.json").unlink()
    with pytest.raises(RatersheetError):
        collect(built.out, filled.dir)


# ---- one test per problem code ---------------------------------------------------------------


def test_not_a_ratersheet_garbage_file(built: Built, filled: Filled) -> None:
    (filled.dir / "garbage.xlsx").write_bytes(b"this is not a zip file")
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "not_a_ratersheet")
    assert len(ps) == 1 and ps[0].severity == "error" and ps[0].file == "garbage.xlsx"
    assert res.ok is False
    assert res.ratings_path is None and not (built.out / "ratings_blinded.csv").exists()


def test_not_a_ratersheet_plain_workbook(built: Built, filled: Filled) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active["A1"] = "hello"
    wb.save(filled.dir / "plain.xlsx")
    res = collect(built.out, filled.dir)
    assert [p.file for p in problems_with(res, "not_a_ratersheet")] == ["plain.xlsx"]
    assert not res.ok


def test_wrong_study_other_study_sheet(built: Built, filled: Filled, tmp_path: Path) -> None:
    other = make_built(tmp_path, name="proj2", out="out2", nonce="9" * 64, **dict(DEFAULT_BUILD_KWARGS, seed=SEED + 1))
    fill_other = fill_workbooks(other.out, tmp_path / "ret2")
    shutil.copy(fill_other.paths["R1"], filled.paths["R1"])
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "wrong_study")
    assert len(ps) == 1 and ps[0].severity == "error" and ps[0].file == "R1.xlsx"
    assert not res.ok
    assert res.n_received < res.n_expected, "a wrong-study sheet must not count as received"


def test_wrong_study_tampered_commitment(built: Built, filled: Filled) -> None:
    edit_meta(filled.paths["R2"], key_sha256="0" * 64)
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "wrong_study")
    assert len(ps) == 1 and ps[0].file == "R2.xlsx"
    assert not res.ok


def test_unknown_rater(built: Built, filled: Filled) -> None:
    edit_meta(filled.paths["R1"], rater_id="ZZ9")
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "unknown_rater")
    assert len(ps) == 1 and ps[0].severity == "error" and ps[0].file == "R1.xlsx"
    assert not res.ok


def test_duplicate_workbook(built: Built, filled: Filled) -> None:
    shutil.copy(filled.paths["R1"], filled.dir / "R1_copy.xlsx")
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "duplicate_workbook")
    assert len(ps) == 1 and ps[0].severity == "error" and ps[0].rater_id == "R1"
    assert not res.ok
    assert res.n_received == res.n_expected - built.manifest["per_rater"]["R1"] * 2, "none of R1's files are used"


def test_duplicate_workbook_reported_once_per_extra_file(built: Built, filled: Filled) -> None:
    shutil.copy(filled.paths["R2"], filled.dir / "copy_a.xlsx")
    shutil.copy(filled.paths["R2"], filled.dir / "copy_b.xlsx")
    res = collect(built.out, filled.dir)
    assert len(problems_with(res, "duplicate_workbook")) == 2
    assert not res.ok


def test_unknown_rating_id(built: Built, filled: Filled) -> None:
    removed = get_cell(filled.paths["R1"], 2, "rating_id")
    edit_cell(filled.paths["R1"], 2, "rating_id", "ZZZZ-ZZZZ", force_string=True)
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "unknown_rating_id")
    assert len(ps) == 1 and ps[0].severity == "error" and ps[0].rating_id == "ZZZZ-ZZZZ" and ps[0].file == "R1.xlsx"
    assert [p.rating_id for p in problems_with(res, "missing_row")] == [removed]
    assert not res.ok


def test_wrong_rater(built: Built, filled: Filled) -> None:
    others = [a["rating_id"] for a in built.key["assignments"] if a["rater_id"] == "R2"]
    original = get_cell(filled.paths["R1"], 1, "rating_id")
    edit_cell(filled.paths["R1"], 1, "rating_id", others[0], force_string=True)
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "wrong_rater")
    assert len(ps) == 1 and ps[0].severity == "error" and ps[0].rating_id == others[0]
    assert ps[0].file == "R1.xlsx"
    assert original in [p.rating_id for p in problems_with(res, "missing_row")]
    assert not res.ok


def test_duplicate_rating_id(built: Built, filled: Filled) -> None:
    first = get_cell(filled.paths["R3"], 0, "rating_id")
    displaced = get_cell(filled.paths["R3"], 1, "rating_id")
    edit_cell(filled.paths["R3"], 1, "rating_id", first, force_string=True)
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "duplicate_rating_id")
    assert len(ps) >= 1 and all(p.severity == "error" and p.rating_id == first for p in ps)
    assert displaced in [p.rating_id for p in problems_with(res, "missing_row")]
    assert not res.ok


def test_missing_row(built: Built, filled: Filled) -> None:
    gone = clear_row(filled.paths["R2"], -1)
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "missing_row")
    assert len(ps) == 1 and ps[0].severity == "error" and ps[0].rating_id == gone and ps[0].rater_id == "R2"
    assert not res.ok and res.ratings_path is None


def test_text_changed(built: Built, filled: Filled) -> None:
    rid = get_cell(filled.paths["R1"], 3, "rating_id")
    edit_cell(filled.paths["R1"], 3, "text", get_cell(filled.paths["R1"], 3, "text") + " (edited by the rater)")
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "text_changed")
    assert len(ps) == 1 and ps[0].severity == "error" and ps[0].rating_id == rid
    assert ps[0].rater_id == "R1" and ps[0].file == "R1.xlsx"
    assert not res.ok


def test_context_changed(built: Built, filled: Filled) -> None:
    rid = get_cell(filled.paths["R3"], 0, "rating_id")
    edit_cell(filled.paths["R3"], 0, "context", "A different context entirely")
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "context_changed")
    assert len(ps) == 1 and ps[0].severity == "error" and ps[0].rating_id == rid
    assert not res.ok


def test_whitespace_only_differences_in_text_are_tolerated(built: Built, filled: Filled) -> None:
    t = get_cell(filled.paths["R1"], 0, "text")
    edit_cell(filled.paths["R1"], 0, "text", "  " + t + "   \n")
    res = collect(built.out, filled.dir)
    assert "text_changed" not in codes(res)
    assert res.ok


def test_unicode_normal_form_differences_are_tolerated(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    rows[0]["text"] = "Caf\u00e9 au lait \u2014 synthetic"
    b = make_built(tmp_path, rows=rows, rpi=1, raters=["R1", "R2"])
    fill = fill_workbooks(b.out, tmp_path / "ret")
    for r in ("R1", "R2"):
        from conftest import data_row_numbers, open_sheet

        wb, ws, meta, cmap = open_sheet(fill.paths[r])
        for row in data_row_numbers(ws, cmap):
            if ws.cell(row, cmap["text"]).value.startswith("Caf"):
                ws.cell(row, cmap["text"]).value = "Cafe\u0301 au lait \u2014 synthetic"
        wb.save(fill.paths[r])
    assert collect(b.out, fill.dir).ok


# ---- value rules ---------------------------------------------------------------------------


def stored_value(res: CollectResult, built: Built, rid: str, scale: str) -> str:
    for r in read_csv_dicts(res.ratings_path):
        if r["rating_id"] == rid and r["scale"] == scale:
            return r["value"]
    raise AssertionError(f"no row for {rid} {scale}")


@pytest.mark.parametrize("raw", [4, 4.0, "4", " 4 ", "\t4\n"])
def test_ordinal_values_accepted(built: Built, filled: Filled, raw: Any) -> None:
    rid = get_cell(filled.paths["R1"], 0, "rating_id")
    edit_cell(filled.paths["R1"], 0, "accuracy", raw)
    res = collect(built.out, filled.dir)
    assert res.ok and res.complete, [(p.code, p.message) for p in res.problems]
    assert stored_value(res, built, rid, "accuracy") == "4"


@pytest.mark.parametrize("raw,stored", [("MINOR", "minor"), (" Serious ", "serious"), ("none", "none"), ("None", "none")])
def test_nominal_values_accepted_and_stored_in_scale_spelling(
    built: Built, filled: Filled, raw: str, stored: str
) -> None:
    rid = get_cell(filled.paths["R2"], 1, "rating_id")
    edit_cell(filled.paths["R2"], 1, "harm", raw)
    res = collect(built.out, filled.dir)
    assert res.ok and res.complete, [(p.code, p.message) for p in res.problems]
    assert stored_value(res, built, rid, "harm") == stored


@pytest.mark.parametrize("raw", [True, False, 3.5, 4.5, 0, 6, -1, 10, "abc", "4.5", "4.0", "four", "=2+2", "=4", "4 5", "1,2"])
def test_ordinal_values_rejected(built: Built, filled: Filled, raw: Any) -> None:
    rid = get_cell(filled.paths["R1"], 2, "rating_id")
    edit_cell(filled.paths["R1"], 2, "accuracy", raw)
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "invalid_value")
    assert len(ps) == 1, f"value {raw!r} should give exactly one invalid_value, got {codes(res)}"
    p = ps[0]
    assert p.severity == "error" and p.rating_id == rid and p.scale == "accuracy" and p.rater_id == "R1"
    assert not res.ok and res.ratings_path is None


@pytest.mark.parametrize("raw", [True, 3, 1.0, "other", "=\"none\"", "none,minor", "mino", "seriou s"])
def test_nominal_values_rejected(built: Built, filled: Filled, raw: Any) -> None:
    rid = get_cell(filled.paths["R1"], 2, "rating_id")
    edit_cell(filled.paths["R1"], 2, "harm", raw)
    res = collect(built.out, filled.dir)
    ps = problems_with(res, "invalid_value")
    assert len(ps) == 1, f"value {raw!r} should give exactly one invalid_value, got {codes(res)}"
    assert ps[0].rating_id == rid and ps[0].scale == "harm" and ps[0].severity == "error"
    assert not res.ok


@pytest.mark.parametrize("raw", [None, "", "   ", "\t \n"])
def test_empty_and_blank_cells_are_missing_values(built: Built, filled: Filled, raw: Any) -> None:
    rid = get_cell(filled.paths["R1"], 0, "rating_id")
    edit_cell(filled.paths["R1"], 0, "accuracy", raw)
    res = collect(built.out, filled.dir)
    assert codes(res) == ["missing_value"], codes(res)
    p = res.problems[0]
    assert p.severity == "warning" and p.rating_id == rid and p.scale == "accuracy" and p.rater_id == "R1"
    assert res.ok is True and res.complete is False
    assert res.n_received == res.n_expected - 1
    assert stored_value(res, built, rid, "accuracy") == ""


def test_require_complete_turns_warnings_into_errors(built: Built, filled: Filled) -> None:
    edit_cell(filled.paths["R1"], 0, "accuracy", None)
    res = collect(built.out, filled.dir, require_complete=True)
    ps = problems_with(res, "missing_value")
    assert len(ps) == 1 and ps[0].severity == "error"
    assert res.ok is False and res.complete is False
    assert res.ratings_path is None and not (built.out / "ratings_blinded.csv").exists()
    rep = json.loads((built.out / "collect_report.json").read_text(encoding="utf-8"))
    assert rep["require_complete"] is True and rep["ok"] is False and rep["problems"][0]["severity"] == "error"


def test_require_complete_on_a_complete_set_is_fine(built: Built, filled: Filled) -> None:
    res = collect(built.out, filled.dir, require_complete=True)
    assert res.ok and res.complete and res.ratings_path is not None


# ---- missing workbook ------------------------------------------------------------------------


def test_missing_workbook_is_a_warning_and_csv_still_written(built: Built, tmp_path: Path) -> None:
    fill = fill_workbooks(built.out, tmp_path / "partial", skip_raters=("R3",))
    res = collect(built.out, fill.dir)
    ps = problems_with(res, "missing_workbook")
    assert len(ps) == 1 and ps[0].severity == "warning" and ps[0].rater_id == "R3"
    assert res.ok is True and res.complete is False
    assert res.n_received == res.n_expected - built.manifest["per_rater"]["R3"] * 2
    header, rows = read_csv(res.ratings_path)
    assert header == RATINGS_HEADER
    assert rows == expected_ratings_rows(built, fill.values), "all assignments appear; missing ones have empty values"
    assert any(r[1] == "R3" and r[3] == "" for r in rows)


def test_missing_workbook_with_require_complete(built: Built, tmp_path: Path) -> None:
    fill = fill_workbooks(built.out, tmp_path / "partial", skip_raters=("R3",))
    res = collect(built.out, fill.dir, require_complete=True)
    ps = problems_with(res, "missing_workbook")
    assert len(ps) == 1 and ps[0].severity == "error"
    assert not res.ok and res.ratings_path is None and not (built.out / "ratings_blinded.csv").exists()


def test_empty_returned_folder_means_everything_is_missing(built: Built, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    res = collect(built.out, empty)
    assert sorted(p.rater_id for p in problems_with(res, "missing_workbook")) == ["R1", "R2", "R3"]
    assert res.n_received == 0 and res.complete is False and res.ok is True


# ---- optional scales ------------------------------------------------------------------------


def test_optional_scale_may_stay_empty(tmp_path: Path) -> None:
    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, scales=OPTIONAL_SCALES))
    fill = fill_workbooks(
        b.out, tmp_path / "ret",
        value_fn=lambda sc, rid: None if sc["id"] == "tone" else default_value(sc, rid),
    )
    # tone is empty everywhere; only required scales count towards completeness
    res = collect(b.out, fill.dir)
    assert res.problems == (), [(p.code, p.scale) for p in res.problems]
    assert res.ok and res.complete
    assert res.n_expected == 28 * 2 == res.n_received
    header, rows = read_csv(res.ratings_path)
    assert len(rows) == 28 * 3, "optional scales are written too"
    assert [r[2] for r in rows[:3]] == ["accuracy", "harm", "tone"]
    assert all(r[3] == "" for r in rows if r[2] == "tone")


def test_optional_scale_values_are_written_and_validated(tmp_path: Path) -> None:
    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, scales=OPTIONAL_SCALES))
    fill = fill_workbooks(b.out, tmp_path / "ret")
    res = collect(b.out, fill.dir)
    assert res.ok and res.complete
    _, rows = read_csv(res.ratings_path)
    got = {(r[4], r[2]): r[3] for r in rows}
    assert all(got[(rid, "tone")] == str(v["tone"]) for rid, v in fill.values.items())
    edit_cell(fill.paths["R1"], 0, "tone", 9)
    res2 = collect(b.out, fill.dir)
    assert [p.scale for p in problems_with(res2, "invalid_value")] == ["tone"]
    assert not res2.ok


# ---- stale outputs ---------------------------------------------------------------------------


def test_errors_delete_stale_outputs_and_write_no_csv(built: Built, filled: Filled) -> None:
    assert collect(built.out, filled.dir).ok
    unblind(built.out)
    for name in STALE:
        assert (built.out / name).exists(), f"setup: {name} should exist"
    edit_cell(filled.paths["R1"], 0, "text", "tampered text")
    res = collect(built.out, filled.dir)
    assert not res.ok
    assert res.ratings_path is None and res.comments_path is None
    for name in STALE:
        assert not (built.out / name).exists(), f"stale {name} left beside a failing report"
    assert (built.out / "collect_report.json").exists()
    rep = json.loads((built.out / "collect_report.json").read_text(encoding="utf-8"))
    assert rep["ok"] is False and any(p["code"] == "text_changed" for p in rep["problems"])


def test_report_problem_entries_have_all_fields(built: Built, filled: Filled) -> None:
    edit_cell(filled.paths["R1"], 0, "accuracy", "nope")
    collect(built.out, filled.dir)
    rep = json.loads((built.out / "collect_report.json").read_text(encoding="utf-8"))
    assert rep["problems"], "expected a problem in the report"
    for p in rep["problems"]:
        assert set(p) == {"severity", "code", "message", "file", "rater_id", "rating_id", "scale"}
        assert p["message"]
    p = rep["problems"][0]
    assert (p["code"], p["severity"], p["file"], p["rater_id"], p["scale"]) == (
        "invalid_value", "error", "R1.xlsx", "R1", "accuracy")


def test_problem_is_a_dataclass_with_the_contract_fields(built: Built, filled: Filled) -> None:
    edit_cell(filled.paths["R1"], 0, "accuracy", "nope")
    res = collect(built.out, filled.dir)
    p = res.problems[0]
    assert isinstance(p, Problem) and isinstance(res.problems, tuple)
    assert p.message and isinstance(p.message, str)


def test_a_later_clean_collect_replaces_a_failing_report(built: Built, filled: Filled) -> None:
    backup = filled.paths["R1"].read_bytes()
    edit_cell(filled.paths["R1"], 0, "accuracy", "nope")
    assert not collect(built.out, filled.dir).ok
    filled.paths["R1"].write_bytes(backup)
    res = collect(built.out, filled.dir)
    assert res.ok
    rep = json.loads((built.out / "collect_report.json").read_text(encoding="utf-8"))
    assert rep["ok"] is True and rep["problems"] == []
    assert (built.out / "ratings_blinded.csv").exists()


def test_created_timestamp_in_report(built: Built, filled: Filled) -> None:
    import re

    collect(built.out, filled.dir)
    rep = json.loads((built.out / "collect_report.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", rep["created"]), rep["created"]


# ---- completeness when no scale is required ---------------------------------------------------

ALL_OPTIONAL = [dict(sc, required=False) for sc in OPTIONAL_SCALES]


def report_of(b: Built) -> dict:
    return json.loads((b.out / "collect_report.json").read_text(encoding="utf-8"))


def test_nothing_returned_is_not_complete_even_if_no_rating_is_required(tmp_path: Path) -> None:
    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, scales=ALL_OPTIONAL))
    empty = tmp_path / "empty"
    empty.mkdir()
    res = collect(b.out, empty)
    assert res.n_expected == 0 and res.n_received == 0, "no scale is required, so no rating is expected"
    assert sorted(p.rater_id for p in problems_with(res, "missing_workbook")) == ["R1", "R2", "R3"]
    assert res.ok is True
    assert res.complete is False, "no sheet has come back yet"
    assert report_of(b)["complete"] is False


def test_one_sheet_outstanding_is_not_complete_even_if_no_rating_is_required(tmp_path: Path) -> None:
    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, scales=ALL_OPTIONAL))
    fill = fill_workbooks(b.out, tmp_path / "ret", skip_raters=("R2",))
    res = collect(b.out, fill.dir)
    assert res.ok is True and res.n_expected == 0
    assert res.complete is False and report_of(b)["complete"] is False


def test_all_sheets_back_is_complete_when_no_rating_is_required(tmp_path: Path) -> None:
    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, scales=ALL_OPTIONAL))
    fill = fill_workbooks(b.out, tmp_path / "ret", value_fn=lambda sc, rid: None)
    res = collect(b.out, fill.dir)
    assert res.problems == () and res.ok is True
    assert res.complete is True and report_of(b)["complete"] is True


def test_a_rater_without_assignments_does_not_hold_up_completeness(tmp_path: Path) -> None:
    # 2 items, 1 rater per item, 3 raters: at least one rater has nothing to rate.
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 1, SRC_B: 1}), raters=["R1", "R2", "R3"], rpi=1)
    idle = tuple(r for r, n in b.manifest["per_rater"].items() if n == 0)
    assert idle, "setup: two assignments cannot occupy three raters"
    fill = fill_workbooks(b.out, tmp_path / "ret", skip_raters=idle)
    res = collect(b.out, fill.dir)
    assert res.problems == () and res.complete is True


# ---- the hidden column description must be the one build wrote --------------------------------


def set_meta_columns(path: Path, change: Any) -> None:
    from conftest import open_sheet

    _, _, meta, _ = open_sheet(path)
    columns = json.loads(json.dumps(meta["columns"]))
    change(columns)
    edit_meta(path, columns=columns)


def assert_altered_sheet_is_refused(res: CollectResult, b: Built, file: str) -> None:
    ps = problems_with(res, "not_a_ratersheet")
    assert [(p.file, p.severity) for p in ps] == [(file, "error")], [(p.code, p.message) for p in res.problems]
    assert "hidden sheet has been altered" in ps[0].message
    assert res.ok is False and res.ratings_path is None
    assert not (b.out / "ratings_blinded.csv").exists() and not (b.out / "comments_blinded.csv").exists()


def test_swapped_scale_ids_in_the_hidden_sheet_are_refused(tmp_path: Path) -> None:
    # accuracy is 1..5 and tone 1..3; every rating is 1..3, so each column is valid for both.
    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, scales=OPTIONAL_SCALES))
    fill = fill_workbooks(
        b.out, tmp_path / "ret",
        value_fn=lambda sc, rid: "none" if sc["id"] == "harm" else (2 if sc["id"] == "accuracy" else 1),
    )

    def swap(columns: list[dict]) -> None:
        by_id = {c.get("scale_id"): c for c in columns}
        by_id["accuracy"]["scale_id"], by_id["tone"]["scale_id"] = "tone", "accuracy"

    set_meta_columns(fill.paths["R1"], swap)
    assert_altered_sheet_is_refused(collect(b.out, fill.dir), b, "R1.xlsx")


def test_text_check_cannot_be_pointed_at_another_column(built: Built, filled: Filled) -> None:
    from conftest import data_row_numbers, open_sheet

    wb, ws, meta, cmap = open_sheet(filled.paths["R2"])
    spare = ws.max_column + 1
    ws.cell(1, spare).value = "hidden original"
    for r in data_row_numbers(ws, cmap):
        ws.cell(r, spare).value = ws.cell(r, cmap["text"]).value
        ws.cell(r, cmap["text"]).value = "REWRITTEN TEXT THE RATER ACTUALLY SAW"
    wb.save(filled.paths["R2"])

    def redirect(columns: list[dict]) -> None:
        next(c for c in columns if c["role"] == "text")["header"] = "hidden original"

    set_meta_columns(filled.paths["R2"], redirect)
    assert_altered_sheet_is_refused(collect(built.out, filled.dir), built, "R2.xlsx")


def test_comment_column_cannot_be_dropped_from_the_hidden_sheet(built: Built, filled: Filled) -> None:
    set_meta_columns(filled.paths["R3"], lambda columns: columns.pop())
    assert_altered_sheet_is_refused(collect(built.out, filled.dir), built, "R3.xlsx")


def test_moving_a_column_in_the_ratings_sheet_is_still_fine(built: Built, filled: Filled) -> None:
    import openpyxl

    wb = openpyxl.load_workbook(filled.paths["R1"])
    wb["Ratings"].insert_cols(1)
    wb.save(filled.paths["R1"])
    res = collect(built.out, filled.dir)
    assert res.problems == () and res.ok and res.complete


# ---- stray cells and stray files ----------------------------------------------------------------


@pytest.mark.parametrize("blank", [" ", " ", "  \t"])
def test_blank_looking_cell_below_the_last_row_does_not_fail_collection(
    built: Built, filled: Filled, blank: str
) -> None:
    from conftest import open_sheet

    wb, ws, meta, cmap = open_sheet(filled.paths["R1"])
    ws.cell(ws.max_row + 1, cmap["accuracy"]).value = blank
    ws.cell(ws.max_row + 4, cmap["comment"]).value = blank
    wb.save(filled.paths["R1"])
    res = collect(built.out, filled.dir)
    assert res.problems == (), [(p.code, p.message) for p in res.problems]
    assert res.ok and res.complete and res.ratings_path is not None


def test_a_rating_below_the_last_row_is_still_an_error(built: Built, filled: Filled) -> None:
    from conftest import open_sheet

    wb, ws, meta, cmap = open_sheet(filled.paths["R1"])
    ws.cell(ws.max_row + 1, cmap["accuracy"]).value = 4
    wb.save(filled.paths["R1"])
    res = collect(built.out, filled.dir)
    assert codes(res) == ["unknown_rating_id"] and not res.ok


def test_folder_scan_skips_hidden_companion_files(built: Built, filled: Filled) -> None:
    # macOS writes an AppleDouble "._name" file beside every file on a non-Mac volume.
    for name in ("._R1.xlsx", "._R2.xlsx", ".hidden.xlsx"):
        (filled.dir / name).write_bytes(b"\x00\x05\x16\x07" + b"\x00" * 60)
    res = collect(built.out, filled.dir)
    assert res.problems == (), [(p.code, p.file) for p in res.problems]
    assert res.ok and res.complete and res.ratings_path is not None


def test_a_hidden_file_named_explicitly_is_still_read_and_reported(built: Built, filled: Filled) -> None:
    stray = filled.dir / "._R1.xlsx"
    stray.write_bytes(b"\x00\x05\x16\x07" + b"\x00" * 60)
    res = collect(built.out, [filled.dir, stray])
    assert [p.file for p in problems_with(res, "not_a_ratersheet")] == ["._R1.xlsx"]
    assert not res.ok


# ---- texts that contain _xHHHH_ -------------------------------------------------------------------

XSTRING_TEXT = "The column is called Cost_x0020_Center, and _x0041_x0042_ marks rows A and B."
XSTRING_CONTEXT = "Export note: _x000A_ stands for a line feed in that system."


def excel_decodes(stored: str) -> str:
    """What a spreadsheet program shows for a stored string (the format's rule, not the tool's code)."""
    import re

    return re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), stored)


def excel_stores(shown: str) -> str:
    """What a spreadsheet program stores for a shown string."""
    import re

    return re.sub(r"_(?=x[0-9A-Fa-f]{4}_)", "_x005F_", shown)


XSTRING_SCALES = [
    {"id": "accuracy", "label": "Acc_x0042_uracy", "type": "ordinal", "min": 1, "max": 5},
    {"id": "harm", "type": "nominal", "values": ["none", "mi_x0043_nor"]},
]


def built_with_xstrings(tmp_path: Path) -> Built:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3}, with_context=True)
    rows[0]["text"], rows[0]["context"] = XSTRING_TEXT, XSTRING_CONTEXT
    rows[3]["text"] = "_x0041_"
    return make_built(tmp_path, rows=rows, raters=["R1", "R2"], scales=XSTRING_SCALES, comments=True)


def fill_as_a_spreadsheet_program_stores(b: Built, dest: Path) -> Filled:
    """Fill the sheets, storing each picked nominal value the way the file format spells it."""

    def stored(scale: dict, rating_id: str) -> Any:
        value = default_value(scale, rating_id)
        return excel_stores(value) if isinstance(value, str) else value

    return fill_workbooks(b.out, dest, value_fn=stored)


def test_texts_with_escape_like_sequences_collect_untouched(tmp_path: Path) -> None:
    b = built_with_xstrings(tmp_path)
    fill = fill_as_a_spreadsheet_program_stores(b, tmp_path / "ret")
    res = collect(b.out, fill.dir)
    assert res.problems == (), [(p.code, p.message) for p in res.problems]
    assert res.ok and res.complete


def test_text_hash_still_matches_after_a_spreadsheet_program_saved_the_sheet(tmp_path: Path) -> None:
    from conftest import data_row_numbers, open_sheet

    b = built_with_xstrings(tmp_path)
    fill = fill_as_a_spreadsheet_program_stores(b, tmp_path / "ret")
    items = {i["item_id"]: i for i in b.key["items"]}
    item_of = {a["rating_id"]: items[a["item_id"]] for a in b.key["assignments"]}
    study_items = {i.item_id: i for i in b.study.items}
    seen_special = 0
    for path in fill.paths.values():
        wb, ws, meta, cmap = open_sheet(path)
        for r in data_row_numbers(ws, cmap):
            original = study_items[item_of[ws.cell(r, cmap["rating_id"]).value]["item_id"]]
            for key, expected in (("text", original.text), ("context", original.context)):
                cell = ws.cell(r, cmap[key])
                shown = excel_decodes(cell.value)  # the program decodes on opening ...
                assert shown == expected, "the rater must see the text of the study"
                seen_special += "_x0041_" in shown
                cell.value = excel_stores(shown)  # ... and stores a plain string again on saving
        wb.save(path)
    assert seen_special >= 2, "setup: the special texts should be on the sheets"
    res = collect(b.out, fill.dir)
    assert res.problems == (), [(p.code, p.message) for p in res.problems]
    assert res.ok and res.complete
    values = {r["value"] for r in read_csv_dicts(res.ratings_path) if r["scale"] == "harm"}
    assert values <= {"none", "mi_x0043_nor"} and "mi_x0043_nor" in values


def test_escaped_carriage_return_in_a_comment_is_collected_as_a_line_break(built: Built, filled: Filled) -> None:
    rid = get_cell(filled.paths["R1"], 0, "rating_id")
    edit_cell(filled.paths["R1"], 0, "comment", "first line_x000D_\nsecond line")  # as Excel stores CR LF
    res = collect(built.out, filled.dir)
    assert res.ok
    comment = {r["rating_id"]: r["comment"] for r in read_csv_dicts(res.comments_path)}[rid]
    assert comment == "first line\nsecond line"
    assert b"\r" not in res.comments_path.read_bytes() and b"_x000D_" not in res.comments_path.read_bytes()


@pytest.mark.parametrize("hostile", ["_xD800_", "_xDFFF_", "_x0000_"])
def test_hostile_escapes_cannot_break_collection(built: Built, filled: Filled, hostile: str) -> None:
    edit_cell(filled.paths["R1"], 0, "comment", f"note {hostile}")
    edit_cell(filled.paths["R1"], 1, "text", f"text {hostile}")
    res = collect(built.out, filled.dir)  # must report, not raise UnicodeEncodeError
    assert codes(res) == ["text_changed"] and not res.ok


# ---- nominal spellings a rater cannot see or did not choose ---------------------------------------

KNOW = {"id": "know", "label": "Know", "type": "nominal", "values": ["yes", "no", "don't know", "café", "not sure"]}


@pytest.mark.parametrize(
    "raw,stored",
    [
        ("don’t know", "don't know"),  # typographic apostrophe
        ("DON‘T  KNOW", "don't know"),
        ("café", "café"),  # decomposed accent
        ("not sure", "not sure"),  # non-breaking space
        ("not   sure", "not sure"),
        (" Not\tSure ", "not sure"),
    ],
)
def test_nominal_spelling_variants_are_accepted_in_the_scale_spelling(tmp_path: Path, raw: str, stored: str) -> None:
    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, scales=[KNOW]))
    fill = fill_workbooks(b.out, tmp_path / "ret")
    rid = get_cell(fill.paths["R1"], 0, "rating_id")
    edit_cell(fill.paths["R1"], 0, "know", raw)
    res = collect(b.out, fill.dir)
    assert res.ok and res.complete, [(p.code, p.message) for p in res.problems]
    assert stored_value(res, b, rid, "know") == stored


def test_loose_nominal_matching_never_chooses_between_two_values(tmp_path: Path) -> None:
    both = dict(KNOW, values=["yes", "don't know", "don’t know"])  # two values that differ in the apostrophe
    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, scales=[both]))
    fill = fill_workbooks(b.out, tmp_path / "ret", value_fn=lambda sc, rid: "yes")
    rids = [get_cell(fill.paths["R1"], i, "rating_id") for i in range(3)]
    edit_cell(fill.paths["R1"], 0, "know", "Don't know")
    edit_cell(fill.paths["R1"], 1, "know", "Don’t know")
    assert collect(b.out, fill.dir).ok
    res = collect(b.out, fill.dir)
    assert stored_value(res, b, rids[0], "know") == "don't know"
    assert stored_value(res, b, rids[1], "know") == "don’t know"
    edit_cell(fill.paths["R1"], 2, "know", "donʼt know")  # a third apostrophe: fits both values
    res = collect(b.out, fill.dir)
    ps = problems_with(res, "invalid_value")
    assert [p.rating_id for p in ps] == [rids[2]] and not res.ok


# ---- nominal values a spreadsheet program would re-type --------------------------------------------

RETYPED = [
    {"id": "halluc", "label": "Hallucination", "type": "nominal", "values": ["TRUE", "FALSE"]},
    {"id": "credit", "label": "Credit", "type": "nominal", "values": ["0", "0.5", "1"]},
    {"id": "errors", "label": "Number of errors", "type": "nominal", "values": ["0", "1-2", "3-5", "6+"]},
    {"id": "share", "label": "Share", "type": "nominal", "values": ["0%", "50%", "100%"]},
    {"id": "accuracy", "label": "Accuracy", "type": "ordinal", "min": 1, "max": 5},
]


def test_built_sheets_keep_nominal_picks_as_text(tmp_path: Path) -> None:
    from conftest import data_row_numbers, open_sheet

    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, scales=RETYPED))
    for rater in b.manifest["raters"]:
        wb, ws, meta, cmap = open_sheet(b.out / b.manifest["sheets"][rater]["file"])
        for r in data_row_numbers(ws, cmap):
            for sc in RETYPED:
                expected = "@" if sc["type"] == "nominal" else "General"
                assert ws.cell(r, cmap[sc["id"]]).number_format == expected, (rater, r, sc["id"])
    fill = fill_workbooks(b.out, tmp_path / "ret")  # a text cell hands the pick back as text
    res = collect(b.out, fill.dir)
    assert res.problems == () and res.ok and res.complete


# ---- a run that fails part-way ---------------------------------------------------------------------


def test_failed_collect_leaves_no_report_beside_the_previous_dataset(
    built: Built, filled: Filled, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    partial = fill_workbooks(built.out, tmp_path / "partial", skip_raters=("R3",))
    assert collect(built.out, partial.dir).complete is False
    earlier_dataset = (built.out / "ratings_blinded.csv").read_bytes()

    def disk_full(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    # ratersheet.collect is the function; the module is reached through sys.modules.
    monkeypatch.setattr(sys.modules["ratersheet.collect"], "write_csv", disk_full)
    with pytest.raises(OSError):
        collect(built.out, filled.dir)  # every sheet is back, so this run would report "complete"
    monkeypatch.undo()

    assert (built.out / "ratings_blinded.csv").read_bytes() == earlier_dataset, "setup: the old dataset is still there"
    assert not (built.out / "collect_report.json").exists(), "a report must never describe another dataset"
    with pytest.raises(RatersheetError, match="collect"):
        unblind(built.out)
    assert not (built.out / "ratings.csv").exists() and not (built.out / "unblind_record.json").exists()


# ---- the report identifies its run and the files it wrote ------------------------------------------


def test_report_has_a_run_id_that_is_new_on_every_run(built: Built, filled: Filled) -> None:
    import re

    seen = set()
    for _ in range(3):
        collect(built.out, filled.dir, created="2026-11-27T10:00:00Z")  # the same clock second
        run_id = report_of(built)["run_id"]
        assert re.fullmatch(r"[0-9a-f]{16}", run_id), run_id
        seen.add(run_id)
    assert len(seen) == 3, "two runs in the same second must still be told apart"


def test_report_holds_the_hashes_of_the_blinded_files_it_wrote(built: Built, filled: Filled) -> None:
    res = collect(built.out, filled.dir)
    rep = report_of(built)
    assert rep["ratings_blinded_sha256"] == file_sha256(res.ratings_path)
    assert rep["comments_blinded_sha256"] == file_sha256(res.comments_path)


def test_report_hash_is_null_for_a_file_that_was_not_written(built: Built, filled: Filled, tmp_path: Path) -> None:
    b = make_built(tmp_path, name="nocomments", out="out-nocomments", **dict(DEFAULT_BUILD_KWARGS, comments=False))
    fill = fill_workbooks(b.out, tmp_path / "ret-nocomments")
    res = collect(b.out, fill.dir)
    rep = report_of(b)
    assert rep["ratings_blinded_sha256"] == file_sha256(res.ratings_path) and rep["comments_blinded_sha256"] is None

    edit_cell(filled.paths["R1"], 0, "text", "tampered text")
    assert not collect(built.out, filled.dir).ok
    rep = report_of(built)
    assert rep["ratings_blinded_sha256"] is None and rep["comments_blinded_sha256"] is None
    assert len(rep["run_id"]) == 16, "a failing run is a run too"


# ---- the result says what was removed and how many files were looked at ----------------------------


def test_result_names_the_stale_files_a_run_removed(built: Built, filled: Filled) -> None:
    first = collect(built.out, filled.dir)
    assert first.removed == (), "nothing was there to remove; overwriting the blinded files is not removing"
    again = collect(built.out, filled.dir)
    assert again.removed == (), "the blinded files were written again, not removed"
    unblind(built.out)
    after_unblinding = collect(built.out, filled.dir)
    assert after_unblinding.removed == ("ratings.csv", "comments.csv")
    assert not (built.out / "ratings.csv").exists() and not (built.out / "comments.csv").exists()

    unblind(built.out)
    edit_cell(filled.paths["R1"], 0, "text", "tampered text")
    failing = collect(built.out, filled.dir)
    assert not failing.ok
    assert failing.removed == ("ratings.csv", "comments.csv", "ratings_blinded.csv", "comments_blinded.csv")
    assert collect(built.out, filled.dir).removed == (), "a second failing run finds nothing left to remove"
    assert (built.out / "unblind_record.json").exists(), "the unblinding record is never removed"


def test_removed_lists_only_files_that_existed(tmp_path: Path) -> None:
    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, comments=False))
    fill = fill_workbooks(b.out, tmp_path / "ret")
    assert collect(b.out, fill.dir).ok
    unblind(b.out)
    assert collect(b.out, fill.dir).removed == ("ratings.csv",)


def test_result_counts_the_returned_files_that_were_examined(built: Built, filled: Filled, tmp_path: Path) -> None:
    assert collect(built.out, filled.dir).n_files_read == 3
    assert collect(built.out, filled.paths["R1"]).n_files_read == 1
    assert collect(built.out, [filled.dir, filled.paths["R1"]]).n_files_read == 3, "a file given twice is read once"
    (filled.dir / "garbage.xlsx").write_bytes(b"this is not a zip file")
    (filled.dir / "notes.txt").write_text("not a workbook", encoding="utf-8")
    assert collect(built.out, filled.dir).n_files_read == 4, "an unreadable .xlsx was examined; notes.txt was not"
    empty = tmp_path / "empty"
    empty.mkdir()
    res = collect(built.out, empty)
    assert res.n_files_read == 0 and res.ok, "the caller needs this to say that nothing was found to check"


# ---- messages about refused files -------------------------------------------------------------------


def test_a_refused_sheet_is_not_also_reported_as_not_provided(built: Built, filled: Filled) -> None:
    set_meta_columns(filled.paths["R3"], lambda columns: columns.pop())
    for require_complete in (False, True):
        res = collect(built.out, filled.dir, require_complete=require_complete)
        assert [(p.code, p.rater_id) for p in res.problems] == [("not_a_ratersheet", "R3")], (
            "R3 did send a workbook; one bad file is one error"
        )
        assert res.complete is False and res.ok is False
        assert report_of(built)["per_rater"]["R3"]["received"] == 0


def test_a_rater_who_sent_nothing_is_still_reported_beside_a_refused_sheet(built: Built, filled: Filled) -> None:
    set_meta_columns(filled.paths["R3"], lambda columns: columns.pop())
    filled.paths["R2"].unlink()
    res = collect(built.out, filled.dir)
    assert [p.rater_id for p in problems_with(res, "missing_workbook")] == ["R2"]


def test_a_good_file_is_used_although_another_file_of_the_rater_was_refused(built: Built, filled: Filled) -> None:
    altered = filled.dir / "R3_altered.xlsx"
    shutil.copy(filled.paths["R3"], altered)
    set_meta_columns(altered, lambda columns: columns.pop())
    res = collect(built.out, filled.dir)
    assert [(p.code, p.file) for p in res.problems] == [("not_a_ratersheet", "R3_altered.xlsx")]
    assert res.n_received == res.n_expected, "R3's intact file was read"


def test_wrong_study_message_shows_the_other_commitment_in_full(built: Built, filled: Filled, tmp_path: Path) -> None:
    other = make_built(tmp_path, name="proj2", out="out2", nonce="9" * 64, **dict(DEFAULT_BUILD_KWARGS, seed=SEED + 1))
    shutil.copy(other.out / other.manifest["sheets"]["R1"]["file"], filled.paths["R1"])
    message = problems_with(collect(built.out, filled.dir), "wrong_study")[0].message
    theirs = other.manifest["key_sha256"]
    assert len(theirs) == 64 and theirs != built.manifest["key_sha256"]
    assert message.endswith(f"(its key commitment is {theirs})"), message


def test_wrong_study_message_still_shortens_a_commitment_that_is_not_a_hash(built: Built, filled: Filled) -> None:
    edit_meta(filled.paths["R2"], key_sha256="=HYPERLINK(" + "z" * 500)
    message = problems_with(collect(built.out, filled.dir), "wrong_study")[0].message
    assert len(message) < 250 and "z" * 60 not in message and message.endswith("...)"), message
