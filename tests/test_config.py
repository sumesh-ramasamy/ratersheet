"""load_study: defaults and every validation rule in the contract."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

import pytest

from ratersheet import ConfigError, RatersheetError, Study, load_study
from conftest import DEFAULT_SCALES, SRC_A, SRC_B, make_item_rows, write_study


def expect_error(tmp_path: Path, *needles: str, **kw: Any) -> str:
    path = write_study(tmp_path, **kw)
    with pytest.raises(ConfigError) as ei:
        load_study(path)
    msg = str(ei.value)
    for n in needles:
        assert n.lower() in msg.lower(), f"message {msg!r} should name {n!r} (kwargs: {sorted(kw)})"
    return msg


def loads(tmp_path: Path, **kw: Any) -> Study:
    return load_study(write_study(tmp_path, **kw))


# ---- a valid study ---------------------------------------------------------------------------


def test_valid_study_with_defaults(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    s = loads(tmp_path, rows=rows, instructions=None)
    assert isinstance(s, Study)
    assert s.title == "Synthetic rating study"
    assert s.seed == 7391846 and type(s.seed) is int
    assert s.instructions == ""
    assert s.raters == ("R1", "R2", "R3")
    assert s.raters_per_item == 3, "default raters_per_item is the number of raters"
    assert s.group_by_case is False
    assert s.comments is False
    assert s.ignored_columns == ()
    assert len(s.items) == 6
    assert s.sources == (SRC_A, SRC_B)
    assert [i.item_id for i in s.items] == [r["item_id"] for r in rows], "items keep file order"
    first = s.items[0]
    assert (first.item_id, first.source, first.text) == (rows[0]["item_id"], rows[0]["source"], rows[0]["text"])
    assert first.case_id is None and first.context is None


def test_scale_defaults_and_values(tmp_path: Path) -> None:
    s = loads(tmp_path)
    acc, harm = s.scales
    assert acc.id == "accuracy" and acc.label == "Accuracy" and acc.type == "ordinal"
    assert acc.values == (1, 2, 3, 4, 5) and acc.description == "1 = wrong, 5 = fully correct"
    assert acc.required is True
    assert harm.id == "harm" and harm.label == "harm", "label defaults to the id"
    assert harm.type == "nominal" and harm.values == ("none", "minor", "serious")
    assert harm.description == "" and harm.required is True


def test_explicit_options_are_read(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3}, with_case=True, with_context=True)
    scales = [{"id": "a", "type": "ordinal", "min": 0, "max": 10, "required": False}]
    s = loads(tmp_path, rows=rows, rpi=2, group_by_case=True, comments=True, scales=scales, raters=["x", "y", "z"])
    assert s.raters_per_item == 2 and s.group_by_case is True and s.comments is True
    assert s.scales[0].values == tuple(range(0, 11)) and s.scales[0].required is False
    assert s.items[0].case_id == "cs001" and s.items[0].context == "Synthetic case note 1."


def test_study_and_items_are_frozen(tmp_path: Path) -> None:
    s = loads(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.title = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.items[0].text = "x"  # type: ignore[misc]


def test_items_file_is_relative_to_the_study_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    s = loads(tmp_path / "deep" / "er")
    assert len(s.items) == 14


def test_path_may_be_str_or_pathlike(tmp_path: Path) -> None:
    p = write_study(tmp_path)
    assert load_study(str(p)).title == load_study(p).title


def test_missing_study_file_is_a_ratersheet_error(tmp_path: Path) -> None:
    with pytest.raises(RatersheetError):
        load_study(tmp_path / "nope.toml")


def test_missing_items_file_is_a_ratersheet_error(tmp_path: Path) -> None:
    p = write_study(tmp_path)
    (tmp_path / "items.csv").unlink()
    with pytest.raises(RatersheetError):
        load_study(p)


def test_invalid_toml_is_a_ratersheet_error(tmp_path: Path) -> None:
    p = tmp_path / "study.toml"
    p.write_text("[study\ntitle = = 3", encoding="utf-8")
    with pytest.raises(RatersheetError):
        load_study(p)


# ---- unknown keys and tables ---------------------------------------------------------------


def test_unknown_table(tmp_path: Path) -> None:
    expect_error(tmp_path, "bogus", extra_toml="[bogus]\nx = 1\n")


def test_unknown_key_in_study(tmp_path: Path) -> None:
    expect_error(tmp_path, "titel", study_extra={"titel": "typo"})


def test_unknown_key_in_raters(tmp_path: Path) -> None:
    expect_error(tmp_path, "idz", raters_table={"ids": ["R1"], "idz": ["R2"]})


def test_unknown_key_in_design(tmp_path: Path) -> None:
    expect_error(tmp_path, "raters_per_items", design={"raters_per_items": 2})


def test_unknown_key_in_items_table(tmp_path: Path) -> None:
    expect_error(tmp_path, "encoding", items_table={"file": "items.csv", "encoding": "utf-8"})


def test_unknown_key_in_comments(tmp_path: Path) -> None:
    expect_error(tmp_path, "enable", comments={"enable": True})


def test_unknown_key_in_scale(tmp_path: Path) -> None:
    sc = [{"id": "a", "type": "ordinal", "min": 1, "max": 3, "step": 1}]
    expect_error(tmp_path, "step", scales=sc)


# ---- required keys and types ----------------------------------------------------------------


@pytest.mark.parametrize("missing", ["title", "seed", "raters", "items", "scales"])
def test_missing_required_parts(tmp_path: Path, missing: str) -> None:
    kw: dict[str, Any] = {}
    if missing == "title":
        kw["title"] = None
    elif missing == "seed":
        kw["seed"] = None
    elif missing == "raters":
        kw["raters"] = None
    elif missing == "items":
        kw["items_table"] = {}
    elif missing == "scales":
        kw["scales"] = []
    expect_error(tmp_path, **kw)


def test_empty_title(tmp_path: Path) -> None:
    expect_error(tmp_path, "title", title="")


def test_title_must_be_string(tmp_path: Path) -> None:
    expect_error(tmp_path, "title", title=5)


@pytest.mark.parametrize("bad", ["abc", True, 1.5])
def test_seed_must_be_an_integer_not_a_bool(tmp_path: Path, bad: Any) -> None:
    expect_error(tmp_path, "seed", seed=bad)


def test_instructions_must_be_string(tmp_path: Path) -> None:
    expect_error(tmp_path, "instructions", study_extra={"instructions": 3}, instructions=None)


def test_rater_ids_must_be_a_list(tmp_path: Path) -> None:
    expect_error(tmp_path, "ids", raters_table={"ids": "R1"})


def test_no_raters(tmp_path: Path) -> None:
    expect_error(tmp_path, "ids", raters=[])


# ---- rater ids -----------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["bad id!", "a b", "a.b", "R/1", "R\\1", "x" * 33, "", "café", "R:1"])
def test_bad_rater_id(tmp_path: Path, bad: str) -> None:
    expect_error(tmp_path, raters=["R1", bad] if bad else [bad])


@pytest.mark.parametrize("good", ["x" * 32, "a-b_C9", "7", "R1"])
def test_good_rater_ids(tmp_path: Path, good: str) -> None:
    assert loads(tmp_path, raters=[good]).raters == (good,)


def test_duplicate_rater_ids(tmp_path: Path) -> None:
    expect_error(tmp_path, "R1", raters=["R1", "R2", "R1"])


def test_duplicate_rater_ids_ignoring_case(tmp_path: Path) -> None:
    expect_error(tmp_path, "r1", raters=["R1", "r1"])


@pytest.mark.parametrize(
    "name", ["CON", "prn", "Aux", "nul", "COM1", "com5", "COM9", "LPT1", "lpt9", "Lpt3"]
)
def test_reserved_windows_names_are_rejected(tmp_path: Path, name: str) -> None:
    expect_error(tmp_path, name, raters=["R1", name])


@pytest.mark.parametrize("name", ["COM0", "CONSOLE", "LPT10", "COM", "NULL", "AUX1"])
def test_names_that_only_look_reserved_are_fine(tmp_path: Path, name: str) -> None:
    assert name in loads(tmp_path, raters=["R1", name]).raters


# ---- raters_per_item and group_by_case ------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, 4, 99])
def test_raters_per_item_out_of_range(tmp_path: Path, bad: int) -> None:
    expect_error(tmp_path, "raters_per_item", rpi=bad)


def test_raters_per_item_not_an_integer(tmp_path: Path) -> None:
    expect_error(tmp_path, "raters_per_item", design={"raters_per_item": "2"})


@pytest.mark.parametrize("ok", [1, 2, 3])
def test_raters_per_item_in_range(tmp_path: Path, ok: int) -> None:
    assert loads(tmp_path, rpi=ok).raters_per_item == ok


def test_group_by_case_requires_case_ids_column(tmp_path: Path) -> None:
    expect_error(tmp_path, "case_id", group_by_case=True)


def test_group_by_case_requires_every_item_to_have_case_id(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3}, with_case=True)
    rows[2]["case_id"] = ""
    expect_error(tmp_path, "case_id", rows=rows, group_by_case=True)


def test_group_by_case_with_all_case_ids_loads(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3}, with_case=True)
    assert loads(tmp_path, rows=rows, group_by_case=True).group_by_case is True


def test_missing_case_ids_are_fine_without_group_by_case(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3}, with_case=True)
    rows[2]["case_id"] = ""
    s = loads(tmp_path, rows=rows)
    assert s.items[2].case_id is None


def test_group_by_case_must_be_bool(tmp_path: Path) -> None:
    expect_error(tmp_path, "group_by_case", design={"group_by_case": "yes"})


# ---- scales: common rules -----------------------------------------------------------------


def ordinal(id_: str = "a", **kw: Any) -> dict[str, Any]:
    return {"id": id_, "type": "ordinal", "min": 1, "max": 5, **kw}


def nominal(id_: str = "n", values: list[str] | None = None, **kw: Any) -> dict[str, Any]:
    return {"id": id_, "type": "nominal", "values": values if values is not None else ["yes", "no"], **kw}


@pytest.mark.parametrize("bad", ["Accuracy", "1x", "_a", "a-b", "a b", "", "a" * 33])
def test_bad_scale_id(tmp_path: Path, bad: str) -> None:
    expect_error(tmp_path, scales=[ordinal(bad)])


@pytest.mark.parametrize("good", ["a", "a1_b", "a" * 32])
def test_good_scale_id(tmp_path: Path, good: str) -> None:
    assert loads(tmp_path, scales=[ordinal(good)]).scales[0].id == good


def test_duplicate_scale_ids(tmp_path: Path) -> None:
    expect_error(tmp_path, "dup", scales=[ordinal("dup"), nominal("dup", label="Other")])


def test_duplicate_scale_labels(tmp_path: Path) -> None:
    expect_error(tmp_path, "Same", scales=[ordinal("a", label="Same"), ordinal("b", label="Same")])


def test_label_defaulting_to_id_can_collide(tmp_path: Path) -> None:
    expect_error(tmp_path, "b", scales=[ordinal("b"), ordinal("c", label="b")])


@pytest.mark.parametrize("label", ["rating_id", "context", "text", "comment"])
def test_scale_label_may_not_be_a_fixed_header(tmp_path: Path, label: str) -> None:
    expect_error(tmp_path, label, scales=[ordinal("a", label=label)])


@pytest.mark.parametrize("sid", ["text", "context", "comment"])
def test_scale_id_defaulting_to_fixed_header_label_is_rejected(tmp_path: Path, sid: str) -> None:
    # No label given, so the label defaults to the id, which is a fixed header.
    expect_error(tmp_path, sid, scales=[ordinal(sid)])


def test_unknown_scale_type(tmp_path: Path) -> None:
    expect_error(tmp_path, "likert", scales=[{"id": "a", "type": "likert"}])


def test_scale_type_missing(tmp_path: Path) -> None:
    expect_error(tmp_path, "type", scales=[{"id": "a", "min": 1, "max": 5}])


# ---- ordinal scales -------------------------------------------------------------------------


@pytest.mark.parametrize("lo,hi", [(5, 1), (3, 3)])
def test_ordinal_min_must_be_below_max(tmp_path: Path, lo: int, hi: int) -> None:
    expect_error(tmp_path, "a", scales=[{"id": "a", "type": "ordinal", "min": lo, "max": hi}])


def test_ordinal_at_most_eleven_points(tmp_path: Path) -> None:
    expect_error(tmp_path, "a", scales=[{"id": "a", "type": "ordinal", "min": 1, "max": 12}])


@pytest.mark.parametrize("lo,hi", [(1, 11), (0, 10), (-5, 5), (1, 2)])
def test_ordinal_allowed_sizes(tmp_path: Path, lo: int, hi: int) -> None:
    sc = loads(tmp_path, scales=[{"id": "a", "type": "ordinal", "min": lo, "max": hi}]).scales[0]
    assert sc.values == tuple(range(lo, hi + 1))


@pytest.mark.parametrize("bad", [1.5, "1", True])
def test_ordinal_min_must_be_integer(tmp_path: Path, bad: Any) -> None:
    expect_error(tmp_path, "a", scales=[{"id": "a", "type": "ordinal", "min": bad, "max": 5}])


@pytest.mark.parametrize("bad", [5.5, "5", True])
def test_ordinal_max_must_be_integer(tmp_path: Path, bad: Any) -> None:
    expect_error(tmp_path, "a", scales=[{"id": "a", "type": "ordinal", "min": -3, "max": bad}])


def test_ordinal_requires_min_and_max(tmp_path: Path) -> None:
    expect_error(tmp_path, "a", scales=[{"id": "a", "type": "ordinal", "min": 1}])
    expect_error(tmp_path, "a", scales=[{"id": "a", "type": "ordinal", "max": 5}])


def test_ordinal_does_not_allow_values(tmp_path: Path) -> None:
    expect_error(tmp_path, "values", scales=[ordinal("a", values=["x", "y"])])


# ---- nominal scales -------------------------------------------------------------------------


def test_nominal_needs_two_values(tmp_path: Path) -> None:
    expect_error(tmp_path, "n", scales=[nominal("n", ["only"])])
    expect_error(tmp_path, "n", scales=[nominal("n", [])])


def test_nominal_requires_values(tmp_path: Path) -> None:
    expect_error(tmp_path, "values", scales=[{"id": "n", "type": "nominal"}])


def test_nominal_values_unique(tmp_path: Path) -> None:
    expect_error(tmp_path, "n", scales=[nominal("n", ["a", "b", "a"])])


def test_nominal_values_unique_ignoring_case(tmp_path: Path) -> None:
    expect_error(tmp_path, "n", scales=[nominal("n", ["Yes", "yes"])])


@pytest.mark.parametrize("bad", ["a,b", ",", " lead", "trail ", "  "])
def test_nominal_value_forbidden_forms(tmp_path: Path, bad: str) -> None:
    expect_error(tmp_path, "n", scales=[nominal("n", ["ok", bad])])


def test_nominal_empty_string_value(tmp_path: Path) -> None:
    expect_error(tmp_path, "n", scales=[nominal("n", ["ok", ""])])


def test_nominal_values_must_be_strings(tmp_path: Path) -> None:
    expect_error(tmp_path, "n", scales=[nominal("n", ["ok", 3])])  # type: ignore[list-item]


def test_nominal_joined_length_limit_255(tmp_path: Path) -> None:
    ok = ["a" * 127, "b" * 127]  # 127 + 1 + 127 == 255
    assert loads(tmp_path, scales=[nominal("n", ok)]).scales[0].values == tuple(ok)
    too_long = ["a" * 127, "b" * 128]  # 256
    expect_error(tmp_path / "second", "n", scales=[nominal("n", too_long)])


def test_nominal_does_not_allow_min_or_max(tmp_path: Path) -> None:
    expect_error(tmp_path, "min", scales=[nominal("n", min=1)])
    expect_error(tmp_path / "second", "max", scales=[nominal("n", max=3)])


# ---- items file -----------------------------------------------------------------------------


@pytest.mark.parametrize("col", ["item_id", "source", "text"])
def test_missing_required_column(tmp_path: Path, col: str) -> None:
    cols = [c for c in ("item_id", "source", "text") if c != col]
    expect_error(tmp_path, col, columns=cols)


def test_duplicate_item_ids(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    rows[4]["item_id"] = rows[1]["item_id"]
    expect_error(tmp_path, rows[1]["item_id"], rows=rows)


def test_duplicate_item_ids_after_stripping(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    rows[4]["item_id"] = "  " + rows[1]["item_id"] + " "
    expect_error(tmp_path, rows[1]["item_id"], rows=rows)


def test_empty_item_id(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    rows[2]["item_id"] = "  "
    expect_error(tmp_path, rows=rows)


def test_empty_source(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    rows[2]["source"] = " "
    expect_error(tmp_path, rows[2]["item_id"], rows=rows)


@pytest.mark.parametrize("text", ["", "   ", "\n\t \n"])
def test_empty_text(tmp_path: Path, text: str) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    rows[3]["text"] = text
    expect_error(tmp_path, rows[3]["item_id"], rows=rows)


def test_text_length_limit(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    rows[1]["text"] = "a" * 32767
    assert len(loads(tmp_path / "ok", rows=rows).items[1].text) == 32767
    rows[1]["text"] = "a" * 32768
    expect_error(tmp_path / "bad", rows[1]["item_id"], rows=rows)


def test_context_length_limit(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3}, with_context=True)
    rows[1]["context"] = "c" * 32767
    assert len(loads(tmp_path / "ok", rows=rows).items[1].context or "") == 32767
    rows[1]["context"] = "c" * 32768
    expect_error(tmp_path / "bad", rows[1]["item_id"], rows=rows)


@pytest.mark.parametrize("ch", ["\x00", "\x01", "\x08", "\x0b", "\x0c", "\x1f"])
def test_control_characters_in_text(tmp_path: Path, ch: str) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    rows[2]["text"] = f"bad{ch}text"
    expect_error(tmp_path, rows[2]["item_id"], rows=rows)


def test_control_characters_in_context(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3}, with_context=True)
    rows[2]["context"] = "bad\x07context"
    expect_error(tmp_path, rows[2]["item_id"], rows=rows)


def test_tab_newline_and_carriage_return_are_allowed(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    rows[2]["text"] = "col1\tcol2\nnext line"
    s = loads(tmp_path, rows=rows)
    assert s.items[2].text == "col1\tcol2\nnext line"


def test_at_least_two_items(tmp_path: Path) -> None:
    expect_error(tmp_path, rows=make_item_rows({SRC_A: 1}))


def test_two_items_is_enough(tmp_path: Path) -> None:
    assert len(loads(tmp_path, rows=make_item_rows({SRC_A: 1, SRC_B: 1})).items) == 2


def test_bom_is_tolerated(tmp_path: Path) -> None:
    s = loads(tmp_path, bom=True)
    assert s.items[0].item_id == "it0001"
    assert (tmp_path / "items.csv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_extra_columns_are_ignored_and_listed(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    for i, r in enumerate(rows):
        r["annotator_note"] = f"note {i}"
        r["internal_ref"] = f"ref {i}"
    s = loads(tmp_path, rows=rows)
    assert set(s.ignored_columns) == {"annotator_note", "internal_ref"}
    assert len(s.ignored_columns) == 2
    dumped = repr(s.items)
    assert "note 0" not in dumped and "ref 0" not in dumped


def test_known_optional_columns_are_not_listed_as_ignored(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3}, with_case=True, with_context=True)
    assert loads(tmp_path, rows=rows).ignored_columns == ()


def test_quoted_multiline_text_is_preserved(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3}, with_context=True)
    tricky = 'First line, with a comma\nSecond line with "quotes"\n\nFourth line'
    rows[0]["text"] = tricky
    rows[0]["context"] = "Context\nover two lines"
    s = loads(tmp_path, rows=rows)
    assert s.items[0].text == tricky
    assert s.items[0].context == "Context\nover two lines"
    assert len(s.items) == 6, "a quoted newline must not split the row"


def test_stripping_rules(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3}, with_case=True, with_context=True)
    rows[0]["item_id"] = "  idx1 "
    rows[0]["source"] = "\tsrcpadded  "
    rows[0]["case_id"] = " cx1 "
    rows[0]["text"] = "  keep my spaces  "
    rows[0]["context"] = "  keep context spaces\n"
    it = loads(tmp_path, rows=rows).items[0]
    assert it.item_id == "idx1"
    assert it.source == "srcpadded"
    assert it.case_id == "cx1"
    assert it.text == "  keep my spaces  "
    assert it.context == "  keep context spaces\n"


def test_empty_case_id_and_context_become_none(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3}, with_case=True, with_context=True)
    rows[1]["case_id"] = ""
    rows[1]["context"] = ""
    it = loads(tmp_path, rows=rows).items[1]
    assert it.case_id is None and it.context is None


def test_sources_are_distinct_and_sorted(tmp_path: Path) -> None:
    rows = make_item_rows({"zeta": 2, "alpha": 2, "Mid": 2})
    assert loads(tmp_path, rows=rows).sources == tuple(sorted({"zeta", "alpha", "Mid"}))


def test_default_scales_constant_is_valid() -> None:
    # guards the shared test fixture itself
    assert [s["id"] for s in DEFAULT_SCALES] == ["accuracy", "harm"]
    assert os.sep  # keeps the os import honest for platforms where path handling differs


# ---- the items file is read strictly, and its errors name the cause ---------------------------

HEADER = "item_id,source,text\n"


def test_unescaped_quote_inside_a_quoted_text_is_refused(tmp_path: Path) -> None:
    # Lenient CSV reading would drop a quote mark here and hand raters an altered text.
    items = HEADER + 'a,s1,"The label says "do not exceed 4 g" per day."\nb,s2,plain text\nc,s1,more\n'
    msg = expect_error(tmp_path, "not valid CSV", "line 2", "written twice", items_text=items)
    assert "items.csv" in msg


def test_unclosed_quote_is_refused_instead_of_swallowing_rows(tmp_path: Path) -> None:
    items = HEADER + 'a,s1,fine\nb,s2,"An opening quote that is never closed\nc,s1,swallowed\nd,s2,swallowed too\n'
    expect_error(tmp_path, "not valid CSV", "never closed", items_text=items)


def test_correct_quoting_still_loads_in_strict_mode(tmp_path: Path) -> None:
    items = HEADER + 'a,s1,"He said ""yes"", then left"\nb,s2,5" pipe\nc,s1,"two\nlines"\n'
    s = loads(tmp_path, items_text=items)
    assert [i.text for i in s.items] == ['He said "yes", then left', '5" pipe', "two\nlines"]


@pytest.mark.parametrize("sep,name", [(";", "semicolons"), ("\t", "tabs")])
def test_other_separators_are_named_in_the_error(tmp_path: Path, sep: str, name: str) -> None:
    items = "\n".join(sep.join(r) for r in [("item_id", "source", "text"), ("a", "s1", "one"), ("b", "s2", "two")])
    msg = expect_error(tmp_path, "item_id", "read as", f"separated by {name}", "commas", items_text=items + "\n")
    assert f"item_id{sep}source{sep}text".replace("\t", "\\t") in msg, "the header is shown as it was read"


def test_utf16_items_file_without_bom_is_named(tmp_path: Path) -> None:
    path = write_study(tmp_path)
    (tmp_path / "items.csv").write_bytes((HEADER + "a,s1,one\nb,s2,two\n").encode("utf-16-le"))
    with pytest.raises(ConfigError, match="UTF-16"):
        load_study(path)


def test_a_really_missing_column_gets_no_separator_hint(tmp_path: Path) -> None:
    msg = expect_error(tmp_path, "'source'", "read as", items_text="item_id,text;notes\na,one\nb,two\n")
    assert "separated by" not in msg and "UTF-16" not in msg


def test_very_long_text_names_its_item_and_csv_limit_is_restored(tmp_path: Path) -> None:
    import csv

    before = csv.field_size_limit()
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    rows[1]["text"] = "x" * 200_000  # longer than the csv module's own field limit
    expect_error(tmp_path, rows[1]["item_id"], "200000", "32767", rows=rows)
    assert csv.field_size_limit() == before, "the process-wide limit must be put back"


# ---- the study file ----------------------------------------------------------------------------


def test_study_file_with_bom_loads_like_the_plain_one(tmp_path: Path) -> None:
    path = write_study(tmp_path)
    plain = load_study(path)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))  # as Notepad saves
    assert load_study(path) == plain


def test_integer_too_long_to_convert_is_a_config_error(tmp_path: Path) -> None:
    path = write_study(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("seed = 7391846", "seed = " + "9" * 5000), encoding="utf-8")
    with pytest.raises(ConfigError, match="study.toml"):
        load_study(path)


def test_deeply_nested_arrays_are_a_config_error(tmp_path: Path) -> None:
    path = write_study(tmp_path, extra_toml="\n[extra]\nnest = " + "[" * 5000 + "]" * 5000 + "\n")
    with pytest.raises(ConfigError, match="study.toml"):
        load_study(path)


@pytest.mark.parametrize("br", ["\n", "\r", "\r\n", " ", "\x85"])
def test_title_with_a_line_break_is_refused(tmp_path: Path, br: str) -> None:
    expect_error(tmp_path, "[study] title", "line break", title=f"Line one{br}Line two")


def test_scale_label_with_a_line_break_is_refused(tmp_path: Path) -> None:
    scales = [dict(DEFAULT_SCALES[0], label="Accuracy\nof the answer"), DEFAULT_SCALES[1]]
    expect_error(tmp_path, "accuracy", "label", "line break", scales=scales)


def test_nominal_value_with_a_line_break_is_refused(tmp_path: Path) -> None:
    scales = [DEFAULT_SCALES[0], dict(DEFAULT_SCALES[1], values=["none", "minor\nor less", "serious"])]
    expect_error(tmp_path, "harm", "values", "line break", scales=scales)


def test_line_breaks_stay_allowed_in_instructions_and_descriptions(tmp_path: Path) -> None:
    scales = [dict(DEFAULT_SCALES[0], description="1 = wrong\n5 = fully correct"), DEFAULT_SCALES[1]]
    s = loads(tmp_path, instructions="First paragraph.\n\nSecond paragraph.", scales=scales)
    assert "\n\n" in s.instructions and "\n" in s.scales[0].description


# ---- source and case labels --------------------------------------------------------------------

NFC_LABEL = "Émile"  # one code point for the accented letter
NFD_LABEL = "Émile"  # the same letter as a base letter and a combining accent


def test_source_labels_in_either_normal_form_are_one_source(tmp_path: Path) -> None:
    rows = make_item_rows({NFC_LABEL: 4, "zeta": 4})
    for r in rows[::4]:  # two of the four accented rows come from a decomposed spelling
        assert r["source"] == NFC_LABEL
        r["source"] = NFD_LABEL
    s = loads(tmp_path, rows=rows)
    assert s.sources == tuple(sorted([NFC_LABEL, "zeta"]))
    assert sum(i.source == NFC_LABEL for i in s.items) == 4


def test_the_normal_form_of_the_labels_does_not_change_the_study(tmp_path: Path) -> None:
    def study(label: str, folder: str) -> Study:
        rows = make_item_rows({label: 3, "Zeta": 3, "alpha": 3}, with_case=True)
        for r in rows:
            r["case_id"] = r["case_id"].replace("cs", label)
        return loads(tmp_path / folder, rows=rows)

    assert study(NFC_LABEL, "nfc") == study(NFD_LABEL, "nfd")


def test_sources_differing_only_in_case_are_refused(tmp_path: Path) -> None:
    rows = make_item_rows({"GPT-4": 3, "human": 3})
    rows[4]["source"] = "gpt-4"
    msg = expect_error(tmp_path, "'gpt-4'", "'GPT-4'", "upper and lower case", "row 6", "row 2", rows=rows)
    assert "items.csv" in msg


# ---- the cell limit counts the text as it is stored ------------------------------------------------


@pytest.mark.parametrize("column", ["text", "context"])
def test_text_whose_escaped_form_passes_the_cell_limit_is_refused_by_name(tmp_path: Path, column: str) -> None:
    # The sheet stores the underscore of a literal _xHHHH_ sequence as _x005F_ (6 more characters).
    # A text that only fits before that would be cut off in every rater's sheet without a word.
    start = "See column total_x0041_ in the table. "
    rows = make_item_rows({SRC_A: 3, SRC_B: 3}, with_context=True)
    rows[1][column] = start + "y" * (32767 - 6 - len(start))
    assert len(getattr(loads(tmp_path / "fits", rows=rows).items[1], column)) == 32761
    rows[1][column] += "y"
    msg = expect_error(tmp_path / "one-over", rows[1]["item_id"], column, "32762", "_xHHHH_", "32767", rows=rows)
    assert "1 literal _xHHHH_ sequence " in msg and "6 more characters" in msg
    rows[1][column] = start + "y" * (32767 - len(start))
    expect_error(tmp_path / "full", rows[1]["item_id"], "32767 characters long and contains", rows=rows)


def test_several_escape_like_sequences_each_count_towards_the_cell_limit(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 3, SRC_B: 3})
    rows[2]["text"] = "_x0041_x0042_x0043_" + "y" * (32767 - 19 - 18)  # three sequences sharing underscores
    assert len(loads(tmp_path / "fits", rows=rows).items[2].text) == 32749
    rows[2]["text"] += "y"
    msg = expect_error(tmp_path / "over", rows[2]["item_id"], "3 literal _xHHHH_ sequences", rows=rows)
    assert "32750 characters long" in msg
