"""Look for content that could tell a rater which source wrote a text.

Only what a rater sees is checked: each item's text and context. The checks
report and never rewrite; deciding what to do about a warning is the
researcher's job. They are heuristics: no warning does not mean no cue.

Some checks cannot run on every study: a very short source label is not
searched for, and a source with few items is not compared with the others.
``leak_coverage`` says what was and was not looked at, so that "no warning"
is never mistaken for "checked and clean".
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations, product
from math import comb

from .config import Item, Study

SELF_ID_PHRASES = (
    "as an ai",
    "i am an ai",
    "i'm an ai",
    "ai language model",
    "language model",
    "chatgpt",
    "openai",
    "gpt-3",
    "gpt-4",
    "gpt-5",
    "claude",
    "anthropic",
    "gemini",
    "bard",
    "copilot",
    "llama",
    "mistral",
    "deepseek",
    "grok",
)
LENGTH_AUC_THRESHOLD = 0.9
MIN_ITEMS_PER_SOURCE = 5
MIN_SOURCE_LABEL_CHARS = 3
MARKUP_SHARE_HIGH = 0.8
MARKUP_SHARE_LOW = 0.2

# A line that opens, after any indentation, with a heading mark, a list marker
# ("-", "*", "+", a bullet character, an en dash, "1." or "1)") or a quote
# mark. Each marker needs its space: "#1 choice" and "-5 degrees" are prose.
_MARKUP_LINE_RE = re.compile(r"\s*(?:#{1,6} |[-*+\u2022\u2023\u25e6\u2013] |[0-9]+[.)] |> )")

# Invisible characters that are not format characters (category Cf) and so
# have to be named: the variation selectors and the combining grapheme joiner.
_INVISIBLE_NOT_CF = frozenset(map(chr, (0x034F, *range(0xFE00, 0xFE10))))


class _FoldTable(dict[int, str | int | None]):
    """A table for ``str.translate`` that also drops what a reader does not see.

    The format characters (zero-width spaces and joiners, direction marks,
    tag characters and more) are too many to list and grow with every Unicode
    version, so a character that is not in the table is classified the first
    time it is met, and the answer is kept.
    """

    def __missing__(self, code: int) -> int | None:
        char = chr(code)
        invisible = unicodedata.category(char) == "Cf" or char in _INVISIBLE_NOT_CF
        folded = None if invisible else code
        self[code] = folded
        return folded


# Characters that look alike to a reader are made alike before matching.
_FOLD_TABLE = _FoldTable(
    {
        # typographic apostrophes, and the marks typed in place of one: acute
        # accent, grave accent (backtick), prime
        **dict.fromkeys(map(ord, "\u2018\u2019\u201b\u02bc\u00b4\u0060\u2032"), "'"),
        # hyphens and dashes
        **dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-"),
    }
)

_Visible = list[tuple[Item, tuple[str, ...]]]  # each item with its folded context and text
_PartOf = Callable[[Study, str], list[str]]  # the cells of one column of the sheet for one source


@dataclass(frozen=True)
class LeakWarning:
    """One reason a rater might be able to guess a source."""

    code: str
    message: str  # one plain sentence a researcher can act on
    item_ids: tuple[str, ...]  # items involved (empty for whole-source warnings)
    sources: tuple[str, ...]  # sources involved (may be empty)


@dataclass(frozen=True)
class LeakCoverage:
    """What the leak checks could and could not look at in one study.

    The thresholds are recorded with the counts because a later version of
    the tool may use other thresholds than the one that made the check.
    """

    n_labels: int  # source labels in the study
    n_labels_searched: int  # of those, how many were searched for in texts and contexts
    short_labels: tuple[str, ...]  # labels too short to be searched for
    n_pairs: int  # pairs of sources in the study
    n_pairs_compared: int  # of those, how many were compared for length and markup
    small_sources: tuple[str, ...]  # sources with too few items to be compared
    min_label_chars: int
    min_items_per_source: int


def check_leaks(study: Study) -> list[LeakWarning]:
    """Return every leak warning for the study, in a fixed order.

    An empty list means that the checks that ran found nothing; which checks
    ran is reported by ``leak_coverage``.
    """
    # Folded once per item: every label and phrase is searched for in the same
    # folded copy, and folding a long text is the costly part of a search.
    visible = [(item, _visible_parts(item)) for item in study.items]
    warnings = _source_names_in_text(study, visible)
    warnings.extend(_self_identification(visible))
    pairs = _comparable_source_pairs(study)
    warnings.extend(_length_separable(study, pairs))
    warnings.extend(_formatting_separable(study, pairs))
    return warnings


def leak_coverage(study: Study) -> LeakCoverage:
    """Say what ``check_leaks`` looked at for this study, and what it skipped."""
    labels = study.sources
    short = tuple(label for label in labels if not _is_searchable(label))
    counts = _items_per_source(study)
    small = tuple(label for label in labels if counts[label] < MIN_ITEMS_PER_SOURCE)
    return LeakCoverage(
        n_labels=len(labels),
        n_labels_searched=len(labels) - len(short),
        short_labels=short,
        n_pairs=comb(len(labels), 2),
        n_pairs_compared=comb(len(labels) - len(small), 2),
        small_sources=small,
        min_label_chars=MIN_SOURCE_LABEL_CHARS,
        min_items_per_source=MIN_ITEMS_PER_SOURCE,
    )


def coverage_notes(coverage: LeakCoverage) -> list[str]:
    """Return one plain sentence per check that did not run in full.

    Meant to be printed beside the warnings; empty when nothing was skipped.
    """
    notes = []
    if coverage.short_labels:
        skipped = len(coverage.short_labels)
        listed = ", ".join(repr(label) for label in coverage.short_labels)
        notes.append(
            f"{skipped} of {_count(coverage.n_labels, 'source label')} "
            f"{'was' if skipped == 1 else 'were'} not searched for in the texts and contexts "
            f"({listed}): a label shorter than {coverage.min_label_chars} characters is not "
            "searched for."
        )
    if coverage.n_pairs_compared < coverage.n_pairs:
        listed = ", ".join(repr(label) for label in coverage.small_sources)
        notes.append(
            f"Length and markup were not compared for "
            f"{coverage.n_pairs - coverage.n_pairs_compared} of "
            f"{_count(coverage.n_pairs, 'pair')} of sources: a source with fewer than "
            f"{coverage.min_items_per_source} items is not compared ({listed})."
        )
    return notes


def _fold(text: str) -> str:
    """Return ``text`` as a reader sees it, for matching only.

    Without this a typographic apostrophe, a non-breaking hyphen, a decomposed
    accent, a full-width letter or an invisible character (a zero-width space,
    a direction mark, a variation selector) hides a name from the search
    although the rater reads the name. The table is applied on both sides of
    the normalisation: an invisible character must not keep an accent from
    joining its letter, an acute accent typed as an apostrophe must be folded
    before normalising turns it into a space and a combining mark, and
    normalising can itself produce a hyphen that still has to be folded.
    """
    return unicodedata.normalize("NFKC", text.translate(_FOLD_TABLE)).translate(_FOLD_TABLE)


def _visible_parts(item: Item) -> tuple[str, ...]:
    """What a rater reads for this item, folded for matching."""
    parts = (item.text,) if item.context is None else (item.context, item.text)
    return tuple(_fold(part) for part in parts)


def _mentions(pattern: re.Pattern[str], parts: tuple[str, ...]) -> bool:
    """True if the pattern occurs in the folded parts of one item."""
    return any(pattern.search(part) for part in parts)


def _is_searchable(label: str) -> bool:
    """True if a source label is long enough, as a reader sees it, to be searched for.

    White space at the ends is not counted: a label that folds to spaces has
    no word to search for, and its pattern would be empty and match every item.
    """
    return len(_fold(label).strip()) >= MIN_SOURCE_LABEL_CHARS


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Match ``phrase`` ignoring case, but not inside a longer word.

    The phrase is folded like the texts it is searched in, so a label written
    with a typographic apostrophe is found whichever apostrophe the text uses.
    ``[^\\W_]`` is "a letter or a digit" in any script. Spaces in the phrase
    match any run of white space, so a line break inside it does not hide it.
    """
    phrase = _fold(phrase)
    body = r"\s+".join(re.escape(word) for word in phrase.split())
    # A phrase that ends in a digit is a model family ("gpt-4"): variants such
    # as "GPT-4o" or "GPT-4.1" continue straight on, so only the start is anchored.
    end = "" if phrase[-1].isdigit() else r"(?![^\W_])"
    return re.compile(rf"(?<![^\W_]){body}{end}", re.IGNORECASE)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _percent(share: float) -> str:
    """A whole percentage that never rounds an inexact share to 100% or 0%."""
    text = f"{share:.0%}"
    if text == "100%" and share < 1:
        return "more than 99%"
    if text == "0%" and share > 0:
        return "less than 1%"
    return text


def _source_names_in_text(study: Study, visible: _Visible) -> list[LeakWarning]:
    warnings = []
    for label in study.sources:
        if not _is_searchable(label):
            continue
        pattern = _phrase_pattern(label)
        hits = tuple(item.item_id for item, parts in visible if _mentions(pattern, parts))
        if hits:
            warnings.append(
                LeakWarning(
                    code="source_name_in_text",
                    message=(
                        f"The source label {label!r} appears in the text or context of "
                        f"{_count(len(hits), 'item')}; a rater who reads it may guess the source."
                    ),
                    item_ids=hits,
                    sources=(label,),
                )
            )
    return warnings


def _self_identification(visible: _Visible) -> list[LeakWarning]:
    patterns = [(phrase, _phrase_pattern(phrase)) for phrase in SELF_ID_PHRASES]
    hits: list[Item] = []
    found_phrases: list[str] = []
    for item, parts in visible:
        found = [phrase for phrase, pattern in patterns if _mentions(pattern, parts)]
        if found:
            hits.append(item)
            found_phrases.extend(phrase for phrase in found if phrase not in found_phrases)
    if not hits:
        return []
    quoted = ", ".join(f'"{phrase}"' for phrase in found_phrases)
    verb = "contains" if len(hits) == 1 else "contain"
    return [
        LeakWarning(
            code="self_identification",
            message=(
                f"{_count(len(hits), 'item')} {verb} a phrase by which a model names itself "
                f"or its vendor ({quoted}); a rater who reads it may guess the source."
            ),
            item_ids=tuple(item.item_id for item in hits),
            sources=tuple(sorted({item.source for item in hits})),
        )
    ]


def _items_per_source(study: Study) -> dict[str, int]:
    counts = {source: 0 for source in study.sources}
    for item in study.items:
        counts[item.source] += 1
    return counts


def _comparable_source_pairs(study: Study) -> list[tuple[str, str]]:
    """Pairs of sources that each have enough items for a comparison to mean something."""
    counts = _items_per_source(study)
    large_enough = [source for source in study.sources if counts[source] >= MIN_ITEMS_PER_SOURCE]
    return list(combinations(large_enough, 2))


def _texts_of(study: Study, source: str) -> list[str]:
    return [item.text for item in study.items if item.source == source]


def _contexts_of(study: Study, source: str) -> list[str]:
    """The context cells of a source as the sheet shows them: empty when there is none."""
    return [item.context or "" for item in study.items if item.source == source]


def _compared_parts(study: Study) -> list[tuple[str, _PartOf]]:
    """The columns of the sheet that are compared between sources, each with its name.

    Text and context are compared separately. Joined together, a long question
    shared by every source would mask a real difference between the texts. A
    study without any context has no context column to compare.
    """
    parts: list[tuple[str, _PartOf]] = [("text", _texts_of)]
    if any(item.context is not None for item in study.items):
        parts.append(("context", _contexts_of))
    return parts


def _probability_longer(first: list[str], second: list[str]) -> float:
    """Probability that a random text of ``first`` is longer than one of ``second``.

    Ties count as one half. 0.5 means length says nothing about the source;
    values near 0 or 1 mean length alone nearly identifies it.
    """
    score = 0.0
    for a in first:
        for b in second:
            if len(a) > len(b):
                score += 1.0
            elif len(a) == len(b):
                score += 0.5
    return score / (len(first) * len(second))


def _length_separable(study: Study, pairs: list[tuple[str, str]]) -> list[LeakWarning]:
    warnings = []
    for (first, second), (noun, part_of) in product(pairs, _compared_parts(study)):
        probability = _probability_longer(part_of(study, first), part_of(study, second))
        # The probability seen from the longer source's side: 0.1 one way round
        # is 0.9 the other way round.
        share = max(probability, 1 - probability)
        if share < LENGTH_AUC_THRESHOLD:
            continue
        longer, shorter = (first, second) if probability >= 0.5 else (second, first)
        warnings.append(
            LeakWarning(
                code="length_separable",
                message=(
                    f"A {noun} from {longer!r} is longer than a {noun} from {shorter!r} in "
                    f"{_percent(share)} of comparisons (ties count as half); a rater may tell "
                    f"these two sources apart by the length of the {noun} alone."
                ),
                item_ids=(),
                sources=(first, second),
            )
        )
    return warnings


def _has_markup(text: str) -> bool:
    """True if the text uses headings, list or quote markers, bold markers or code marks."""
    if "**" in text or "`" in text:
        return True
    return any(_MARKUP_LINE_RE.match(line) for line in text.splitlines())


def _markup_share(texts: list[str]) -> float:
    return sum(1 for text in texts if _has_markup(text)) / len(texts)


def _formatting_separable(study: Study, pairs: list[tuple[str, str]]) -> list[LeakWarning]:
    warnings = []
    for (first, second), (noun, part_of) in product(pairs, _compared_parts(study)):
        share_first = _markup_share(part_of(study, first))
        share_second = _markup_share(part_of(study, second))
        high, low = max(share_first, share_second), min(share_first, share_second)
        if high < MARKUP_SHARE_HIGH or low > MARKUP_SHARE_LOW:
            continue
        with_markup, without = (first, second) if share_first >= share_second else (second, first)
        warnings.append(
            LeakWarning(
                code="formatting_separable",
                message=(
                    f"List, heading or emphasis markup is used in {_percent(high)} of the "
                    f"{noun}s from {with_markup!r} but in {_percent(low)} of the {noun}s from "
                    f"{without!r}; a rater may tell these two sources apart by the formatting "
                    f"of the {noun} alone."
                ),
                item_ids=(),
                sources=(first, second),
            )
        )
    return warnings
