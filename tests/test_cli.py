"""The command line, driven through ratersheet.cli.main(argv)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import ratersheet
from ratersheet import build, check_leaks, collect, design_summary, load_study, make_design, read_workbook
from ratersheet.leaks import coverage_notes, leak_coverage
from ratersheet.cli import main
from conftest import (
    CREATED,
    DEFAULT_BUILD_KWARGS,
    NONCE,
    REPO_URL,
    SRC_A,
    SRC_B,
    Built,
    Filled,
    edit_cell,
    edit_meta,
    fill_workbooks,
    make_built,
    make_item_rows,
    read_csv_dicts,
    write_study,
)


def run(capsys: pytest.CaptureFixture[str], *argv: Any) -> tuple[int, str, str]:
    try:
        code = main([str(a) for a in argv])
    except SystemExit as e:  # argparse exits for --version and for bad usage
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    cap = capsys.readouterr()
    return (0 if code is None else code), cap.out, cap.err


def assert_clean_error(out: str, err: str) -> None:
    assert "error:" in err, f"stderr should carry 'error: <message>', got {err!r}"
    assert "Traceback" not in out + err, "no traceback may reach the user"


def leaky_rows() -> list[dict[str, str]]:
    rows = make_item_rows({SRC_A: 5, SRC_B: 5})
    rows[0]["text"] = "As an AI language model I would say " + rows[0]["text"]
    return rows


def tree(path: Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))


# ---- version and usage -----------------------------------------------------------------------


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "--version")
    assert code == 0
    assert "ratersheet 0.1.0" in out + err
    assert ratersheet.__version__ == "0.1.0"


def test_unknown_command_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "frobnicate")
    assert code == 2 and "Traceback" not in out + err


def test_missing_argument_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    for cmd in ("check", "build", "collect", "verify", "unblind", "methods"):
        code, out, err = run(capsys, cmd)
        assert code == 2, cmd
        assert "Traceback" not in out + err


def test_python_dash_m_works() -> None:
    proc = subprocess.run([sys.executable, "-m", "ratersheet", "--version"], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "ratersheet 0.1.0" in proc.stdout + proc.stderr


# ---- init ---------------------------------------------------------------------------------------


def test_init_writes_loadable_leak_free_example(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = tmp_path / "proj"
    d.mkdir()
    code, out, err = run(capsys, "init", d)
    assert code == 0, err
    assert (d / "study.toml").is_file() and (d / "items.csv").is_file()
    study = load_study(d / "study.toml")
    assert check_leaks(study) == [], "the example must build without leak warnings"
    res = build(study, tmp_path / "out", nonce=NONCE, created=CREATED)
    assert res.warnings == []
    assert (d / "study.toml").read_text(encoding="utf-8").lstrip().startswith("#"), "commented example"


def test_init_defaults_to_current_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    code, _, err = run(capsys, "init")
    assert code == 0, err
    assert (tmp_path / "study.toml").is_file() and (tmp_path / "items.csv").is_file()


def test_init_refuses_to_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "init", tmp_path)[0] == 0
    before = {n: (tmp_path / n).read_bytes() for n in ("study.toml", "items.csv")}
    (tmp_path / "study.toml").write_text("# my edits\n" + before["study.toml"].decode(), encoding="utf-8")
    edited = (tmp_path / "study.toml").read_bytes()
    code, out, err = run(capsys, "init", tmp_path)
    assert code == 2
    assert_clean_error(out, err)
    assert (tmp_path / "study.toml").read_bytes() == edited
    assert (tmp_path / "items.csv").read_bytes() == before["items.csv"]


def test_init_refuses_when_only_items_exists(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "items.csv").write_text("precious,data\n", encoding="utf-8")
    code, out, err = run(capsys, "init", tmp_path)
    assert code == 2
    assert_clean_error(out, err)
    assert (tmp_path / "items.csv").read_text(encoding="utf-8") == "precious,data\n"


def test_init_example_goes_through_check_and_build(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "init", tmp_path)
    assert run(capsys, "check", tmp_path / "study.toml", "--strict")[0] == 0
    assert run(capsys, "build", tmp_path / "study.toml", "--strict")[0] == 0


def test_init_template_does_not_promise_that_the_seed_fixes_the_design(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "init", tmp_path)
    lines = (tmp_path / "study.toml").read_text(encoding="utf-8").splitlines()
    seed_line = next(n for n, line in enumerate(lines) if line.startswith("seed ="))
    comment = " ".join(line.lstrip("# ") for line in lines[seed_line - 3 : seed_line]).lower()
    assert "same seed" not in comment
    assert "random secret" in comment and "key" in comment and "cannot be used to predict" in comment


def test_two_builds_of_the_init_example_share_no_ids_and_none_follow_from_the_seed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Everyone who keeps the template seed used to get the same codes and rating ids.
    run(capsys, "init", tmp_path)
    study = load_study(tmp_path / "study.toml")
    keys = []
    for name in ("one", "two"):
        assert run(capsys, "build", tmp_path / "study.toml", "--out", tmp_path / name)[0] == 0
        keys.append(json.loads((tmp_path / name / "private" / "key.json").read_text(encoding="utf-8")))
    ids = [{a["rating_id"] for a in key["assignments"]} for key in keys]
    item_codes = [{i["item_code"] for i in key["items"]} for key in keys]
    assert not ids[0] & ids[1] and not item_codes[0] & item_codes[1]
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    seed_only = ratersheet.DeterministicRNG(study.seed, "ids")
    seed_only_codes = {"I-" + seed_only.code(alphabet, 6) for _ in study.items}
    seed_only_ids = {seed_only.code(alphabet, 4) + "-" + seed_only.code(alphabet, 4) for _ in keys[0]["assignments"]}
    for n in (0, 1):
        assert not ids[n] & seed_only_ids and not item_codes[n] & seed_only_codes


# ---- check --------------------------------------------------------------------------------------


@pytest.fixture
def study_path(tmp_path: Path) -> Path:
    return write_study(tmp_path / "proj", **DEFAULT_BUILD_KWARGS)


def test_check_prints_summary_and_writes_nothing(study_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = tree(study_path.parent)
    code, out, err = run(capsys, "check", study_path)
    assert code == 0, err
    for needle in ("R1", "R2", "R3", "14"):
        assert needle in out, f"design summary should show {needle!r}"
    assert tree(study_path.parent) == before, "check must not write anything"
    assert "Traceback" not in out + err


def test_check_shows_only_what_is_certain_before_the_build(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 14 items (7 + 7), 3 raters, 2 per item: 28 seats give 9 or 10 items each, 14 seats per
    # source give 4 or 5. Who gets which is drawn at build time, so check cannot show it.
    p = write_study(tmp_path / "proj", **DEFAULT_BUILD_KWARGS)
    code, out, err = run(capsys, "check", p)
    assert code == 0, err
    assert "Items per rater: 9 or 10" in out
    assert f"  {SRC_A}: 4 or 5" in out and f"  {SRC_B}: 4 or 5" in out
    assert "R1, R2, R3" in out
    sentence = next(line for line in out.splitlines() if "drawn at build time" in line)
    assert "random secret" in sentence and "key" in sentence
    assert "Who rates what" in sentence and "order" in sentence and "codes" in sentence
    assert not re.search(r"R\d: \d", out), "no per-rater numbers: they are not known yet"
    assert "|" not in out, "no pair counts: they are not known yet"
    assert run(capsys, "check", p)[1] == out, "check prints the same thing every time"
    # a share that divides evenly is one number, not a range
    even = write_study(tmp_path / "even", rows=make_item_rows({SRC_A: 6, SRC_B: 6}), rpi=2)
    out_even = run(capsys, "check", even)[1]
    assert "Items per rater: 8\n" in out_even and f"  {SRC_A}: 4\n" in out_even


@pytest.mark.parametrize("n_raters,rpi,counts", [(3, 2, {SRC_A: 7, SRC_B: 7}), (4, 3, {SRC_A: 5, SRC_B: 9}), (5, 2, {SRC_A: 1, SRC_B: 6})])
def test_what_check_promises_is_what_build_delivers(n_raters: int, rpi: int, counts: dict[str, int], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raters = [f"R{i}" for i in range(1, n_raters + 1)]
    p = write_study(tmp_path / "proj", rows=make_item_rows(counts), raters=raters, rpi=rpi)
    out = run(capsys, "check", p)[1]

    def promised(line_start: str) -> set[int]:
        line = next(line for line in out.splitlines() if line.startswith(line_start))
        return {int(n) for n in re.findall(r"\d+", line.split(": ", 1)[1])}

    study = load_study(p)
    for trial in range(5):  # several builds: each draws another design
        res = build(study, tmp_path / f"out{trial}")
        summary = design_summary(study, res.design)
        assert set(summary["per_rater"].values()) <= promised("Items per rater:")
        for src in study.sources:
            got = {summary["per_rater_source"][r][src] for r in raters}
            assert got <= promised(f"  {src}:"), f"{src}: check promised {promised(f'  {src}:')}, build gave {got}"


def test_check_prints_leak_warnings_and_strict_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = write_study(tmp_path / "proj", rows=leaky_rows())
    warnings = check_leaks(load_study(p))
    assert warnings
    code, out, err = run(capsys, "check", p)
    assert code == 0, "warnings alone do not fail check"
    assert any(w.code in out or w.message in out for w in warnings), "leak warnings must be printed"
    code2, out2, err2 = run(capsys, "check", p, "--strict")
    assert code2 == 1
    assert "Traceback" not in out2 + err2


def test_check_strict_passes_on_clean_study(study_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert check_leaks(load_study(study_path)) == []
    assert run(capsys, "check", study_path, "--strict")[0] == 0


def short_label_rows() -> list[dict[str, str]]:
    """Clean texts, but one source label is too short to be searched for and one source is small."""
    return make_item_rows({"ab": 3, SRC_B: 7})


def test_check_and_build_say_what_the_leak_checks_could_not_look_at(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = write_study(tmp_path / "proj", rows=short_label_rows())
    study = load_study(p)
    notes = coverage_notes(leak_coverage(study))
    assert len(notes) == 2 and check_leaks(study) == []
    for argv in (("check", p), ("build", p, "--out", tmp_path / "out")):
        code, out, err = run(capsys, *argv)
        assert code == 0, err
        lines = out.splitlines()
        start = lines.index("Not checked:")
        assert lines[start - 1] == "Leak warnings: none", "the notes follow the warnings"
        assert lines[start + 1 : start + 3] == [f"  {note}" for note in notes]


def test_unchecked_parts_are_information_and_do_not_fail_strict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = write_study(tmp_path / "proj", rows=short_label_rows())
    code, out, _ = run(capsys, "check", p, "--strict")
    assert code == 0 and "Not checked:" in out
    code, out, _ = run(capsys, "build", p, "--out", tmp_path / "out", "--strict")
    assert code == 0 and "Not checked:" in out
    assert (tmp_path / "out" / "manifest.json").is_file()


def test_nothing_is_said_about_coverage_when_every_check_ran(study_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert coverage_notes(leak_coverage(load_study(study_path))) == []
    assert "Not checked" not in run(capsys, "check", study_path)[1]
    assert "Not checked" not in run(capsys, "build", study_path, "--out", tmp_path / "out")[1]


def test_check_bad_study_is_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = write_study(tmp_path / "proj", study_extra={"titel": "typo"})
    code, out, err = run(capsys, "check", p)
    assert code == 2
    assert_clean_error(out, err)
    assert "titel" in err


def test_check_missing_file_is_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "check", tmp_path / "nope.toml")
    assert code == 2
    assert_clean_error(out, err)


# ---- build --------------------------------------------------------------------------------------


def test_build_default_out_dir_is_next_to_the_study_file(study_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    code, out, err = run(capsys, "build", study_path)
    assert code == 0, err
    default = study_path.parent / "ratersheet-out"
    assert (default / "manifest.json").is_file()
    assert not (elsewhere / "ratersheet-out").exists()
    assert "private" in out.lower(), "output should say what to keep private"
    assert "sheets" in out.lower()
    assert "Traceback" not in out + err


def test_build_prints_the_design_that_was_built(study_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_dir = tmp_path / "custom"
    code, out, err = run(capsys, "build", study_path, "--out", out_dir)
    assert code == 0, err
    study = load_study(study_path)
    key = json.loads((out_dir / "private" / "key.json").read_text(encoding="utf-8"))
    summary = design_summary(study, make_design(study, secret=key["nonce"]))
    lines = out.splitlines()
    for rater, n in summary["per_rater"].items():
        by_source = summary["per_rater_source"][rater]
        assert f"  {rater}: {n} ({SRC_A}: {by_source[SRC_A]}, {SRC_B}: {by_source[SRC_B]})" in lines
        in_key = [a for a in key["assignments"] if a["rater_id"] == rater]
        assert len(in_key) == n, "the table is of the design in the key, not a preview"
    for pair, n in summary["pair_counts"].items():
        assert f"  {pair}: {n}" in lines
    assert "share no item" not in out


def test_build_says_when_two_raters_share_no_item(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 3 items, 2 raters each, 6 raters: 3 of the 15 pairs can share an item, so 12 cannot.
    p = write_study(tmp_path / "proj", rows=make_item_rows({SRC_A: 2, SRC_B: 1}), raters=[f"R{i}" for i in range(1, 7)], rpi=2)
    code, out, err = run(capsys, "build", p, "--out", tmp_path / "out")
    assert code == 0, err
    assert "12 pairs of raters share no item" in out and "agreement" in out
    # with one rater per item nobody is meant to share anything, so there is nothing to say
    single = write_study(tmp_path / "single", rows=make_item_rows({SRC_A: 3, SRC_B: 3}), rpi=1)
    assert "share no item" not in run(capsys, "build", single, "--out", tmp_path / "out1")[1]


def test_build_with_out_dir(study_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_dir = tmp_path / "custom"
    code, _, err = run(capsys, "build", study_path, "--out", out_dir)
    assert code == 0, err
    for r in ("R1", "R2", "R3"):
        assert (out_dir / "sheets" / f"{r}.xlsx").is_file()
    assert (out_dir / "private" / "key.json").is_file()


def test_build_refuses_to_overwrite_then_allows_with_flag(study_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_dir = tmp_path / "custom"
    assert run(capsys, "build", study_path, "--out", out_dir)[0] == 0
    key_before = (out_dir / "private" / "key.json").read_bytes()
    code, out, err = run(capsys, "build", study_path, "--out", out_dir)
    assert code == 2
    assert_clean_error(out, err)
    assert (out_dir / "private" / "key.json").read_bytes() == key_before
    code, out, err = run(capsys, "build", study_path, "--out", out_dir, "--overwrite")
    assert code == 0, err
    assert (out_dir / "private" / "key.json").read_bytes() != key_before


def test_build_does_not_replace_a_key_whose_manifest_is_missing(study_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_dir = tmp_path / "custom"
    assert run(capsys, "build", study_path, "--out", out_dir)[0] == 0
    (out_dir / "manifest.json").unlink()
    before = {p: p.read_bytes() for p in out_dir.rglob("*") if p.is_file()}
    code, out, err = run(capsys, "build", study_path, "--out", out_dir)
    assert code == 2
    assert_clean_error(out, err)
    assert str(out_dir / "private" / "key.json") in err and "--overwrite" in err
    assert {p: p.read_bytes() for p in out_dir.rglob("*") if p.is_file()} == before
    assert run(capsys, "build", study_path, "--out", out_dir, "--overwrite")[0] == 0


def test_build_does_not_delete_the_users_own_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # `build study.toml --out .` in a project folder that already has files with ordinary names.
    proj = tmp_path / "proj"
    p = write_study(proj, **DEFAULT_BUILD_KWARGS)
    mine = {"methods.md": "# my manuscript methods\n", "ratings.csv": "id,score\n", "sheets/budget-2026.xlsx": "x"}
    for name, text in mine.items():
        (proj / name).parent.mkdir(exist_ok=True)
        (proj / name).write_text(text, encoding="utf-8")
    before = tree(proj)
    code, out, err = run(capsys, "build", p, "--out", proj)
    assert code == 2
    assert_clean_error(out, err)
    assert tree(proj) == before, "a refused build writes and deletes nothing"
    for name, text in mine.items():
        assert (proj / name).read_text(encoding="utf-8") == text


def test_overwrite_help_says_what_is_deleted(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "build", "--help")
    assert code == 0
    text = " ".join(out.split())
    assert "--overwrite" in text and "delete" in text and "sheets/" in text and "key" in text


def test_build_strict_with_leaks_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = write_study(tmp_path / "proj", rows=leaky_rows())
    out_dir = tmp_path / "out"
    code, out, err = run(capsys, "build", p, "--out", out_dir, "--strict")
    assert code == 1
    assert "Traceback" not in out + err
    assert not (out_dir / "manifest.json").exists()
    assert not out_dir.exists() or list(out_dir.iterdir()) == [], "build --strict must write nothing"


def test_build_without_strict_builds_despite_leaks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = write_study(tmp_path / "proj", rows=leaky_rows())
    code, out, err = run(capsys, "build", p, "--out", tmp_path / "out")
    assert code == 0, err
    assert (tmp_path / "out" / "manifest.json").is_file()
    manifest_text = (tmp_path / "out" / "manifest.json").read_text(encoding="utf-8")
    report = json.loads((tmp_path / "out" / "private" / "leak_report.json").read_text(encoding="utf-8"))
    assert report["warnings"], "the warnings are on record in the private leak report"
    assert "leak_warnings" not in json.loads(manifest_text)
    for warning in report["warnings"]:
        assert warning["code"] in out, "the warnings are printed"
        assert warning["code"] not in manifest_text, "the shareable manifest does not name the cue"
    assert "leak_report.json" in out


def test_build_bad_study_is_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = write_study(tmp_path / "proj", raters=["R1", "r1"])
    code, out, err = run(capsys, "build", p, "--out", tmp_path / "out")
    assert code == 2
    assert_clean_error(out, err)
    assert not (tmp_path / "out" / "manifest.json").exists()


def test_build_says_that_a_partial_design_narrows_which_items_share_a_source(study_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 3 raters, 2 per item: who rated what, with the equal share per source, is a hint.
    code, out, err = run(capsys, "build", study_path, "--out", tmp_path / "partial")
    assert code == 0, err
    notes = [line for line in out.splitlines() if "not every rater rates every item" in line]
    assert len(notes) == 1, "one note, not one per source or rater"
    note = notes[0]
    assert note.startswith("Note:") and "equal share of every source" in note
    assert "who rated what" in note and "share a source" in note and "small study" in note
    assert "several sheets" in note and "blinded ratings" in note
    assert "fully crossed design" in note and "no such pattern" in note
    # every rater rates every item: there is no pattern, so there is nothing to say
    crossed = write_study(tmp_path / "crossed", rows=make_item_rows({SRC_A: 5, SRC_B: 5}))
    code, out, err = run(capsys, "build", crossed, "--out", tmp_path / "out-crossed")
    assert code == 0, err
    assert "not every rater rates every item" not in out
    # one source: no two items can be told to share it or not
    single = write_study(tmp_path / "single", rows=make_item_rows({SRC_A: 6}), rpi=2)
    code, out, err = run(capsys, "build", single, "--out", tmp_path / "out-single")
    assert code == 0, err
    assert "not every rater rates every item" not in out


def test_build_does_not_say_to_send_a_sheet_that_has_no_item(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 2 items, 1 rater each, 5 raters: three raters get nothing.
    raters = [f"R{i}" for i in range(1, 6)]
    p = write_study(tmp_path / "proj", rows=make_item_rows({SRC_A: 1, SRC_B: 1}), raters=raters, rpi=1)
    out_dir = tmp_path / "out"
    code, out, err = run(capsys, "build", p, "--out", out_dir)
    assert code == 0, err
    key = json.loads((out_dir / "private" / "key.json").read_text(encoding="utf-8"))
    busy = {a["rater_id"] for a in key["assignments"]}
    idle = [r for r in raters if r not in busy]
    assert len(busy) == 2 and len(idle) == 3
    lines = out.splitlines()
    start = lines.index("Send to the raters, one file each:")
    end = lines.index("Keep private; never send to raters:")
    to_send = [line for line in lines[start + 1 : end] if line.startswith("  ")]
    assert to_send == [f"  {r}: {out_dir / 'sheets' / (r + '.xlsx')}" for r in raters if r in busy]
    assert f"No items were assigned to {idle[0]}, {idle[1]} and {idle[2]}; their sheets are empty and need not be sent." in lines
    # one rater without items is spoken of in the singular
    one = write_study(tmp_path / "one", rows=make_item_rows({SRC_A: 1, SRC_B: 1}), raters=raters[:3], rpi=1)
    out = run(capsys, "build", one, "--out", tmp_path / "out-one")[1]
    key = json.loads((tmp_path / "out-one" / "private" / "key.json").read_text(encoding="utf-8"))
    (idle_one,) = set(raters[:3]) - {a["rater_id"] for a in key["assignments"]}
    assert f"No items were assigned to {idle_one}; that sheet is empty and need not be sent." in out.splitlines()


def test_build_says_nothing_about_empty_sheets_when_every_rater_has_items(study_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = run(capsys, "build", study_path, "--out", tmp_path / "out")[1]
    assert "No items were assigned" not in out and "need not be sent" not in out
    for r in ("R1", "R2", "R3"):
        assert f"  {r}: {tmp_path / 'out' / 'sheets' / (r + '.xlsx')}" in out.splitlines()


# ---- collect ------------------------------------------------------------------------------------


def test_collect_ok(built: Built, filled: Filled, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "collect", built.out, filled.dir)
    assert code == 0, out + err
    assert "56" in out, "completeness should be printed"
    assert (built.out / "ratings_blinded.csv").is_file()
    assert "Traceback" not in out + err


def test_collect_accepts_several_returned_arguments(built: Built, filled: Filled, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "collect", built.out, *[filled.paths[r] for r in ("R1", "R2", "R3")])
    assert code == 0, out + err
    assert (built.out / "ratings_blinded.csv").is_file()


def test_collect_errors_exit_1_and_print_problems(built: Built, filled: Filled, capsys: pytest.CaptureFixture[str]) -> None:
    edit_cell(filled.paths["R1"], 0, "accuracy", "nope")
    code, out, err = run(capsys, "collect", built.out, filled.dir)
    assert code == 1
    assert "invalid_value" in out + err, "the problem code should be shown"
    assert "Traceback" not in out + err
    assert not (built.out / "ratings_blinded.csv").exists()


def test_collect_incomplete_is_still_exit_0_but_require_complete_is_1(built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fill = fill_workbooks(built.out, tmp_path / "partial", skip_raters=("R3",))
    code, out, err = run(capsys, "collect", built.out, fill.dir)
    assert code == 0, out + err
    assert "missing_workbook" in out + err
    code, out, err = run(capsys, "collect", built.out, fill.dir, "--require-complete")
    assert code == 1
    assert not (built.out / "ratings_blinded.csv").exists()


def test_collect_integrity_failure_is_exit_2(built: Built, filled: Filled, capsys: pytest.CaptureFixture[str]) -> None:
    key = built.out / "private" / "key.json"
    key.write_bytes(key.read_bytes() + b" ")
    code, out, err = run(capsys, "collect", built.out, filled.dir)
    assert code == 2
    assert_clean_error(out, err)


def test_collect_missing_study_folder_is_exit_2(tmp_path: Path, filled: Filled, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "collect", tmp_path / "nowhere", filled.dir)
    assert code == 2
    assert_clean_error(out, err)


def test_collect_says_which_earlier_files_it_removed(built: Built, filled: Filled, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "collect", built.out, filled.dir)
    assert code == 0, out + err
    assert "Removed" not in out and "unblind" not in out, "a first check removes nothing"
    assert run(capsys, "unblind", built.out)[0] == 0
    assert (built.out / "ratings.csv").is_file() and (built.out / "comments.csv").is_file()
    # a second check: the unblinded files came from the first one and are deleted
    code, out, err = run(capsys, "collect", built.out, filled.dir)
    assert code == 0, out + err
    assert not (built.out / "ratings.csv").exists() and not (built.out / "comments.csv").exists()
    lines = out.splitlines()
    assert "Removed, because they belonged to an earlier check: ratings.csv and comments.csv." in lines
    assert "  To get the unblinded files back, run 'ratersheet unblind' again." in lines
    # a failed check after an unblinding removes the blinded dataset as well, and says so
    assert run(capsys, "unblind", built.out)[0] == 0
    edit_cell(filled.paths["R1"], 0, "accuracy", "nope")
    code, out, err = run(capsys, "collect", built.out, filled.dir)
    assert code == 1
    assert sorted(p.name for p in built.out.glob("*.csv")) == []
    lines = out.splitlines()
    removed = next(line for line in lines if line.startswith("Removed"))
    for name in ("ratings.csv", "comments.csv", "ratings_blinded.csv", "comments_blinded.csv"):
        assert re.search(rf"[ ,]{re.escape(name)}\b", removed), f"{name} is not named in: {removed}"
    assert "  To get the unblinded files back, run 'ratersheet unblind' again once a check has passed." in lines
    # the same failed check again: nothing is left to remove, so nothing is said
    code, out, err = run(capsys, "collect", built.out, filled.dir)
    assert code == 1 and "Removed" not in out and "unblind" not in out


def test_collect_says_when_no_returned_file_was_read(built: Built, filled: Filled, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "collect", built.out, filled.dir)
    assert code == 0 and "no returned file was read" not in out
    wrong = tmp_path / "wrong-folder"
    wrong.mkdir()
    (wrong / "notes.txt").write_text("not a sheet", encoding="utf-8")
    code, out, err = run(capsys, "collect", built.out, wrong)
    assert code == 0, "missing sheets are warnings unless --require-complete is given"
    note = next(line for line in out.splitlines() if "no returned file was read" in line)
    assert note.startswith("Note:") and "nothing was checked" in note and "wrong folder" in note
    assert "Traceback" not in out + err


def test_collect_says_that_an_existing_methods_text_is_out_of_date(built: Built, filled: Filled, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "collect", built.out, filled.dir)
    assert code == 0 and "methods" not in out, "no methods.md yet: nothing to say"
    assert run(capsys, "methods", built.out)[0] == 0
    code, out, err = run(capsys, "collect", built.out, filled.dir)
    assert code == 0, out + err
    note = next(line for line in out.splitlines() if "methods.md" in line)
    assert note.startswith("Note:") and "before this check" in note and "'ratersheet methods' again" in note


# ---- verify -------------------------------------------------------------------------------------


def test_verify_key_only(built: Built, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "verify", built.out)
    assert code == 0, out + err
    assert "Traceback" not in out + err


def test_verify_with_sheets(built: Built, filled: Filled, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "verify", built.out, *[built.result.sheets[r] for r in ("R1", "R2")], filled.paths["R3"])
    assert code == 0, out + err


def test_verify_sheet_mismatch_is_exit_1(built: Built, filled: Filled, capsys: pytest.CaptureFixture[str]) -> None:
    edit_meta(filled.paths["R2"], key_sha256="e" * 64)
    code, out, err = run(capsys, "verify", built.out, filled.paths["R1"], filled.paths["R2"])
    assert code == 1
    assert "Traceback" not in out + err


def test_verify_sheet_of_another_study_is_exit_1(built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    other = make_built(tmp_path, name="proj2", out="out2", nonce="5" * 64, **dict(DEFAULT_BUILD_KWARGS, seed=99))
    code, out, err = run(capsys, "verify", built.out, other.result.sheets["R1"])
    assert code == 1


def test_verify_edited_key_fails(built: Built, capsys: pytest.CaptureFixture[str]) -> None:
    key = built.out / "private" / "key.json"
    key.write_bytes(key.read_bytes() + b" ")
    code, out, err = run(capsys, "verify", built.out)
    # The contract lists both "verify found a mismatch" (1) and "integrity failure" (2); either is a failure.
    assert code in (1, 2)
    assert "Traceback" not in out + err


def test_verify_missing_folder_is_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "verify", tmp_path / "nowhere")
    assert code == 2
    assert_clean_error(out, err)


def test_verify_hint_can_be_followed_from_where_the_command_was_run(built: Built, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "verify", built.out)
    assert code == 0, out + err
    hint = next(line for line in out.splitlines() if line.startswith("No sheets were given"))
    example = hint.rsplit(" ", 1)[-1]
    assert example == str(built.out / "sheets" / "*.xlsx"), "the example path includes the study folder"
    sheets = sorted(Path(example).parent.glob(Path(example).name))  # what a shell makes of the example
    assert len(sheets) == 3
    code, out, err = run(capsys, "verify", built.out, *sheets)
    assert code == 0 and "No sheets were given" not in out


def test_verify_goes_on_to_the_other_sheets_when_one_cannot_be_read(built: Built, filled: Filled, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    edit_meta(filled.paths["R3"], key_sha256="e" * 64)
    missing = tmp_path / "nowhere.xlsx"
    code, out, err = run(capsys, "verify", built.out, built.study_path, filled.paths["R1"], missing, filled.paths["R2"], filled.paths["R3"])
    assert code == 2, "a sheet that could not be read is not a verdict on it"
    lines = out.splitlines()
    assert any(line.startswith(f"{built.study_path}: NOT READ (") and "study.toml" in line for line in lines)
    assert any(line.startswith(f"{missing}: NOT READ (") and "does not exist" in line for line in lines)
    assert f"{filled.paths['R1']}: OK (carries this study's key commitment)" in lines
    assert f"{filled.paths['R2']}: OK (carries this study's key commitment)" in lines, "the sheets after the unreadable ones are still checked"
    assert any(line.startswith(f"{filled.paths['R3']}: MISMATCH") for line in lines)
    assert "error: 2 sheets could not be read" in err
    assert_clean_error(out, err)
    # without the unreadable files the verdict is the mismatch alone
    assert run(capsys, "verify", built.out, filled.paths["R1"], filled.paths["R3"])[0] == 1


# ---- unblind ------------------------------------------------------------------------------------


def test_unblind_command(built: Built, filled: Filled, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "collect", built.out, filled.dir)[0] == 0
    code, out, err = run(capsys, "unblind", built.out)
    assert code == 0, out + err
    assert (built.out / "ratings.csv").is_file()
    assert "ratings.csv" in out


def test_unblind_warns_that_the_unblinded_files_are_outside_private(built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fill = fill_workbooks(built.out, tmp_path / "partial", skip_raters=("R3",))
    assert run(capsys, "collect", built.out, fill.dir)[0] == 0
    code, out, err = run(capsys, "unblind", built.out, "--allow-incomplete")
    assert code == 0, out + err
    caution = next(line for line in out.splitlines() if line.startswith("Caution:"))
    assert "ratings.csv and comments.csv" in caution, "both unblinded files are named"
    assert "private/" in caution and "source" in caution and "still rating" in caution
    assert "not yet returned" in caution


def test_unblind_caution_names_only_the_files_that_were_written(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    b = make_built(tmp_path, **dict(DEFAULT_BUILD_KWARGS, comments=False))
    fill = fill_workbooks(b.out, tmp_path / "returned")
    assert run(capsys, "collect", b.out, fill.dir)[0] == 0
    out = run(capsys, "unblind", b.out)[1]
    caution = next(line for line in out.splitlines() if line.startswith("Caution:"))
    assert "(ratings.csv)" in caution and "comments.csv" not in caution


def test_unblind_without_collect_is_exit_2(built: Built, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "unblind", built.out)
    assert code == 2
    assert_clean_error(out, err)
    assert not (built.out / "ratings.csv").exists()


def test_unblind_incomplete_needs_the_flag(built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fill = fill_workbooks(built.out, tmp_path / "partial", skip_raters=("R3",))
    assert run(capsys, "collect", built.out, fill.dir)[0] == 0
    code, out, err = run(capsys, "unblind", built.out)
    assert code == 2
    assert_clean_error(out, err)
    assert not (built.out / "ratings.csv").exists()
    code, out, err = run(capsys, "unblind", built.out, "--allow-incomplete")
    assert code == 0, out + err
    assert (built.out / "ratings.csv").is_file()


def test_unblind_after_failed_collect_is_exit_2(built: Built, filled: Filled, capsys: pytest.CaptureFixture[str]) -> None:
    edit_cell(filled.paths["R1"], 0, "text", "edited")
    assert run(capsys, "collect", built.out, filled.dir)[0] == 1
    code, out, err = run(capsys, "unblind", built.out)
    assert code == 2
    assert_clean_error(out, err)


# ---- methods ------------------------------------------------------------------------------------


def test_methods_command(built: Built, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "methods", built.out)
    assert code == 0, out + err
    assert (built.out / "methods.md").is_file()
    assert "methods.md" in out
    text = (built.out / "methods.md").read_text(encoding="utf-8")
    assert REPO_URL in text and "[AUTHOR:" in text


def test_methods_bad_folder_is_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "methods", tmp_path / "nowhere")
    assert code == 2
    assert_clean_error(out, err)


def test_methods_with_edited_key_is_exit_2(built: Built, capsys: pytest.CaptureFixture[str]) -> None:
    key = built.out / "private" / "key.json"
    key.write_bytes(key.read_bytes() + b" ")
    code, out, err = run(capsys, "methods", built.out)
    assert code == 2
    assert_clean_error(out, err)


def test_methods_cautions_that_the_text_is_outside_private(built: Built, capsys: pytest.CaptureFixture[str]) -> None:
    # methods can be run straight after build, while everyone is still rating.
    code, out, err = run(capsys, "methods", built.out)
    assert code == 0, out + err
    lines = out.splitlines()
    assert lines[0] == str(built.out / "methods.md"), "the path still comes first, on a line of its own"
    caution = next(line for line in lines if line.startswith("Caution:"))
    assert "methods.md is not in private/" in caution
    assert "sources" in caution and "leak warning" in caution and "still rating" in caution
    assert SRC_A in (built.out / "methods.md").read_text(encoding="utf-8"), "what the caution is about"


# ---- files of the study folder that were edited by hand ---------------------------------------


def edit_manifest(out_dir: Path, **changes: Any) -> None:
    path = out_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(changes)
    path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize("command", ["methods", "collect", "unblind", "verify"])
@pytest.mark.parametrize("exc", [KeyError("per_rater"), TypeError("string indices must be integers, not 'str'")])
def test_unexpected_content_in_the_study_folder_is_one_error_line(command: str, exc: Exception, built: Built, filled: Filled, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # What a hand-edited manifest or record ends in once it has passed the checks on its entries.
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise exc

    target = {"methods": "write_methods", "collect": "collect", "unblind": "unblind", "verify": "verify_key"}[command]
    monkeypatch.setattr(f"ratersheet.cli.{target}", broken)
    argv = [command, built.out] + ([filled.dir] if command == "collect" else [])
    code, out, err = run(capsys, *argv)
    assert code == 2, "exit code 1 is reserved for a check that failed"
    assert_clean_error(out, err)
    assert err.count("\n") == 1, "one line"
    assert err.startswith("error: a file in the study folder does not have the expected content (")
    assert err.rstrip().endswith("; it may have been edited")
    assert "per_rater" in err or "string indices" in err, "the detail that tells which entry is kept"


def test_a_manifest_with_an_entry_of_the_wrong_shape_gives_no_traceback(built: Built, filled: Filled, capsys: pytest.CaptureFixture[str]) -> None:
    edit_manifest(built.out, scales="x")
    for argv in (["methods", built.out], ["collect", built.out, filled.dir]):
        code, out, err = run(capsys, *argv)
        assert code == 2, argv[0]
        assert_clean_error(out, err)


@pytest.mark.parametrize("command", ["check", "build"])
def test_a_fault_in_a_command_that_reads_no_study_folder_is_not_blamed_on_one(command: str, study_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise KeyError("a bug")

    monkeypatch.setattr("ratersheet.cli.load_study", broken)
    with pytest.raises(KeyError):
        main([command, str(study_path)])


# ---- end to end ---------------------------------------------------------------------------------


def test_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    proj = tmp_path / "study"
    proj.mkdir()
    assert run(capsys, "init", proj)[0] == 0
    study_file = proj / "study.toml"

    code, out, err = run(capsys, "check", study_file, "--strict")
    assert code == 0, out + err

    code, out, err = run(capsys, "build", study_file, "--strict")
    assert code == 0, out + err
    out_dir = proj / "ratersheet-out"
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "leak_warnings" not in manifest
    assert json.loads((out_dir / "private" / "leak_report.json").read_text(encoding="utf-8"))["warnings"] == []
    sheets = [out_dir / info["file"] for info in manifest["sheets"].values()]
    assert sheets and all(s.is_file() for s in sheets)

    filled = fill_workbooks(out_dir, tmp_path / "returned")
    assert set(filled.paths) == set(manifest["raters"])

    code, out, err = run(capsys, "collect", out_dir, tmp_path / "returned", "--require-complete")
    assert code == 0, out + err
    assert (out_dir / "ratings_blinded.csv").is_file()

    code, out, err = run(capsys, "verify", out_dir, *filled.paths.values())
    assert code == 0, out + err

    code, out, err = run(capsys, "unblind", out_dir)
    assert code == 0, out + err
    study = load_study(study_file)
    original = {i.item_id: i.source for i in study.items}
    rows = read_csv_dicts(out_dir / "ratings.csv")
    assert rows, "ratings.csv should not be empty"
    assert len(rows) == manifest["n_assignments"] * len(manifest["scales"])
    for r in rows:
        assert original[r["item_id"]] == r["source"]
        assert r["value"] != ""

    code, out, err = run(capsys, "methods", out_dir)
    assert code == 0, out + err
    methods = (out_dir / "methods.md").read_text(encoding="utf-8")
    assert REPO_URL in methods and manifest["key_sha256"] in methods

    # nothing along the way produced a traceback
    assert "Traceback" not in out + err
    # the returned sheets are still readable ratersheet workbooks
    assert read_workbook(next(iter(filled.paths.values()))).rows
    # collect agrees with the library
    assert collect(out_dir, tmp_path / "returned", require_complete=True).ok


# ---- output that the console encoding cannot show ----------------------------------------------


def run_with_stdout_encoding(encoding: str, *argv: Any) -> subprocess.CompletedProcess[bytes]:
    """Run the command line in a new process whose stdout has ``encoding`` (as when redirected)."""
    env = dict(os.environ, PYTHONIOENCODING=encoding)
    env.pop("PYTHONUTF8", None)
    return subprocess.run(
        [sys.executable, "-m", "ratersheet", *[str(a) for a in argv]], capture_output=True, env=env, timeout=120
    )


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_titles_labels_and_paths_that_stdout_cannot_encode_do_not_crash(tmp_path: Path, encoding: str) -> None:
    title = "\u00c9tude: \u03ba \u2265 0.8 \u2014 \u65e5\u672c\u8a9e"
    rows = make_item_rows({"\u6a21\u578b\u7532": 7, SRC_B: 7}, with_case=True)
    try:
        folder = tmp_path / "\u65e5\u672c"
        p = write_study(folder, rows=rows, title=title, comments=True)
    except (OSError, UnicodeError):
        pytest.skip("this file system cannot hold a non-Latin folder name")
    out_dir = folder / "ratersheet-out"

    proc = run_with_stdout_encoding(encoding, "check", p)
    assert proc.returncode == 0, proc.stderr
    assert b"Traceback" not in proc.stderr
    assert b"Leak warnings: none" in proc.stdout, "the output is complete"
    assert b"\\u65e5\\u672c\\u8a9e" in proc.stdout, "what cannot be encoded is written as an escape"

    proc = run_with_stdout_encoding(encoding, "build", p)
    assert proc.returncode == 0, proc.stderr
    assert b"Traceback" not in proc.stderr
    assert b"Key commitment (SHA-256): " in proc.stdout, "the output is not cut short"
    assert (out_dir / "manifest.json").is_file()

    fill = fill_workbooks(out_dir, tmp_path / "returned")
    for argv in (("collect", out_dir, fill.dir), ("verify", out_dir, *fill.paths.values()), ("unblind", out_dir), ("methods", out_dir)):
        proc = run_with_stdout_encoding(encoding, *argv)
        assert proc.returncode == 0, f"{argv[0]}: {proc.stderr!r}"
        assert b"Traceback" not in proc.stderr, argv[0]


def test_output_that_stdout_can_encode_is_not_escaped(tmp_path: Path) -> None:
    title = "\u00c9tude: \u03ba \u2265 0.8"
    p = write_study(tmp_path / "proj", title=title)
    proc = run_with_stdout_encoding("utf-8", "check", p)
    assert proc.returncode == 0, proc.stderr
    assert f"Study: {title}".encode("utf-8") in proc.stdout
