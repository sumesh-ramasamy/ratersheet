"""check_leaks: four codes, thresholds, minimum items per source, word boundaries.

Also leak_coverage: what the checks could and could not look at.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from ratersheet import LeakCoverage, LeakWarning, check_leaks, leak_coverage
from ratersheet import leaks as leaks_mod
from ratersheet.leaks import coverage_notes
from conftest import SRC_A, SRC_B, SRC_C, make_item_rows, make_study, study_from_texts

CONTRACT_PHRASES = {
    "as an ai", "i am an ai", "i'm an ai", "ai language model", "language model", "chatgpt", "openai",
    "gpt-3", "gpt-4", "gpt-5", "claude", "anthropic", "gemini", "bard", "copilot", "llama", "mistral",
    "deepseek", "grok",
}


def plain(i: int) -> str:
    """Plain texts with scattered lengths."""
    return f"Synthetic sentence number {i} " + "filler " * ((i * 7919) % 11 + 1)


def plain_texts(n: int, offset: int = 0) -> list[str]:
    return [plain(i + offset) for i in range(n)]


def by_code(ws: list[LeakWarning], code: str) -> list[LeakWarning]:
    return [w for w in ws if w.code == code]


# ---- clean study ----------------------------------------------------------------------------


def test_clean_study_is_silent() -> None:
    study = make_study({SRC_A: 12, SRC_B: 12, SRC_C: 12}, with_context=True)
    assert check_leaks(study) == []


def test_returns_leakwarning_objects() -> None:
    study = study_from_texts({"zzq": ["This is Zzq speaking"] + plain_texts(5), SRC_B: plain_texts(6, 20)})
    ws = check_leaks(study)
    assert ws and all(isinstance(w, LeakWarning) for w in ws)
    for w in ws:
        assert isinstance(w.message, str) and w.message.strip()
        assert isinstance(w.item_ids, tuple) and isinstance(w.sources, tuple)


# ---- source_name_in_text -------------------------------------------------------------------


def source_study(text: str, label: str = "orion", *, context: str | None = None, n: int = 6):
    texts_a = [text] + plain_texts(n - 1)
    ctx_a = [context] + [None] * (n - 1) if context is not None else None
    return study_from_texts(
        {label: texts_a, SRC_B: plain_texts(n, 30)},
        contexts_by_source={label: ctx_a} if ctx_a else None,
    )


@pytest.mark.parametrize(
    "text",
    [
        "This answer came from orion.",
        "ORION wrote this",
        "Orion's view is stated here",
        "(orion) is mentioned",
        "see orion, then act",
        "written by Orion",
        "orion-style answer",
    ],
)
def test_source_name_in_text_fires(text: str) -> None:
    study = source_study(text)
    ws = by_code(check_leaks(study), "source_name_in_text")
    assert len(ws) == 1, f"text {text!r} should raise one source_name_in_text warning"
    assert study.items[0].item_id in ws[0].item_ids


@pytest.mark.parametrize("text", ["the orions gathered", "reorion test", "orion2 is a name", "2orion", "oriONic", "Orionis"])
def test_source_label_inside_longer_word_does_not_fire(text: str) -> None:
    ws = check_leaks(source_study(text))
    assert by_code(ws, "source_name_in_text") == [], f"{text!r} must not fire"


def test_source_name_in_context_fires() -> None:
    study = source_study("A plain text with no label.", context="Context mentioning ORION here")
    ws = by_code(check_leaks(study), "source_name_in_text")
    assert len(ws) == 1 and study.items[0].item_id in ws[0].item_ids


def test_source_name_other_sources_label_is_also_checked() -> None:
    # The label of source B appearing in an item written by source A still leaks.
    texts_a = ["Reply drafted with help from srcbeta tools"] + plain_texts(5)
    study = study_from_texts({SRC_A: texts_a, SRC_B: plain_texts(6, 40)})
    ws = by_code(check_leaks(study), "source_name_in_text")
    assert len(ws) == 1 and study.items[0].item_id in ws[0].item_ids


def test_one_warning_per_label_listing_all_items() -> None:
    texts = ["From orion: one", "From ORION: two", "Orion again: three"] + plain_texts(3)
    study = study_from_texts({"orion": texts, SRC_B: plain_texts(6, 30)})
    ws = by_code(check_leaks(study), "source_name_in_text")
    assert len(ws) == 1
    assert set(ws[0].item_ids) == {i.item_id for i in study.items[:3]}


def test_two_labels_two_warnings() -> None:
    a = ["Mentions orion here"] + plain_texts(5)
    b = ["Mentions nebulon here"] + plain_texts(5, 20)
    ws = by_code(check_leaks(study_from_texts({"orion": a, "nebulon": b})), "source_name_in_text")
    assert len(ws) == 2


def test_labels_shorter_than_three_characters_are_not_checked() -> None:
    texts_a = ["The ab pattern and xy"] + plain_texts(5)
    study = study_from_texts({"ab": texts_a, "xy": plain_texts(6, 30)})
    assert by_code(check_leaks(study), "source_name_in_text") == []


def test_three_character_label_is_checked() -> None:
    texts_a = ["The abc pattern"] + plain_texts(5)
    study = study_from_texts({"abc": texts_a, SRC_B: plain_texts(6, 30)})
    assert len(by_code(check_leaks(study), "source_name_in_text")) == 1


# ---- self_identification ----------------------------------------------------------------------


def test_phrase_list_matches_contract() -> None:
    assert set(leaks_mod.SELF_ID_PHRASES) == CONTRACT_PHRASES


@pytest.mark.parametrize("phrase", sorted(CONTRACT_PHRASES))
def test_each_self_id_phrase_fires(phrase: str) -> None:
    study = study_from_texts({SRC_A: [f"Note: {phrase.upper()} was said here."] + plain_texts(5), SRC_B: plain_texts(6, 30)})
    ws = by_code(check_leaks(study), "self_identification")
    assert len(ws) == 1, f"phrase {phrase!r} should raise self_identification"
    assert study.items[0].item_id in ws[0].item_ids


def test_self_id_lowercase_and_in_context() -> None:
    study = study_from_texts(
        {SRC_A: plain_texts(6), SRC_B: plain_texts(6, 30)},
        contexts_by_source={SRC_A: [None, "as an ai, I think", None, None, None, None]},
    )
    ws = by_code(check_leaks(study), "self_identification")
    assert len(ws) == 1 and study.items[1].item_id in ws[0].item_ids


def test_self_id_is_one_warning_listing_all_items() -> None:
    a = ["As an AI I cannot say"] + plain_texts(4) + ["Powered by OpenAI today"]
    b = ["Ask Gemini instead"] + plain_texts(5, 30)
    study = study_from_texts({SRC_A: a, SRC_B: b})
    ws = by_code(check_leaks(study), "self_identification")
    assert len(ws) == 1
    expected = {study.items[0].item_id, study.items[5].item_id, study.items[6].item_id}
    assert set(ws[0].item_ids) == expected


@pytest.mark.parametrize(
    "text",
    ["Claudette went home", "I saw llamas in the field", "He was grokking it", "Bardolph arrived", "an aid worker", "geminids shower"],
)
def test_self_id_respects_word_boundaries(text: str) -> None:
    study = study_from_texts({SRC_A: [text] + plain_texts(5), SRC_B: plain_texts(6, 30)})
    assert by_code(check_leaks(study), "self_identification") == [], f"{text!r} must not fire"


# ---- length_separable -----------------------------------------------------------------------


def sized(n: int, ch: str = "w") -> str:
    return ch * n


def test_length_separable_fires_both_directions() -> None:
    long_ = [sized(200 + i) for i in range(6)]
    short = [sized(20 + i) for i in range(6)]
    for first, second in [(long_, short), (short, long_)]:
        study = study_from_texts({SRC_A: first, SRC_B: second})
        ws = by_code(check_leaks(study), "length_separable")
        assert len(ws) == 1
        assert set(ws[0].sources) == {SRC_A, SRC_B}


def test_length_not_separable_when_distributions_overlap() -> None:
    a = [sized(n) for n in (10, 30, 50, 70, 90, 110)]
    b = [sized(n) for n in (20, 40, 60, 80, 100, 120)]
    assert by_code(check_leaks(study_from_texts({SRC_A: a, SRC_B: b})), "length_separable") == []


def test_length_requires_five_items_per_source() -> None:
    long_ = [sized(200 + i) for i in range(4)]
    short = [sized(20 + i) for i in range(6)]
    assert by_code(check_leaks(study_from_texts({SRC_A: long_, SRC_B: short})), "length_separable") == []
    long5 = [sized(200 + i) for i in range(5)]
    assert len(by_code(check_leaks(study_from_texts({SRC_A: long5, SRC_B: short})), "length_separable")) == 1


def test_length_constants() -> None:
    assert leaks_mod.LENGTH_AUC_THRESHOLD == 0.9
    assert leaks_mod.MIN_ITEMS_PER_SOURCE == 5


def _auc_study(a_wins_total: int):
    """10 vs 10 texts. B lengths 100,102,...,118. A gets `a_wins_total` wins out of 100 pairs, no ties."""
    b = [sized(100 + 2 * k) for k in range(10)]
    full, rest = divmod(a_wins_total, 10)
    a_lengths = [200] * full
    if rest:
        a_lengths.append(100 + 2 * rest - 1)  # odd length beats exactly `rest` of B
    while len(a_lengths) < 10:
        a_lengths.append(99)  # beats none
    return study_from_texts({SRC_A: [sized(n) for n in a_lengths], SRC_B: b})


def test_length_threshold_exactly_at_point_nine_fires() -> None:
    assert len(by_code(check_leaks(_auc_study(90)), "length_separable")) == 1


def test_length_just_below_threshold_is_silent() -> None:
    assert by_code(check_leaks(_auc_study(89)), "length_separable") == []


def test_length_threshold_exactly_at_point_one_fires() -> None:
    # Swap roles: B beats A in 90 of 100 pairs, so A beats B in 10.
    assert len(by_code(check_leaks(_auc_study(10)), "length_separable")) == 1


def test_length_just_above_low_threshold_is_silent() -> None:
    assert by_code(check_leaks(_auc_study(11)), "length_separable") == []


def test_length_ties_count_as_half() -> None:
    # Every A text is longer than 5 of the 10 B texts and equal to ... build 0.95 with ties:
    # A: 5 texts of length 200 (beat all 10) and 5 texts equal in length to the longest B (tie with 1, beat 9).
    # wins = 5*10 + 5*9 = 95, ties = 5 -> (95 + 2.5) / 100 = 0.975 -> fires.
    b = [sized(100 + 2 * k) for k in range(10)]
    a = [sized(200)] * 5 + [sized(118)] * 5
    assert len(by_code(check_leaks(study_from_texts({SRC_A: a, SRC_B: b})), "length_separable")) == 1
    # All ties -> exactly 0.5 -> silent.
    same = [sized(50)] * 6
    assert by_code(check_leaks(study_from_texts({SRC_A: same, SRC_B: list(same)})), "length_separable") == []


def test_length_one_warning_per_pair() -> None:
    long_ = [sized(300 + i) for i in range(6)]
    mid = [sized(100 + 3 * i) for i in range(6)]
    short = [sized(5 + i) for i in range(6)]
    ws = by_code(check_leaks(study_from_texts({SRC_A: long_, SRC_B: mid, SRC_C: short})), "length_separable")
    assert len(ws) == 3
    assert {frozenset(w.sources) for w in ws} == {
        frozenset({SRC_A, SRC_B}), frozenset({SRC_A, SRC_C}), frozenset({SRC_B, SRC_C})
    }


def test_length_only_separable_pair_is_reported() -> None:
    long_ = [sized(300 + i) for i in range(6)]
    b = [sized(20 + (i * 7) % 13) for i in range(6)]
    c = [sized(20 + (i * 5) % 13) for i in range(6)]
    ws = by_code(check_leaks(study_from_texts({SRC_A: long_, SRC_B: b, SRC_C: c})), "length_separable")
    assert {frozenset(w.sources) for w in ws} == {frozenset({SRC_A, SRC_B}), frozenset({SRC_A, SRC_C})}


def test_small_third_source_is_not_compared() -> None:
    a = [sized(20 + (i * 7) % 13) for i in range(6)]
    b = [sized(20 + (i * 5) % 13) for i in range(6)]
    c = [sized(500 + i) for i in range(4)]
    assert by_code(check_leaks(study_from_texts({SRC_A: a, SRC_B: b, SRC_C: c})), "length_separable") == []


def test_length_counts_characters_not_bytes() -> None:
    a = ["\u00e9" * 30 for _ in range(6)]  # 30 characters but 60 bytes
    b = ["x" * n for n in (10, 20, 28, 32, 40, 50)]
    # In characters A beats 3 of 6 B texts in every pair (AUC 0.5): silent.
    # In bytes A (60) would beat all of B (AUC 1.0) and fire.
    assert by_code(check_leaks(study_from_texts({SRC_A: a, SRC_B: b})), "length_separable") == []


# ---- formatting_separable ---------------------------------------------------------------------


def marked(text_marker: str, n: int, offset: int = 0) -> list[str]:
    return [text_marker.replace("{i}", str(i + offset)) + " " + plain(i + offset) for i in range(n)]


MARKUP_STYLES = {
    "hash": "# Heading {i}\nbody",
    "hash_level_three": "### Heading {i}\nbody",
    "dash": "- point {i}",
    "star": "* point {i}",
    "plus": "+ point {i}",
    "bullet": "\u2022 point {i}",  # as a list copied from a chat page arrives
    "triangular_bullet": "\u2023 point {i}",
    "white_bullet": "\u25e6 point {i}",
    "en_dash": "\u2013 point {i}",
    "indented_dash": "  - point {i}",  # a nested list
    "tab_indented_star": "\t* point {i}",
    "numbered": "1. point {i}",
    "numbered_multi_digit": "12. point {i}",
    "numbered_parenthesis": "1) point {i}",
    "indented_numbered": "   1. point {i}",
    "quote": "> quoted {i}",
    "double_star": "this is **bold {i}** text",
    "backtick": "use `code {i}` here",
    "second_line": "intro line {i}\n- point on a later line",
}


@pytest.mark.parametrize("style", sorted(MARKUP_STYLES))
def test_each_markup_form_counts(style: str) -> None:
    marker = MARKUP_STYLES[style]
    a = [marker.replace("{i}", str(i)) + "\nplain tail " + plain(i) for i in range(6)]
    b = plain_texts(6, 30)
    ws = by_code(check_leaks(study_from_texts({SRC_A: a, SRC_B: b})), "formatting_separable")
    assert len(ws) == 1, f"markup style {style} should be detected"
    assert set(ws[0].sources) == {SRC_A, SRC_B}


def test_formatting_fires_in_reverse_direction() -> None:
    a = plain_texts(6)
    b = ["- item\n" + plain(i) for i in range(6)]
    assert len(by_code(check_leaks(study_from_texts({SRC_A: a, SRC_B: b})), "formatting_separable")) == 1


@pytest.mark.parametrize(
    "text",
    [
        "-5 degrees outside",
        "2024 was a long year",
        "3.5 mg per day",
        "*emphasis* only",
        "#1 choice of most clinicians",  # a heading mark needs its space
        "#tag at the start",
        "(1) first of all",
        "5 > 3 as everyone knows",
        "\u2013and so on",  # a dash without its space opens no list
    ],
)
def test_things_that_are_not_markup(text: str) -> None:
    a = [text + " " + plain(i) for i in range(6)]
    b = plain_texts(6, 30)
    assert by_code(check_leaks(study_from_texts({SRC_A: a, SRC_B: b})), "formatting_separable") == [], text


def _fmt_study(a_marked: int, a_total: int, b_marked: int, b_total: int):
    a = ["- point\n" + plain(i) if i < a_marked else plain(i) for i in range(a_total)]
    b = ["- point\n" + plain(i + 40) if i < b_marked else plain(i + 40) for i in range(b_total)]
    return study_from_texts({SRC_A: a, SRC_B: b})


def test_formatting_thresholds_are_inclusive() -> None:
    # 4/5 = 0.8 and 1/5 = 0.2 exactly.
    assert len(by_code(check_leaks(_fmt_study(4, 5, 1, 5)), "formatting_separable")) == 1
    assert len(by_code(check_leaks(_fmt_study(8, 10, 2, 10)), "formatting_separable")) == 1


def test_formatting_just_inside_thresholds_is_silent() -> None:
    assert by_code(check_leaks(_fmt_study(3, 5, 1, 5)), "formatting_separable") == []  # 0.6 / 0.2
    assert by_code(check_leaks(_fmt_study(4, 5, 2, 5)), "formatting_separable") == []  # 0.8 / 0.4
    assert by_code(check_leaks(_fmt_study(5, 5, 5, 5)), "formatting_separable") == []  # both marked
    assert by_code(check_leaks(_fmt_study(0, 5, 0, 5)), "formatting_separable") == []  # neither marked


def test_formatting_requires_five_items_per_source() -> None:
    assert by_code(check_leaks(_fmt_study(4, 4, 0, 6)), "formatting_separable") == []
    assert by_code(check_leaks(_fmt_study(5, 5, 0, 4)), "formatting_separable") == []
    assert len(by_code(check_leaks(_fmt_study(5, 5, 0, 5)), "formatting_separable")) == 1


def test_formatting_one_warning_per_pair() -> None:
    a = ["- x\n" + plain(i) for i in range(6)]
    b = plain_texts(6, 30)
    c = plain_texts(6, 60)
    ws = by_code(check_leaks(study_from_texts({SRC_A: a, SRC_B: b, SRC_C: c})), "formatting_separable")
    assert {frozenset(w.sources) for w in ws} == {frozenset({SRC_A, SRC_B}), frozenset({SRC_A, SRC_C})}


# ---- codes are independent --------------------------------------------------------------------


def test_only_expected_codes_appear() -> None:
    a = ["As an AI I think so"] + [sized(300 + i) for i in range(5)]
    b = [sized(10 + i) for i in range(6)]
    got = {w.code for w in check_leaks(study_from_texts({SRC_A: a, SRC_B: b}))}
    assert got <= {"source_name_in_text", "self_identification", "length_separable", "formatting_separable"}
    assert {"self_identification", "length_separable"} <= got


def test_check_leaks_does_not_modify_the_study() -> None:
    study = make_study({SRC_A: 6, SRC_B: 6}, rows=make_item_rows({SRC_A: 6, SRC_B: 6}))
    before = [(i.item_id, i.text, i.context) for i in study.items]
    check_leaks(study)
    assert before == [(i.item_id, i.text, i.context) for i in study.items]


# ---- typographic variants must not hide a name ------------------------------------------------


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("Dr. O\u2019Brien", "Reviewed by Dr. O\u2019Brien."),  # the identical string
        ("Dr. O\u2019Brien", "Reviewed by Dr. O'Brien."),
        ("Dr. O'Brien", "Reviewed by Dr. O\u2018Brien."),
        ("orion-x", "Drafted by orion\u2011x today"),  # non-breaking hyphen
        ("orion-x", "Drafted by orion\u2013x today"),  # en dash
        ("M\u00fcller", "Mu\u0308ller wrote this"),  # label composed, text decomposed
        ("Mu\u0308ller", "M\u00fcller wrote this"),  # and the reverse
        ("orion", "ori\u200bon wrote this"),  # zero-width space inside the name
        ("orion", "ori\u00adon wrote this"),  # soft hyphen inside the name
        ("orion", "\uff4f\uff52\uff49\uff4f\uff4e wrote this"),  # full-width letters
    ],
)
def test_source_label_is_found_whatever_the_typography(label: str, text: str) -> None:
    study = source_study(text, label)
    ws = by_code(check_leaks(study), "source_name_in_text")
    assert len(ws) == 1, f"label {label!r} was not found in {text!r}"
    assert ws[0].item_ids == (study.items[0].item_id,)
    assert ws[0].sources == (label,), "the warning names the label as the study wrote it"


@pytest.mark.parametrize(
    "text",
    [
        "I\u2018m an AI, so I cannot say",
        "I\u02bcm an AI, so I cannot say",
        "I am GPT\u20114o, a model",  # non-breaking hyphen, as models often write it
        "Answer from GPT\u20134",
        "Chat\u00adGPT wrote this",
        "Cl\u200baude wrote this",
        "\uff23\uff48\uff41\uff54\uff27\uff30\uff34 wrote this",
    ],
)
def test_self_id_phrase_is_found_whatever_the_typography(text: str) -> None:
    study = study_from_texts({SRC_A: [text] + plain_texts(5), SRC_B: plain_texts(6, 30)})
    ws = by_code(check_leaks(study), "self_identification")
    assert len(ws) == 1, f"{text!r} was not flagged"
    assert ws[0].item_ids == (study.items[0].item_id,)


@pytest.mark.parametrize("text", ["a bard\u00adic tale", "the x\u200bclaude file"])
def test_invisible_characters_do_not_split_a_word(text: str) -> None:
    # The rater reads "bardic" and "xclaude": the name is inside a longer word.
    study = study_from_texts({SRC_A: [text] + plain_texts(5), SRC_B: plain_texts(6, 30)})
    assert by_code(check_leaks(study), "self_identification") == []


@pytest.mark.parametrize(
    "invisible",
    [
        "\u200e",  # left-to-right mark
        "\u200f",  # right-to-left mark
        "\u2061",  # function application
        "\u180e",  # Mongolian vowel separator
        "\U000e0041",  # a tag character
        "\ufe0f",  # variation selector 16; not a format character
        "\ufe00",  # variation selector 1
        "\u034f",  # combining grapheme joiner; not a format character
    ],
)
def test_no_invisible_character_hides_a_name(invisible: str) -> None:
    study = study_from_texts(
        {
            "orion": [f"ori{invisible}on wrote this", f"Chat{invisible}GPT wrote this"] + plain_texts(4),
            SRC_B: plain_texts(6, 30),
        }
    )
    ws = check_leaks(study)
    first, second = study.items[0].item_id, study.items[1].item_id
    assert [w.item_ids for w in by_code(ws, "source_name_in_text")] == [(first,)], f"U+{ord(invisible):04X}"
    assert [w.item_ids for w in by_code(ws, "self_identification")] == [(second,)], f"U+{ord(invisible):04X}"


@pytest.mark.parametrize("apostrophe", ["\u00b4", "`", "\u2032", "\u201b"])
def test_marks_typed_in_place_of_an_apostrophe_do_not_hide_a_name(apostrophe: str) -> None:
    study = study_from_texts(
        {
            "Dr. O'Brien": [f"Reviewed by Dr. O{apostrophe}Brien.", f"I{apostrophe}m an AI, so I cannot say"]
            + plain_texts(4),
            SRC_B: plain_texts(6, 30),
        }
    )
    ws = check_leaks(study)
    first, second = study.items[0].item_id, study.items[1].item_id
    assert [w.item_ids for w in by_code(ws, "source_name_in_text")] == [(first,)], f"U+{ord(apostrophe):04X}"
    assert [w.item_ids for w in by_code(ws, "self_identification")] == [(second,)], f"U+{ord(apostrophe):04X}"


def test_a_label_written_with_a_look_alike_apostrophe_is_found_with_a_plain_one() -> None:
    study = source_study("Reviewed by Dr. O'Brien.", "Dr. O\u00b4Brien")
    ws = by_code(check_leaks(study), "source_name_in_text")
    assert [w.sources for w in ws] == [("Dr. O\u00b4Brien",)]


def test_folding_a_backtick_for_matching_leaves_the_code_mark_rule_alone() -> None:
    a = [f"use `code {i}` here " + plain(i) for i in range(6)]
    ws = check_leaks(study_from_texts({SRC_A: a, SRC_B: plain_texts(6, 30)}))
    assert [w.code for w in ws] == ["formatting_separable"]


@pytest.mark.parametrize("label", ["\u200b   \u200b", "   \u200e", " ab ", "\u200bab\u200b  "])
def test_label_that_folds_to_spaces_is_not_searched_for(label: str) -> None:
    """Such a label has no word in it; searched for, its empty pattern would flag every item."""
    study = study_from_texts({label: plain_texts(6), SRC_B: plain_texts(6, 30)})
    assert by_code(check_leaks(study), "source_name_in_text") == []
    coverage = leak_coverage(study)
    assert coverage.short_labels == (label,)
    assert coverage.n_labels_searched == 1


def test_spaces_inside_a_label_still_count_towards_its_length() -> None:
    study = source_study("Written by a b today", " a b ")
    assert leak_coverage(study).short_labels == ()
    assert len(by_code(check_leaks(study), "source_name_in_text")) == 1


def test_label_of_invisible_characters_is_too_short_to_search_for() -> None:
    label = "\u200b\u200b\u200b\u200b"
    study = study_from_texts({label: plain_texts(6), SRC_B: plain_texts(6, 30)})
    assert by_code(check_leaks(study), "source_name_in_text") == []
    coverage = leak_coverage(study)
    assert coverage.short_labels == (label,)
    assert coverage.n_labels_searched == 1


def test_each_text_and_context_is_folded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Folding a long text is the costly step, so it must not be repeated per label or phrase."""
    study = study_from_texts(
        {"orion": plain_texts(6), "nebulon": plain_texts(6, 30)},
        contexts_by_source={"orion": [f"Synthetic context {i}" for i in range(6)]},
    )
    folded: list[str] = []
    original = leaks_mod._fold

    def counting_fold(text: str) -> str:
        folded.append(text)
        return original(text)

    monkeypatch.setattr(leaks_mod, "_fold", counting_fold)
    check_leaks(study)
    for item in study.items:
        assert folded.count(item.text) == 1
        if item.context is not None:
            assert folded.count(item.context) == 1


# ---- context is compared too ------------------------------------------------------------------


def _same_texts() -> dict[str, list[str]]:
    return {SRC_A: plain_texts(6), SRC_B: plain_texts(6)}


def test_context_length_that_differs_by_source_is_reported() -> None:
    contexts = {SRC_A: ["q" * (500 + i) for i in range(6)], SRC_B: ["q" * (20 + i) for i in range(6)]}
    ws = check_leaks(study_from_texts(_same_texts(), contexts_by_source=contexts))
    assert [w.code for w in ws] == ["length_separable"]
    assert set(ws[0].sources) == {SRC_A, SRC_B}
    assert "context" in ws[0].message


def test_context_present_for_one_source_only_is_reported() -> None:
    contexts = {SRC_A: [f"Synthetic question {i}?" for i in range(6)]}
    ws = check_leaks(study_from_texts(_same_texts(), contexts_by_source=contexts))
    assert [w.code for w in ws] == ["length_separable"]
    assert "context" in ws[0].message


def test_context_markup_that_differs_by_source_is_reported() -> None:
    # Lengths interleave (14, 18, 22, ... against 12, 16, 20, ...), so only the markup differs.
    contexts = {
        SRC_A: ["**Question:** " + "x" * (4 * i) for i in range(6)],
        SRC_B: ["Question: " + "x" * (4 * i + 2) for i in range(6)],
    }
    ws = check_leaks(study_from_texts(_same_texts(), contexts_by_source=contexts))
    assert [w.code for w in ws] == ["formatting_separable"]
    assert "context" in ws[0].message


def test_shared_contexts_raise_nothing_and_do_not_mask_the_texts() -> None:
    shared = [f"Synthetic question {i}? " + "background " * 80 for i in range(6)]
    contexts = {SRC_A: list(shared), SRC_B: list(shared)}
    assert check_leaks(study_from_texts(_same_texts(), contexts_by_source=contexts)) == []
    # A long shared context must not hide a difference between the texts themselves.
    texts = {SRC_A: [sized(200 + i) for i in range(6)], SRC_B: [sized(20 + i) for i in range(6)]}
    ws = check_leaks(study_from_texts(texts, contexts_by_source=contexts))
    assert [w.code for w in ws] == ["length_separable"]
    assert "text" in ws[0].message and "context" not in ws[0].message


# ---- percentages in the messages --------------------------------------------------------------


def test_length_message_does_not_round_up_to_100_percent() -> None:
    # 15 x 15 texts, exactly one of the 225 comparisons goes the other way: 99.56%.
    a = [sized(200 + i) for i in range(14)] + [sized(100)]
    b = [sized(10 + i) for i in range(14)] + [sized(150)]
    ws = by_code(check_leaks(study_from_texts({SRC_A: a, SRC_B: b})), "length_separable")
    assert len(ws) == 1
    assert "100%" not in ws[0].message
    assert "more than 99%" in ws[0].message


def test_length_message_says_100_percent_only_when_exact() -> None:
    a = [sized(200 + i) for i in range(6)]
    b = [sized(10 + i) for i in range(6)]
    ws = by_code(check_leaks(study_from_texts({SRC_A: a, SRC_B: b})), "length_separable")
    assert "in 100% of comparisons" in ws[0].message


def test_formatting_message_does_not_round_to_100_or_0_percent() -> None:
    ws = by_code(check_leaks(_fmt_study(249, 250, 1, 250)), "formatting_separable")
    assert len(ws) == 1
    assert "100%" not in ws[0].message and " 0%" not in ws[0].message
    assert "more than 99%" in ws[0].message and "less than 1%" in ws[0].message
    exact = by_code(check_leaks(_fmt_study(5, 5, 0, 5)), "formatting_separable")
    assert "100%" in exact[0].message and " 0%" in exact[0].message


# ---- leak_coverage ----------------------------------------------------------------------------


def test_coverage_of_a_study_every_check_could_look_at() -> None:
    study = study_from_texts({SRC_A: plain_texts(6), SRC_B: plain_texts(6, 30)})
    coverage = leak_coverage(study)
    assert coverage == LeakCoverage(
        n_labels=2,
        n_labels_searched=2,
        short_labels=(),
        n_pairs=1,
        n_pairs_compared=1,
        small_sources=(),
        min_label_chars=leaks_mod.MIN_SOURCE_LABEL_CHARS,
        min_items_per_source=leaks_mod.MIN_ITEMS_PER_SOURCE,
    )
    assert coverage_notes(coverage) == []


def test_coverage_says_when_no_pair_was_compared() -> None:
    # The texts could not differ more, yet nothing is compared: 4 items per source.
    long_ = ["# Heading\n- point\n" + sized(600 + i) for i in range(4)]
    short = [f"No {i}." for i in range(4)]
    study = study_from_texts({SRC_A: long_, SRC_B: short})
    assert check_leaks(study) == []
    coverage = leak_coverage(study)
    assert (coverage.n_pairs, coverage.n_pairs_compared) == (1, 0)
    assert coverage.small_sources == (SRC_A, SRC_B)
    notes = coverage_notes(coverage)
    assert len(notes) == 1
    assert "not compared" in notes[0] and "1 of 1 pair" in notes[0]
    assert SRC_A in notes[0] and SRC_B in notes[0]
    assert str(leaks_mod.MIN_ITEMS_PER_SOURCE) in notes[0]


def test_coverage_counts_the_pairs_a_small_source_takes_out() -> None:
    study = study_from_texts({SRC_A: plain_texts(4), SRC_B: plain_texts(6, 30), SRC_C: plain_texts(30, 60)})
    coverage = leak_coverage(study)
    assert (coverage.n_pairs, coverage.n_pairs_compared) == (3, 1)
    assert coverage.small_sources == (SRC_A,)
    assert "2 of 3 pairs" in coverage_notes(coverage)[0]


def test_coverage_names_labels_too_short_to_search_for() -> None:
    texts_a = ["written by o3 and reviewed by an MD"] + plain_texts(5)
    study = study_from_texts({"o3": texts_a, "MD": plain_texts(6, 30), SRC_C: plain_texts(6, 60)})
    assert by_code(check_leaks(study), "source_name_in_text") == []
    coverage = leak_coverage(study)
    assert (coverage.n_labels, coverage.n_labels_searched) == (3, 1)
    assert coverage.short_labels == ("MD", "o3")
    assert coverage.n_pairs_compared == coverage.n_pairs == 3
    notes = coverage_notes(coverage)
    assert len(notes) == 1
    assert "not searched for" in notes[0] and "2 of 3 source labels" in notes[0]
    assert "'o3'" in notes[0] and "'MD'" in notes[0]


def test_coverage_of_a_single_source_has_no_pair_to_miss() -> None:
    coverage = leak_coverage(study_from_texts({SRC_A: plain_texts(3)}))
    assert (coverage.n_pairs, coverage.n_pairs_compared) == (0, 0)
    assert coverage.small_sources == (SRC_A,)
    assert coverage_notes(coverage) == []


def test_coverage_is_frozen_and_can_be_written_as_json() -> None:
    coverage = leak_coverage(study_from_texts({"o3": plain_texts(4), SRC_B: plain_texts(6, 30)}))
    with pytest.raises(dataclasses.FrozenInstanceError):
        coverage.n_pairs = 5  # type: ignore[misc]
    stored = json.loads(json.dumps(dataclasses.asdict(coverage)))
    assert stored == {
        "n_labels": 2,
        "n_labels_searched": 1,
        "short_labels": ["o3"],
        "n_pairs": 1,
        "n_pairs_compared": 0,
        "small_sources": ["o3"],
        "min_label_chars": 3,
        "min_items_per_source": 5,
    }
