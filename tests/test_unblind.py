"""verify_key, verify_sheet, unblind."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import ratersheet
from ratersheet import (
    IntegrityError,
    RatersheetError,
    UnblindResult,
    collect,
    file_sha256,
    unblind,
    verify_key,
    verify_sheet,
)
from conftest import (
    DEFAULT_BUILD_KWARGS,
    SEED,
    Built,
    Filled,
    edit_cell,
    fill_workbooks,
    make_built,
    read_csv,
    read_csv_dicts,
)

RATINGS_HEADER = ["item_id", "case_id", "source", "rater_id", "scale", "value", "item_code", "rating_id", "position"]
COMMENTS_HEADER = ["item_id", "case_id", "source", "rater_id", "rating_id", "comment"]
WHEN = "2026-11-27T09:30:00Z"


@pytest.fixture
def collected(built: Built, filled: Filled) -> Built:
    assert collect(built.out, filled.dir).ok
    return built


# ---- verify_key / verify_sheet ---------------------------------------------------------------


def test_verify_key_returns_the_commitment(built: Built) -> None:
    assert verify_key(built.out) == built.manifest["key_sha256"] == file_sha256(built.out / "private" / "key.json")
    assert verify_key(str(built.out)) == built.manifest["key_sha256"]


def test_verify_key_detects_any_edit(built: Built) -> None:
    key = built.out / "private" / "key.json"
    key.write_bytes(key.read_bytes() + b"\n")
    with pytest.raises(IntegrityError):
        verify_key(built.out)


def test_verify_key_detects_a_changed_source_label(built: Built) -> None:
    key = built.out / "private" / "key.json"
    key.write_text(key.read_text(encoding="utf-8").replace("srcalpha", "srcbeta", 1), encoding="utf-8")
    with pytest.raises(IntegrityError):
        verify_key(built.out)


def test_verify_sheet_true_for_this_study(built: Built, filled: Filled) -> None:
    for r, p in built.result.sheets.items():
        assert verify_sheet(built.out, p) is True, f"built sheet {r}"
    for r, p in filled.paths.items():
        assert verify_sheet(built.out, p) is True, f"filled sheet {r}"


def test_verify_sheet_false_for_another_study(built: Built, tmp_path: Path) -> None:
    other = make_built(tmp_path, name="proj2", out="out2", nonce="7" * 64, **dict(DEFAULT_BUILD_KWARGS, seed=SEED + 5))
    assert verify_sheet(built.out, other.result.sheets["R1"]) is False


def test_verify_sheet_false_when_commitment_is_altered(built: Built, filled: Filled) -> None:
    from conftest import edit_meta

    edit_meta(filled.paths["R1"], key_sha256="f" * 64)
    assert verify_sheet(built.out, filled.paths["R1"]) is False


# ---- refusals ---------------------------------------------------------------------------------


def test_refuses_without_a_report(built: Built) -> None:
    with pytest.raises(RatersheetError):
        unblind(built.out)
    assert not (built.out / "ratings.csv").exists()


def test_refuses_without_blinded_csv(collected: Built) -> None:
    (collected.out / "ratings_blinded.csv").unlink()
    with pytest.raises(RatersheetError):
        unblind(collected.out)


def test_refuses_when_report_is_not_ok(built: Built, filled: Filled) -> None:
    edit_cell(filled.paths["R1"], 0, "text", "edited text")
    assert not collect(built.out, filled.dir).ok
    with pytest.raises(RatersheetError):
        unblind(built.out)
    assert not (built.out / "ratings.csv").exists() and not (built.out / "unblind_record.json").exists()


def test_refuses_incomplete_without_the_flag(built: Built, tmp_path: Path) -> None:
    fill = fill_workbooks(built.out, tmp_path / "partial", skip_raters=("R3",))
    res = collect(built.out, fill.dir)
    assert res.ok and not res.complete
    with pytest.raises(RatersheetError):
        unblind(built.out)
    assert not (built.out / "ratings.csv").exists()


def test_incomplete_with_the_flag_works_and_is_recorded(built: Built, tmp_path: Path) -> None:
    fill = fill_workbooks(built.out, tmp_path / "partial", skip_raters=("R3",))
    cres = collect(built.out, fill.dir)
    res = unblind(built.out, allow_incomplete=True, unblinded_at=WHEN)
    assert res.complete is False
    rec = json.loads((built.out / "unblind_record.json").read_text(encoding="utf-8"))
    assert rec["complete"] is False
    assert rec["n_expected"] == cres.n_expected and rec["n_received"] == cres.n_received
    assert len(read_csv(res.ratings_path)[1]) == 28 * 2


def test_refuses_when_key_edited_after_build(collected: Built) -> None:
    key = collected.out / "private" / "key.json"
    key.write_bytes(key.read_bytes() + b" ")
    with pytest.raises(IntegrityError):
        unblind(collected.out)
    assert not (collected.out / "ratings.csv").exists()


def test_refuses_report_from_another_key(collected: Built) -> None:
    rp = collected.out / "collect_report.json"
    rep = json.loads(rp.read_text(encoding="utf-8"))
    rep["key_sha256"] = "0" * 64
    rp.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(IntegrityError):
        unblind(collected.out)
    assert not (collected.out / "ratings.csv").exists()


# ---- outputs ------------------------------------------------------------------------------------


def test_unblind_outputs(collected: Built, filled: Filled) -> None:
    res = unblind(collected.out, unblinded_at=WHEN)
    assert isinstance(res, UnblindResult)
    assert Path(res.ratings_path) == collected.out / "ratings.csv"
    assert Path(res.comments_path) == collected.out / "comments.csv"
    assert Path(res.record_path) == collected.out / "unblind_record.json"
    assert res.complete is True
    header, rows = read_csv(res.ratings_path)
    assert header == RATINGS_HEADER
    assert len(rows) == 56
    assert read_csv(res.comments_path)[0] == COMMENTS_HEADER


def test_rows_match_blinded_file_and_original_items(collected: Built, filled: Filled) -> None:
    unblind(collected.out)
    blinded = read_csv_dicts(collected.out / "ratings_blinded.csv")
    full = read_csv_dicts(collected.out / "ratings.csv")
    assert len(full) == len(blinded)
    by_id = {i.item_id: i for i in collected.study.items}
    by_code = {i["item_code"]: i["item_id"] for i in collected.key["items"]}
    for b, f in zip(blinded, full):
        for col in ("item_code", "rater_id", "scale", "value", "rating_id", "position"):
            assert f[col] == b[col], f"column {col} differs from the blinded row"
        item = by_id[f["item_id"]]
        assert by_code[f["item_code"]] == f["item_id"]
        assert f["source"] == item.source, f"row for {f['item_id']}: source must match the original items file"
        assert f["case_id"] == (item.case_id or "")
    # and the values really are what the raters entered
    for f in full:
        assert f["value"] == str(filled.values[f["rating_id"]][f["scale"]])


def test_every_item_appears_the_right_number_of_times(collected: Built) -> None:
    unblind(collected.out)
    full = read_csv_dicts(collected.out / "ratings.csv")
    per_item: dict[str, set[str]] = {}
    for r in full:
        per_item.setdefault(r["item_id"], set()).add(r["rater_id"])
    assert set(per_item) == {i.item_id for i in collected.study.items}
    assert all(len(v) == 2 for v in per_item.values())


def test_comments_csv_is_unblinded(collected: Built, filled: Filled) -> None:
    unblind(collected.out)
    blinded = read_csv_dicts(collected.out / "comments_blinded.csv")
    full = read_csv_dicts(collected.out / "comments.csv")
    assert len(full) == len(blinded) == len(filled.comments) > 0
    by_id = {i.item_id: i for i in collected.study.items}
    by_code = {i["item_code"]: i["item_id"] for i in collected.key["items"]}
    for b, f in zip(blinded, full):
        assert f["rating_id"] == b["rating_id"] and f["rater_id"] == b["rater_id"] and f["comment"] == b["comment"]
        assert by_code[b["item_code"]] == f["item_id"]
        assert f["source"] == by_id[f["item_id"]].source
        assert f["comment"] == filled.comments[f["rating_id"]]


def test_unblind_record(collected: Built) -> None:
    res = unblind(collected.out, unblinded_at=WHEN)
    rec = json.loads(res.record_path.read_text(encoding="utf-8"))
    run_id = json.loads((collected.out / "collect_report.json").read_text(encoding="utf-8"))["run_id"]
    assert re.fullmatch(r"[0-9a-f]{16}", run_id)
    this_run = {
        "unblinded_at": WHEN, "complete": True, "n_expected": 56, "n_received": 56, "collect_run_id": run_id,
    }
    assert rec == {
        **this_run,
        "tool_version": ratersheet.__version__,
        "key_sha256": collected.manifest["key_sha256"],
        "first": this_run,
        "times_unblinded": 1,
    }


def test_unblinded_at_defaults_to_a_utc_timestamp(collected: Built) -> None:
    res = unblind(collected.out)
    rec = json.loads(res.record_path.read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", rec["unblinded_at"])


def test_no_comments_csv_when_there_is_no_blinded_comments_file(tmp_path: Path) -> None:
    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, comments=False))
    fill = fill_workbooks(b.out, tmp_path / "ret")
    assert collect(b.out, fill.dir).ok
    res = unblind(b.out)
    assert res.comments_path is None and not (b.out / "comments.csv").exists()


def test_unblind_files_are_utf8_lf(collected: Built) -> None:
    res = unblind(collected.out)
    for p in (res.ratings_path, res.comments_path, res.record_path):
        raw = Path(p).read_bytes()
        raw.decode("utf-8")
        assert b"\r" not in raw, Path(p).name


def test_unblind_works_with_str_path(collected: Built) -> None:
    assert unblind(str(collected.out)).ratings_path.exists()


# ---- what the refusal says is missing -----------------------------------------------------------


def test_refusal_names_the_ratings_and_the_raters_still_missing(built: Built, tmp_path: Path) -> None:
    fill = fill_workbooks(built.out, tmp_path / "partial", skip_raters=("R3",))
    cres = collect(built.out, fill.dir)
    with pytest.raises(RatersheetError) as ei:
        unblind(built.out)
    msg = str(ei.value)
    assert "collection is incomplete" in msg and "--allow-incomplete" in msg
    assert f"{cres.n_received} of {cres.n_expected} expected ratings" in msg
    assert "no sheet has been returned by R3" in msg


def test_refusal_for_an_empty_cell_does_not_blame_a_rater(built: Built, filled: Filled) -> None:
    edit_cell(filled.paths["R1"], 0, "accuracy", None)
    collect(built.out, filled.dir)
    with pytest.raises(RatersheetError) as ei:
        unblind(built.out)
    assert "55 of 56 expected ratings were received" in str(ei.value)
    assert "no sheet" not in str(ei.value)


def test_no_required_scale_still_refuses_until_every_sheet_is_back(tmp_path: Path) -> None:
    optional = [
        {"id": "accuracy", "type": "ordinal", "min": 1, "max": 5, "required": False},
        {"id": "harm", "type": "nominal", "values": ["none", "minor", "serious"], "required": False},
    ]
    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, scales=optional))
    fill = fill_workbooks(b.out, tmp_path / "partial", skip_raters=("R2", "R3"))
    cres = collect(b.out, fill.dir)
    assert cres.ok and cres.n_expected == 0, "setup: nothing is required, so no rating is expected"
    with pytest.raises(RatersheetError) as ei:
        unblind(b.out)
    msg = str(ei.value)
    assert "collection is incomplete" in msg and "no sheet has been returned by R2, R3" in msg
    assert "0 of 0" not in msg, "a count of nothing says nothing"
    assert not (b.out / "ratings.csv").exists() and not (b.out / "unblind_record.json").exists()

    res = unblind(b.out, allow_incomplete=True, unblinded_at=WHEN)
    assert res.complete is False
    assert json.loads((b.out / "unblind_record.json").read_text(encoding="utf-8"))["complete"] is False

    rest = fill_workbooks(b.out, tmp_path / "all")
    assert collect(b.out, rest.dir).complete is True
    assert unblind(b.out, unblinded_at=WHEN).complete is True


# ---- the record comes before any unblinded file -------------------------------------------------


@pytest.mark.parametrize("damaged", ["", "[]", "{not json"])
def test_damaged_record_stops_unblinding_before_any_source_is_written(collected: Built, damaged: str) -> None:
    record = collected.out / "unblind_record.json"
    record.write_text(damaged, encoding="utf-8")
    with pytest.raises(RatersheetError, match="unblind_record.json"):
        unblind(collected.out)
    assert not (collected.out / "ratings.csv").exists(), "sources on disk with no record of the unblinding"
    assert not (collected.out / "comments.csv").exists()
    assert record.read_text(encoding="utf-8") == damaged


def test_no_unblinded_file_exists_unless_the_record_was_written(
    collected: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    # ratersheet.unblind is the function; the module is reached through sys.modules.
    monkeypatch.setattr(sys.modules["ratersheet.unblind"], "write_json", disk_full)
    with pytest.raises(OSError):
        unblind(collected.out)
    assert not (collected.out / "ratings.csv").exists()
    assert not (collected.out / "comments.csv").exists()


def test_record_exists_by_the_time_the_unblinded_ratings_are_written(
    collected: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    module = sys.modules["ratersheet.unblind"]
    real_write_csv = module.write_csv
    record_was_there: list[bool] = []

    def watching(path: Path, *args: object, **kwargs: object) -> None:
        record_was_there.append((collected.out / "unblind_record.json").exists())
        real_write_csv(path, *args, **kwargs)

    monkeypatch.setattr(module, "write_csv", watching)
    unblind(collected.out, unblinded_at=WHEN)
    assert record_was_there == [True, True], "ratings.csv and comments.csv are written after the record"


# ---- the blinded files must be the ones the collection check wrote -----------------------------------


def assert_nothing_was_unblinded(b: Built) -> None:
    for name in ("ratings.csv", "comments.csv", "unblind_record.json"):
        assert not (b.out / name).exists(), f"{name} was written from a file that collect never checked"


def test_refuses_a_blinded_ratings_file_that_was_cut_and_edited_after_collect(collected: Built) -> None:
    path = collected.out / "ratings_blinded.csv"
    header, rows = read_csv(path)
    kept = [",".join(row[:3] + ["5"] + row[4:]) for row in rows[:8]]  # 8 of 56 rows, values changed
    path.write_text("\n".join([",".join(header)] + kept) + "\n", encoding="utf-8")
    with pytest.raises(IntegrityError, match=r"ratings_blinded\.csv is not the file the last collection check wrote"):
        unblind(collected.out)
    assert_nothing_was_unblinded(collected)


def test_refuses_a_blinded_ratings_file_with_one_changed_value(collected: Built) -> None:
    path = collected.out / "ratings_blinded.csv"
    before = path.read_text(encoding="utf-8")
    line = next(ln for ln in before.split("\n") if ",accuracy,1," in ln)
    path.write_text(before.replace(line, line.replace(",accuracy,1,", ",accuracy,5,"), 1), encoding="utf-8")
    with pytest.raises(IntegrityError, match="Run 'ratersheet collect' again"):
        unblind(collected.out, allow_incomplete=True)
    assert_nothing_was_unblinded(collected)


def test_refuses_a_blinded_comments_file_that_was_edited_or_removed_after_collect(collected: Built) -> None:
    path = collected.out / "comments_blinded.csv"
    original = path.read_bytes()
    path.write_bytes(original.replace(b"Synthetic comment", b"Rewritten comment"))
    with pytest.raises(IntegrityError, match=r"comments_blinded\.csv is not the file"):
        unblind(collected.out)
    path.unlink()
    with pytest.raises(IntegrityError, match=r"comments_blinded\.csv is missing"):
        unblind(collected.out)
    assert_nothing_was_unblinded(collected)
    path.write_bytes(original)
    assert unblind(collected.out).comments_path is not None, "the file collect wrote is accepted"


def test_running_collect_again_makes_the_blinded_files_acceptable_again(collected: Built, filled: Filled) -> None:
    path = collected.out / "ratings_blinded.csv"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(IntegrityError):
        unblind(collected.out)
    assert collect(collected.out, filled.dir).ok
    assert unblind(collected.out).complete is True


def test_report_without_hashes_or_run_id_is_still_accepted(collected: Built) -> None:
    # A report from before these entries existed: nothing to compare, so nothing is refused.
    rp = collected.out / "collect_report.json"
    rep = json.loads(rp.read_text(encoding="utf-8"))
    for entry in ("run_id", "ratings_blinded_sha256", "comments_blinded_sha256"):
        del rep[entry]
    rp.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    res = unblind(collected.out, unblinded_at=WHEN)
    rec = json.loads(res.record_path.read_text(encoding="utf-8"))
    assert rec["collect_run_id"] is None and rec["first"]["collect_run_id"] is None
