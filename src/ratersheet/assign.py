"""Decide who rates what, in which order, and under which codes.

The design is a pure function of the study and of a secret drawn when the
study is built, so the key (which records the seed and the secret) is enough
to regenerate it on any machine, and nothing short of the key is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations

from .config import Item, Study
from .rng import DeterministicRNG

RATING_ID_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no I, L, O, 0, 1: easy to read aloud


@dataclass(frozen=True)
class Assignment:
    """One rater scoring one item."""

    rating_id: str  # unique per (rater, item); format XXXX-XXXX
    rater_id: str
    item_id: str
    position: int  # 1-based position of this item in that rater's sheet


@dataclass(frozen=True)
class Design:
    """Every assignment of a study, and the opaque code of every item."""

    assignments: tuple[Assignment, ...]  # sorted by rater (in study order), then position
    item_codes: dict[str, str]  # item_id -> "I-XXXXXX"


def make_design(study: Study, *, secret: str) -> Design:
    """Assign items to raters, order each rater's items and draw the codes.

    ``secret`` is mixed into every random stream. Raters hold their rating ids
    and their row order, and a blind analyst holds the item codes; if these
    depended on the seed alone, anyone could test guesses of a short seed
    against them and then replay the shuffle to recover the order of the items
    file. ``build`` passes the random nonce it stores in the key, so the same
    study and the same secret always give the same design. An empty or
    guessable secret brings the weakness back.

    Three independent random streams are used (who rates what, the order per
    rater, the codes) so that changing a presentation option such as
    ``group_by_case`` does not change who rates what.
    """
    raters_of_item = _assign_raters(study, secret)
    ids = DeterministicRNG(study.seed, f"{secret}|ids")

    used_codes: set[str] = set()
    item_codes = {
        item.item_id: _draw_unique(lambda: "I-" + ids.code(RATING_ID_ALPHABET, 6), used_codes)
        for item in study.items
    }

    used_rating_ids: set[str] = set()
    assignments: list[Assignment] = []
    for rater_id in study.raters:
        items = [item for item in study.items if rater_id in raters_of_item[item.item_id]]
        ordered = _order_for_rater(study, secret, rater_id, items)
        for position, item in enumerate(ordered, start=1):
            rating_id = _draw_unique(lambda: _rating_id(ids), used_rating_ids)
            assignments.append(Assignment(rating_id, rater_id, item.item_id, position))
    return Design(assignments=tuple(assignments), item_codes=item_codes)


def _rating_id(rng: DeterministicRNG) -> str:
    return rng.code(RATING_ID_ALPHABET, 4) + "-" + rng.code(RATING_ID_ALPHABET, 4)


def _draw_unique(draw: Callable[[], str], used: set[str]) -> str:
    """Draw codes until one has not been used before, and remember it."""
    while True:
        code = draw()
        if code not in used:
            used.add(code)
            return code


def _assign_raters(study: Study, secret: str) -> dict[str, tuple[str, ...]]:
    """Return, for every item id, the raters who score that item.

    Items are handled one source at a time. Before a source is dealt out, each
    rater gets a quota for it: an equal share, with the items that do not
    divide evenly going to the raters who have the fewest items so far (see
    ``_raters_with_an_extra_item``). Within the quotas, each item goes to the
    raters who have the fewest items in common with each other, which spreads
    co-rating over the pairs of raters as evenly as the quotas allow (a best
    effort, not a guarantee).

    Why the load guarantees hold whatever the picks are. Per source, the
    quotas are ``base`` or ``base + 1`` and every rater ends the source with
    exactly their quota, so no two raters differ by more than one item of any
    source. Overall, raters' totals differ by at most one before a source and
    the extra items go to the lowest totals first (only raters with equal
    totals are told apart by anything else), so they still differ by at most
    one after it. Every quota is used up because, while a source is dealt,
    the quotas left always add up to ``raters_per_item`` times the items left
    and none exceeds the items left: a rater who owes as many items as are
    left takes each of them (there can be at most ``raters_per_item`` such
    raters), and the other seats can always be filled by distinct raters who
    still owe an item.
    """
    rng = DeterministicRNG(study.seed, f"{secret}|assign")
    total_load = dict.fromkeys(study.raters, 0)
    shared: dict[frozenset[str], int] = {}
    raters_of_item: dict[str, tuple[str, ...]] = {}
    for source in rng.shuffled(study.sources):
        items = rng.shuffled([item for item in study.items if item.source == source])
        base, extra = divmod(len(items) * study.raters_per_item, len(study.raters))
        with_extra = _raters_with_an_extra_item(rng, study, extra, total_load, shared)
        quota = {r: base + (1 if r in with_extra else 0) for r in study.raters}
        for n_done, item in enumerate(items):
            chosen = _choose_raters(rng, study, quota, len(items) - n_done, shared)
            for rater_id in chosen:
                quota[rater_id] -= 1
                total_load[rater_id] += 1
            for pair in combinations(chosen, 2):
                shared[frozenset(pair)] = shared.get(frozenset(pair), 0) + 1
            raters_of_item[item.item_id] = tuple(chosen)
    return raters_of_item


def _raters_with_an_extra_item(
    rng: DeterministicRNG,
    study: Study,
    n_extra: int,
    total_load: dict[str, int],
    shared: dict[frozenset[str], int],
) -> list[str]:
    """Choose the raters who get one item more than the equal share of a source.

    The fewest items so far decides first; that alone keeps the raters' totals
    within one item of each other. Raters with equal totals are told apart by
    the fewest items in common with the raters already chosen here. This is
    where co-rating is spread when a source is too small to leave
    ``_choose_raters`` a choice: the single item of a one-item source goes to
    exactly these raters, so if chance decided among equal totals, a study
    with one text per source would get pair counts no better than random.

    The raters are shuffled before the picks; ``min`` returns the first of
    several equal candidates, so the shuffle is the random tie-break.
    """
    pool = rng.shuffled(study.raters)
    chosen: list[str] = []

    def pick_key(rater_id: str) -> tuple[int, int]:
        in_common = sum(shared.get(frozenset((rater_id, other)), 0) for other in chosen)
        return (total_load[rater_id], in_common)

    while len(chosen) < n_extra:
        best = min(pool, key=pick_key)
        chosen.append(best)
        pool.remove(best)
    return chosen


def _choose_raters(
    rng: DeterministicRNG,
    study: Study,
    quota: dict[str, int],
    items_left: int,
    shared: dict[frozenset[str], int],
) -> list[str]:
    """Choose the raters for one item of the source being dealt out.

    ``quota`` is how many items of the source each rater still owes and
    ``items_left`` counts this item. A rater who owes as many items as are
    left must take this one. The other seats are filled one at a time by the
    rater with the fewest items in common with those already chosen, then the
    most items still owed. The candidates are shuffled before the minimum is
    taken; ``min`` returns the first of several equal candidates, so the
    shuffle is the random tie-break.
    """
    chosen = [rater_id for rater_id in study.raters if quota[rater_id] == items_left]

    def pick_key(rater_id: str) -> tuple[int, int]:
        in_common = sum(shared.get(frozenset((rater_id, other)), 0) for other in chosen)
        return (in_common, -quota[rater_id])

    while len(chosen) < study.raters_per_item:
        candidates = rng.shuffled([r for r in study.raters if r not in chosen and quota[r] > 0])
        chosen.append(min(candidates, key=pick_key))
    return chosen


def _order_for_rater(
    study: Study, secret: str, rater_id: str, items: list[Item]
) -> list[Item]:
    """Return one rater's items in the order of that rater's sheet."""
    rng = DeterministicRNG(study.seed, f"{secret}|order|{rater_id}")
    if not study.group_by_case:
        return rng.shuffled(items)
    cases = rng.shuffled(dict.fromkeys(item.case_id for item in items))
    ordered: list[Item] = []
    for case_id in cases:
        ordered.extend(rng.shuffled([item for item in items if item.case_id == case_id]))
    return ordered


def design_summary(study: Study, design: Design) -> dict:
    """Count what the design gives each rater and each pair of raters."""
    source_of = {item.item_id: item.source for item in study.items}
    per_rater = dict.fromkeys(study.raters, 0)
    per_rater_source = {rater: dict.fromkeys(study.sources, 0) for rater in study.raters}
    raters_of_item: dict[str, set[str]] = {item.item_id: set() for item in study.items}
    for assignment in design.assignments:
        per_rater[assignment.rater_id] += 1
        per_rater_source[assignment.rater_id][source_of[assignment.item_id]] += 1
        raters_of_item[assignment.item_id].add(assignment.rater_id)
    pair_counts = {
        f"{first}|{second}": sum(
            1 for raters in raters_of_item.values() if first in raters and second in raters
        )
        for first, second in combinations(study.raters, 2)
    }
    return {
        "n_items": len(study.items),
        "n_raters": len(study.raters),
        "raters_per_item": study.raters_per_item,
        "n_assignments": len(design.assignments),
        "per_rater": per_rater,
        "per_rater_source": per_rater_source,
        "pair_counts": pair_counts,
    }
