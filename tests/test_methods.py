"""methods_text / write_methods: what the tool states, and what it must leave to the author."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import pytest

import ratersheet
from ratersheet import IntegrityError, RatersheetError, collect, methods_text, unblind, write_methods
from conftest import (
    DEFAULT_BUILD_KWARGS,
    REPO_URL,
    SRC_A,
    SRC_B,
    SRC_C,
    Built,
    Filled,
    edit_cell,
    fill_workbooks,
    make_built,
    make_item_rows,
)

PLACEHOLDER_RE = re.compile(r"\[AUTHOR:[^\]]*\]")


def outside_placeholders(text: str) -> str:
    return PLACEHOLDER_RE.sub("", text)


def has_number(text: str, n: int) -> bool:
    return re.search(rf"(?<![\w.]){n}(?![\w])", text) is not None


def paragraph(text: str, name: str) -> str:
    """The paragraph of the methods text that starts with the bold heading `name`."""
    found = [p for p in text.split("\n\n") if p.startswith(f"**{name}")]
    assert len(found) == 1, f"expected one paragraph headed {name!r}"
    return found[0]


def edit_json(path: Path, change: Callable[[dict], Any]) -> None:
    """Rewrite a JSON file of the study folder after applying `change` to its object."""
    obj = json.loads(path.read_text(encoding="utf-8"))
    change(obj)
    path.write_text(json.dumps(obj), encoding="utf-8")


def leak_report_path(built: Built) -> Path:
    return built.out / "private" / "leak_report.json"


# ---- right after build ------------------------------------------------------------------------


def test_after_build(built: Built) -> None:
    t = methods_text(built.out)
    assert isinstance(t, str) and len(t) > 500
    low = t.lower()
    assert "ratersheet" in low and ratersheet.__version__ in t
    assert REPO_URL in t
    assert built.manifest["key_sha256"] in t, "the key commitment must be stated"
    # measured numbers: 14 items, 2 sources, 3 raters, 2 raters per item
    for n in (14, 3, 2):
        assert has_number(t, n), f"the number {n} should appear"
    # items per source (7 each), range of items per rater (9 to 10), per source per rater (3 to 4)
    assert has_number(t, 7)
    assert has_number(t, 9) and has_number(t, 10)
    assert "random" in low and "code" in low
    assert "not been collected" in low or "not yet been collected" in low or "no ratings" in low
    assert "leak" in low


def test_placeholders_for_what_the_tool_cannot_know(built: Built) -> None:
    t = methods_text(built.out)
    ph = [p.lower() for p in PLACEHOLDER_RE.findall(t)]
    assert len(ph) >= 4, ph
    joined = " ".join(ph)
    assert "qualif" in joined, "raters and their qualifications must be an author placeholder"
    assert "independen" in joined, "rater independence must be an author placeholder"
    assert "key" in joined, "who held the key must be an author placeholder"
    assert any(w in joined for w in ("chosen", "sample", "number of")), "how sizes were chosen"


def test_no_claims_about_qualifications_or_independence_outside_placeholders(built: Built, filled: Filled) -> None:
    for stage in range(3):
        if stage >= 1:
            assert collect(built.out, filled.dir).ok
        if stage >= 2:
            unblind(built.out)
        text = outside_placeholders(methods_text(built.out)).lower()
        for phrase in ("qualified", "experienced", "worked independently", "rated independently",
                       "independently of one another", "compliant", "complies with", "in compliance"):
            assert phrase not in text, f"stage {stage}: unsupported claim {phrase!r}"


def test_fully_crossed_is_stated_for_a_crossed_design(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 5, SRC_B: 5}), raters=["R1", "R2"], rpi=2)
    assert "fully crossed" in methods_text(b.out).lower()


def test_not_fully_crossed_design_is_not_called_crossed(built: Built) -> None:
    low = methods_text(built.out).lower()
    for m in re.finditer("fully crossed", low):
        assert "not" in low[max(0, m.start() - 20): m.start()] or "partially" in low[max(0, m.start() - 20): m.start()], (
            "a design with 2 of 3 raters per item must not be called fully crossed"
        )


def test_group_by_case_is_mentioned(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 4, SRC_B: 4}, with_case=True), group_by_case=True, rpi=2)
    assert "case" in methods_text(b.out).lower()


def test_leak_warnings_are_reported(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 5, SRC_B: 5})
    rows[0]["text"] = "As an AI language model I would say " + rows[0]["text"]
    b = make_built(tmp_path, rows=rows)
    assert json.loads(leak_report_path(b).read_text(encoding="utf-8"))["warnings"]
    t = methods_text(b.out)
    assert "self_identification" in t or "self-identification" in t.lower() or "identif" in t.lower()
    assert any("leak" in p.lower() for p in PLACEHOLDER_RE.findall(t)), "what was done about leaks is for the author"


# ---- the leak paragraph states what ran, from the private leak report -------------------------


def leak_paragraph(built: Built) -> str:
    return paragraph(methods_text(built.out), "Check for identifying content")


def test_leak_warnings_are_taken_from_the_private_leak_report(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 5, SRC_B: 5})
    rows[0]["text"] = "As an AI language model I would say " + rows[0]["text"]
    b = make_built(tmp_path, rows=rows)
    assert "leak_warnings" not in b.manifest, "the kinds of warning are a hint and stay private"
    assert "raised 1 warning: 1 of kind `self_identification`" in leak_paragraph(b)
    # The paragraph follows the leak report, so the report is where it reads the kinds.
    extra = {"code": "length_separable", "message": "synthetic", "item_ids": [], "sources": [SRC_A, SRC_B]}
    edit_json(leak_report_path(b), lambda report: report["warnings"].append(extra))
    text = leak_paragraph(b)
    assert "raised 2 warnings" in text
    assert "1 of kind `self_identification`" in text and "1 of kind `length_separable`" in text


def test_leak_paragraph_when_every_check_ran(built: Built) -> None:
    text = leak_paragraph(built)
    assert "for each of the 2 source labels" in text
    assert "fixed list of English phrases" in text
    assert "It compared the length of the texts" in text and "between the two sources" in text
    assert "did the same for the contexts" in text, "the built study shows a context column"
    assert "These checks raised no warnings." in text
    assert "not compared" not in text and "did not" not in text


def test_leak_paragraph_does_not_claim_a_comparison_that_was_skipped(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 4, SRC_B: 4}))
    text = leak_paragraph(b)
    assert "It did not compare the length or the markup of the texts between the two sources" in text
    assert "a source with fewer than 5 items is not compared" in text
    assert "It compared" not in text
    assert "The checks that were made raised no warnings." in text
    assert "contexts" not in text, "this study shows no context column"


def test_leak_paragraph_counts_the_pairs_that_were_compared(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 4, SRC_B: 6, SRC_C: 6}))
    text = leak_paragraph(b)
    assert "for 1 of the 3 pairs of sources" in text
    assert "the other 2 pairs were not compared, because a source with fewer than 5 items" in text
    assert "The checks that were made raised no warnings." in text


def test_leak_paragraph_counts_the_labels_that_were_searched_for(tmp_path: Path) -> None:
    b = make_built(tmp_path / "some", rows=make_item_rows({"o3": 6, "MD": 6, SRC_C: 6}))
    text = leak_paragraph(b)
    assert "for 1 of the 3 source labels" in text
    assert "the other 2 are shorter than 3 characters" in text
    assert "for each of the 3 pairs of sources" in text
    assert "The checks that were made raised no warnings." in text

    b = make_built(tmp_path / "none", rows=make_item_rows({"o3": 6, "MD": 6}))
    text = leak_paragraph(b)
    assert "It did not search the texts for any of the 2 source labels" in text
    assert "It searched the texts for" not in text


def test_leak_paragraph_names_one_unsearched_label_in_words(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({"o3": 6, SRC_B: 6}))
    text = leak_paragraph(b)
    assert "for 1 of the 2 source labels; the other one is shorter than 3 characters" in text
    assert "the other 1 " not in text


def test_leak_paragraph_with_a_single_source(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 6}), raters=["R1", "R2"])
    text = leak_paragraph(b)
    assert "It searched the texts for the source label." in text
    assert "single source" in text and "It compared" not in text


def test_leak_paragraph_uses_the_thresholds_recorded_at_build_time(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 4, SRC_B: 4}))

    def other_thresholds(report: dict) -> None:
        report["coverage"]["min_items_per_source"] = 7
        report["coverage"]["min_label_chars"] = 9
        report["coverage"]["n_labels_searched"] = 0

    edit_json(leak_report_path(b), other_thresholds)
    text = leak_paragraph(b)
    assert "fewer than 7 items" in text and "shorter than 9 characters" in text


def test_leak_paragraph_without_a_coverage_record_states_only_the_warnings(tmp_path: Path) -> None:
    rows = make_item_rows({SRC_A: 5, SRC_B: 5})
    rows[0]["text"] = "As an AI language model I would say " + rows[0]["text"]
    b = make_built(tmp_path, rows=rows)
    edit_json(leak_report_path(b), lambda report: report.pop("coverage"))
    text = leak_paragraph(b)
    assert "does not say which source labels were searched for" in text
    assert "It searched" not in text and "It compared" not in text
    assert "The check raised 1 warning: 1 of kind `self_identification`" in text


def test_methods_needs_the_leak_report(built: Built) -> None:
    leak_report_path(built).unlink()
    with pytest.raises(RatersheetError, match="leak_report.json does not exist"):
        methods_text(built.out)


@pytest.mark.parametrize(
    "damage",
    [
        lambda report: report.pop("warnings"),
        lambda report: report.update(warnings=[{"message": "no code"}]),
        lambda report: report.update(coverage="all of it"),
        lambda report: report["coverage"].pop("n_pairs"),
        lambda report: report["coverage"].update(n_labels="two"),
    ],
)
def test_methods_refuses_a_damaged_leak_report(built: Built, damage: Callable[[dict], Any]) -> None:
    edit_json(leak_report_path(built), damage)
    with pytest.raises(RatersheetError, match="not a leak report"):
        methods_text(built.out)


# ---- what the tool did, not what it cannot know -----------------------------------------------


def test_does_not_say_the_items_were_rated_or_the_sheets_received(built: Built, filled: Filled) -> None:
    for stage in range(3):
        if stage >= 1:
            assert collect(built.out, filled.dir).ok
        if stage >= 2:
            unblind(built.out)
        t = methods_text(built.out)
        assert "were rated by" not in t, f"stage {stage}: the tool cannot know who rated"
        assert "Rating sheets were generated for 3 raters." in paragraph(t, "Items and raters")
        assert "received a separate spreadsheet" not in t, f"stage {stage}: the tool cannot know delivery"
        assert "A separate spreadsheet was generated for each rater" in paragraph(t, "Blinding")


def test_key_hash_wording_names_what_was_compared(built: Built, filled: Filled) -> None:
    collect(built.out, filled.dir)
    unblind(built.out)
    t = methods_text(built.out)
    blinding = paragraph(t, "Blinding")
    assert "matched that hash" not in blinding
    assert "the key file matched the hash recorded in the manifest" in blinding
    assert "Anyone who holds a sheet can check" in blinding and "Instructions page" in blinding
    unblinding = paragraph(t, "Unblinding")
    assert "matched the hash recorded in the study's manifest when the sheets were generated" in unblinding
    assert "matched the hash recorded when the sheets were generated" not in unblinding


def test_assignment_names_what_the_design_was_generated_from(built: Built) -> None:
    text = paragraph(methods_text(built.out), "Assignment and order")
    assert "random seed and a random value that is kept in the unblinding key" in text
    assert "from a fixed random seed" not in text, "the seed alone no longer gives the design"


def test_a_line_break_in_the_title_does_not_split_the_heading(built: Built) -> None:
    edit_json(built.out / "manifest.json", lambda m: m.update(title="Line one\r\nLine two\n\n## Fake"))
    lines = methods_text(built.out).split("\n")
    assert lines[0] == "# Methods text: Line one Line two ## Fake"
    assert lines[1] == "", "the heading is one line, followed by a blank line"
    assert not any(line.startswith("## Fake") or line == "Line two" for line in lines)


def test_one_rater_is_described_in_the_singular(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 5, SRC_B: 5}), raters=["R1"])
    t = methods_text(b.out)
    assert "Rating sheets were generated for 1 rater." in t
    assignment = paragraph(t, "Assignment and order")
    assert "The study had one rater, who was assigned every item (10 assignments in total)." in assignment
    assert "The rater was assigned 10 items in total" in assignment
    assert "The order of the items was randomised." in assignment
    assert "each rater" not in assignment.lower() and "Every rater" not in assignment
    blinding = paragraph(t, "Blinding")
    assert blinding.startswith(
        f"**Blinding.** A spreadsheet was generated for the rater by ratersheet {ratersheet.__version__}."
    )
    assert "each rater" not in blinding and "separate spreadsheet" not in blinding


def test_several_raters_each_get_a_separate_spreadsheet(built: Built) -> None:
    blinding = paragraph(methods_text(built.out), "Blinding")
    assert blinding.startswith("**Blinding.** A separate spreadsheet was generated for each rater by ")


def test_reporting_table_and_note(built: Built) -> None:
    t = methods_text(built.out)
    assert "CHART" in t and "GRRAS" in t
    assert "7b" in t
    low = t.lower()
    assert "check" in low, "a note to check wording against the published checklists"
    for item in ("6", "8", "9"):
        assert re.search(rf"GRRAS[^\n]*\b{item}\b", t), f"GRRAS item {item} should be in the table"
    assert re.search(r"CHART[^\n]*\b8\b", t)


def test_does_not_leak_the_key(built: Built) -> None:
    t = methods_text(built.out)
    for it in built.study.items:
        assert it.item_id not in t
    for a in built.key["assignments"]:
        assert a["rating_id"] not in t


# ---- later stages -----------------------------------------------------------------------------


def test_after_collect(built: Built, filled: Filled) -> None:
    before = methods_text(built.out)
    res = collect(built.out, filled.dir)
    t = methods_text(built.out)
    low = t.lower()
    assert t != before
    assert has_number(t, res.n_expected) and has_number(t, res.n_received)
    assert "not been collected" not in low
    assert REPO_URL in t and built.manifest["key_sha256"] in t


def test_after_incomplete_collect(built: Built, tmp_path: Path) -> None:
    fill = fill_workbooks(built.out, tmp_path / "partial", skip_raters=("R3",))
    res = collect(built.out, fill.dir)
    t = methods_text(built.out)
    assert has_number(t, res.n_received) and has_number(t, res.n_expected)
    assert res.n_received != res.n_expected


def test_after_unblind(built: Built, filled: Filled) -> None:
    collect(built.out, filled.dir)
    collected_text = methods_text(built.out)
    unblind(built.out)
    t = methods_text(built.out)
    assert t != collected_text, "opening the key changes what the tool can state"
    low = t.lower()
    assert "unblind" in low or "opened" in low
    assert "not been collected" not in low
    assert REPO_URL in t and built.manifest["key_sha256"] in t


def test_after_failing_collect(built: Built, filled: Filled) -> None:
    edit_cell(filled.paths["R1"], 0, "text", "edited")
    collect(built.out, filled.dir)
    t = methods_text(built.out)
    assert isinstance(t, str) and REPO_URL in t


def test_methods_text_is_deterministic_for_a_stage(built: Built) -> None:
    assert methods_text(built.out) == methods_text(built.out)


def test_methods_refuses_if_key_was_edited(built: Built) -> None:
    key = built.out / "private" / "key.json"
    key.write_bytes(key.read_bytes() + b" ")
    with pytest.raises(IntegrityError):
        methods_text(built.out)


# ---- write_methods ----------------------------------------------------------------------------


def test_write_methods(built: Built) -> None:
    p = write_methods(built.out)
    assert Path(p) == built.out / "methods.md"
    content = p.read_text(encoding="utf-8")
    assert content.strip() == methods_text(built.out).strip()
    assert b"\r" not in p.read_bytes()


def test_write_methods_accepts_str_and_overwrites(built: Built, filled: Filled) -> None:
    p1 = write_methods(str(built.out))
    first = p1.read_text(encoding="utf-8")
    collect(built.out, filled.dir)
    p2 = write_methods(built.out)
    assert p2 == p1 and p2.read_text(encoding="utf-8") != first
