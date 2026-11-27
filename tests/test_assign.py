"""make_design: the guarantees over a sweep, the build secret, pair balance, ids, design_summary."""

from __future__ import annotations

import itertools
import random
import re
from collections import Counter, defaultdict

import pytest

from ratersheet import DeterministicRNG, Design, Study, design_summary
from ratersheet import make_design as make_design_with_secret
from ratersheet.assign import RATING_ID_ALPHABET
from conftest import NONCE, SRC_A, SRC_B, SRC_C, make_item_rows, make_study

ALPHA = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
RATING_ID_RE = re.compile(rf"^[{ALPHA}]{{4}}-[{ALPHA}]{{4}}$")
ITEM_CODE_RE = re.compile(rf"^I-[{ALPHA}]{{6}}$")


def layout(kind: str, n: int) -> dict[str, int]:
    if kind == "single":
        return {SRC_A: n}
    if kind == "balanced":
        base, extra = divmod(n, 3)
        sizes = [base + (1 if i < extra else 0) for i in range(3)]
        return {s: k for s, k in zip((SRC_A, SRC_B, SRC_C), sizes) if k > 0}
    if kind == "skewed":
        a, b = (n * 6) // 10, (n * 3) // 10
        c = n - a - b
        return {s: k for s, k in zip((SRC_A, SRC_B, SRC_C), (a, b, c)) if k > 0}
    if kind == "lopsided":
        return {SRC_A: n - 1, SRC_B: 1}
    raise ValueError(kind)


LAYOUTS = ["single", "balanced", "skewed", "lopsided"]
ITEM_COUNTS = [2, 3, 7, 13, 31]
SEEDS = [11, 4242]
OTHER_NONCE = "fedcba9876543210" * 4


def make_design(study: Study, secret: str = NONCE) -> Design:
    """The design a build with the fixed test nonce would make."""
    return make_design_with_secret(study, secret=secret)


def per_rater_items(design: Design) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for a in design.assignments:
        out[a.rater_id].append(a.item_id)
    return out


def check_design(study: Study, design: Design, label: str) -> None:
    rpi = study.raters_per_item
    n_items = len(study.items)
    source_of = {i.item_id: i.source for i in study.items}
    case_of = {i.item_id: i.case_id for i in study.items}

    # exact size and no (rater, item) twice
    assert len(design.assignments) == n_items * rpi, f"{label}: wrong number of assignments"
    pairs = [(a.rater_id, a.item_id) for a in design.assignments]
    assert len(set(pairs)) == len(pairs), f"{label}: a rater got the same item twice"
    assert {a.rater_id for a in design.assignments} <= set(study.raters), f"{label}: unknown rater"

    # 1. every item to exactly raters_per_item distinct raters
    raters_of: dict[str, set[str]] = defaultdict(set)
    for a in design.assignments:
        raters_of[a.item_id].add(a.rater_id)
    assert set(raters_of) == set(source_of), f"{label}: some item is unassigned"
    for item_id, rs in raters_of.items():
        assert len(rs) == rpi, f"{label}: item {item_id} has {len(rs)} raters, wanted {rpi}"

    # 2. items per rater differ by at most one
    counts = {r: 0 for r in study.raters}
    for a in design.assignments:
        counts[a.rater_id] += 1
    assert max(counts.values()) - min(counts.values()) <= 1, f"{label}: per-rater counts {counts}"

    # 3. per source, items per rater differ by at most one
    for src in study.sources:
        per = {r: 0 for r in study.raters}
        for a in design.assignments:
            if source_of[a.item_id] == src:
                per[a.rater_id] += 1
        assert max(per.values()) - min(per.values()) <= 1, f"{label}: source {src} per-rater counts {per}"

    # 4. positions are 1..n; ordering of the tuple; case adjacency
    order = {r: i for i, r in enumerate(study.raters)}
    keys = [(order[a.rater_id], a.position) for a in design.assignments]
    assert keys == sorted(keys), f"{label}: assignments are not sorted by rater (study order), then position"
    by_rater: dict[str, list] = defaultdict(list)
    for a in design.assignments:
        by_rater[a.rater_id].append(a)
    for r, seq in by_rater.items():
        assert [a.position for a in seq] == list(range(1, len(seq) + 1)), f"{label}: positions for {r}"
        if study.group_by_case:
            seq_cases = [case_of[a.item_id] for a in seq]
            blocks = [k for k, _ in itertools.groupby(seq_cases)]
            assert len(blocks) == len(set(blocks)), f"{label}: case split in {r}'s sheet: {seq_cases}"

    # 6. unique ids with the right shape
    rids = [a.rating_id for a in design.assignments]
    assert len(set(rids)) == len(rids), f"{label}: duplicate rating ids"
    assert all(RATING_ID_RE.match(x) for x in rids), f"{label}: bad rating id format"
    assert set(design.item_codes) == set(source_of), f"{label}: item codes do not cover all items"
    codes = list(design.item_codes.values())
    assert len(set(codes)) == len(codes), f"{label}: duplicate item codes"
    assert all(ITEM_CODE_RE.match(c) for c in codes), f"{label}: bad item code format"
    assert not set(rids) & set(codes)


# ---- the sweep ------------------------------------------------------------------------------


@pytest.mark.parametrize("n_raters", range(1, 9))
def test_guarantees_over_sweep(n_raters: int) -> None:
    raters = tuple(f"R{i}" for i in range(1, n_raters + 1))
    n_checked = 0
    for rpi in range(1, n_raters + 1):
        for n_items, kind, seed in itertools.product(ITEM_COUNTS, LAYOUTS, SEEDS):
            study = make_study(layout(kind, n_items), raters=raters, rpi=rpi, seed=seed)
            label = f"raters={n_raters} rpi={rpi} items={n_items} layout={kind} seed={seed}"
            check_design(study, make_design(study), label)
            n_checked += 1
    assert n_checked == n_raters * len(ITEM_COUNTS) * len(LAYOUTS) * len(SEEDS)


@pytest.mark.parametrize("n_raters", [2, 3, 4, 5, 7])
def test_guarantees_with_group_by_case(n_raters: int) -> None:
    raters = tuple(f"R{i}" for i in range(1, n_raters + 1))
    for rpi in range(1, n_raters + 1):
        for n_items, cases_of, kind, seed in itertools.product([6, 13, 31], [1, 2, 3], ["balanced", "skewed"], SEEDS):
            study = make_study(
                layout(kind, n_items), raters=raters, rpi=rpi, seed=seed, group_by_case=True, cases_of=cases_of
            )
            label = f"raters={n_raters} rpi={rpi} items={n_items} cases_of={cases_of} layout={kind} seed={seed}"
            design = make_design(study)
            case_of = {i.item_id: i.case_id for i in study.items}
            for r in raters:
                seq = [case_of[a.item_id] for a in design.assignments if a.rater_id == r]
                blocks = [k for k, _ in itertools.groupby(seq)]
                assert len(blocks) == len(set(blocks)), f"{label}: case split in {r}'s sheet: {seq}"
            # the other guarantees still hold
            check_design(study, design, label)


def test_case_adjacency_is_real_not_accidental() -> None:
    # 3 raters see everything; with cases of 3, a random order would split cases almost surely.
    study = make_study({SRC_A: 15, SRC_B: 15}, group_by_case=True, cases_of=3, raters=("R1", "R2", "R3"))
    design = make_design(study)
    case_of = {i.item_id: i.case_id for i in study.items}
    for r in study.raters:
        seq = [case_of[a.item_id] for a in design.assignments if a.rater_id == r]
        assert [k for k, _ in itertools.groupby(seq)] == sorted(set(seq), key=seq.index)
        assert len(set(seq)) == 10


def test_guarantees_over_random_shapes_and_secrets() -> None:
    # The load guarantees hold by construction, so they must hold for any shape and any secret,
    # including sources of one item and sources that do not divide evenly among the raters.
    shapes = random.Random(20261127)
    for trial in range(300):
        n_raters = shapes.randint(1, 12)
        rpi = shapes.randint(1, n_raters)
        sizes = [shapes.randint(1, 40) for _ in range(shapes.randint(1, 4))]
        study = make_study(
            {f"source{n}": size for n, size in enumerate(sizes)},
            raters=tuple(f"R{i}" for i in range(1, n_raters + 1)),
            rpi=rpi,
            seed=trial,
        )
        label = f"trial={trial} raters={n_raters} rpi={rpi} sizes={sizes}"
        check_design(study, make_design(study, secret=f"{trial:064x}"), label)


# ---- co-rating is spread over the pairs of raters -------------------------------------------

# raters, raters per item, items per source, largest allowed (max - min) of the pair counts.
# The bounds are loose on purpose (one more than was ever seen); a rule that lets the load
# decide every pick gives 13 for the first shape and pairs that share nothing for others.
ORDINARY_SHAPES = [
    (4, 2, [25, 25], 2),
    (4, 2, [25, 25, 25], 3),
    (3, 2, [4, 4, 4], 1),
    (5, 2, [20, 20], 2),
    (6, 2, [10, 10, 10], 3),
    (6, 2, [20, 20, 20], 2),
    (6, 3, [25, 25, 25], 4),
    (8, 2, [25, 25, 25, 25], 3),
    (12, 2, [50, 50, 50], 3),
]


@pytest.mark.parametrize("n_raters,rpi,sizes,bound", ORDINARY_SHAPES)
def test_co_rating_is_spread_over_all_pairs(n_raters: int, rpi: int, sizes: list[int], bound: int) -> None:
    raters = tuple(f"R{i}" for i in range(1, n_raters + 1))
    for seed in range(12):
        study = make_study({f"source{n}": size for n, size in enumerate(sizes)}, raters=raters, rpi=rpi, seed=seed)
        for secret in (NONCE, OTHER_NONCE):
            design = make_design(study, secret)
            label = f"raters={n_raters} rpi={rpi} sizes={sizes} seed={seed} secret={secret[:4]}"
            counts = design_summary(study, design)["pair_counts"]
            assert min(counts.values()) > 0, f"{label}: a pair of raters shares no item: {counts}"
            spread = max(counts.values()) - min(counts.values())
            assert spread <= bound, f"{label}: pair counts differ by {spread}: {counts}"
            check_design(study, design, label)


def test_pairs_are_balanced_within_a_source_too() -> None:
    # 4 raters, 2 per item, 25 items per source: 50 seats per source do not divide by 4, which
    # used to make the same two pairs rate a whole source together (0 items for the other four).
    study = make_study({SRC_A: 25, SRC_B: 25}, raters=("R1", "R2", "R3", "R4"), rpi=2, seed=20261126)
    design = make_design(study)
    raters_of: dict[str, list[str]] = defaultdict(list)
    for a in design.assignments:
        raters_of[a.item_id].append(a.rater_id)
    for src in study.sources:
        pairs = Counter(
            tuple(sorted(raters_of[i.item_id])) for i in study.items if i.source == src
        )
        assert len(pairs) == 6, f"{src}: only the pairs {dict(pairs)} rate together"
        assert max(pairs.values()) - min(pairs.values()) <= 3, f"{src}: {dict(pairs)}"


# raters, raters per item, number of one-item sources, largest allowed (max - min) of the pair
# counts (one more than was ever seen, except where the counts can be exactly equal).
ONE_ITEM_SOURCE_SHAPES = [
    (4, 2, 12, 0),
    (5, 2, 20, 3),
    (6, 2, 30, 3),
    (6, 3, 30, 4),
    (8, 3, 40, 4),
]


@pytest.mark.parametrize("n_raters,rpi,n_sources,bound", ONE_ITEM_SOURCE_SHAPES)
def test_co_rating_is_spread_when_every_source_has_one_item(n_raters: int, rpi: int, n_sources: int, bound: int) -> None:
    # One text per source (one author per text, say). The single item of a source goes to exactly
    # the raters whose quota for it is 1, so the pick among eligible raters never has a choice;
    # the spreading has to happen when the quotas are set. When chance decided among raters with
    # equal totals, 4 raters gave pair counts from 0 to 5 here, and pairs that share nothing.
    raters = tuple(f"R{i}" for i in range(1, n_raters + 1))
    for seed in range(12):
        study = make_study({f"source{n}": 1 for n in range(n_sources)}, raters=raters, rpi=rpi, seed=seed)
        for secret in (NONCE, OTHER_NONCE):
            design = make_design(study, secret)
            label = f"raters={n_raters} rpi={rpi} one-item sources={n_sources} seed={seed} secret={secret[:4]}"
            counts = design_summary(study, design)["pair_counts"]
            assert min(counts.values()) > 0, f"{label}: a pair of raters shares no item: {counts}"
            spread = max(counts.values()) - min(counts.values())
            assert spread <= bound, f"{label}: pair counts differ by {spread}: {counts}"
            check_design(study, design, label)


def test_guarantees_hold_with_many_small_sources() -> None:
    # Spreading co-rating when the quotas are set may only tell apart raters with equal totals.
    # Many sources of one to three items are where the quotas decide most, so a rule that let
    # the pairs outrank the totals would show here as raters more than one item apart.
    shapes = random.Random(20261128)
    for trial in range(300):
        n_raters = shapes.randint(1, 12)
        rpi = shapes.randint(1, n_raters)
        sizes = [shapes.choice([1, 1, 2, 3]) for _ in range(shapes.randint(1, 14))]
        study = make_study(
            {f"source{n}": size for n, size in enumerate(sizes)},
            raters=tuple(f"R{i}" for i in range(1, n_raters + 1)),
            rpi=rpi,
            seed=trial,
        )
        label = f"trial={trial} raters={n_raters} rpi={rpi} sizes={sizes}"
        check_design(study, make_design(study, secret=f"{trial:064x}"), label)


# ---- determinism, seeds and the build secret ------------------------------------------------


def test_same_study_same_design() -> None:
    a = make_design(make_study({SRC_A: 10, SRC_B: 9}, rpi=2))
    b = make_design(make_study({SRC_A: 10, SRC_B: 9}, rpi=2))
    assert a == b
    assert a.assignments == b.assignments and a.item_codes == b.item_codes


def test_secret_is_required_and_keyword_only() -> None:
    study = make_study({SRC_A: 4, SRC_B: 4})
    with pytest.raises(TypeError):
        make_design_with_secret(study)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        make_design_with_secret(study, NONCE)  # type: ignore[misc]


def test_a_different_secret_changes_ids_codes_orders_and_who_rates_what() -> None:
    partial = make_study({SRC_A: 12, SRC_B: 12}, rpi=2)
    a, b = make_design(partial, NONCE), make_design(partial, OTHER_NONCE)
    assert not {x.rating_id for x in a.assignments} & {x.rating_id for x in b.assignments}
    assert not set(a.item_codes.values()) & set(b.item_codes.values())
    assert {(x.rater_id, x.item_id) for x in a.assignments} != {(x.rater_id, x.item_id) for x in b.assignments}
    crossed = make_study({SRC_A: 12, SRC_B: 12})  # everyone rates everything: only the order can differ
    c, d = make_design(crossed, NONCE), make_design(crossed, OTHER_NONCE)
    for r in crossed.raters:
        assert [x.item_id for x in c.assignments if x.rater_id == r] != [
            x.item_id for x in d.assignments if x.rater_id == r
        ], f"{r} has the same order under two secrets"


def test_nothing_a_rater_or_blind_analyst_holds_follows_from_the_seed_alone() -> None:
    # What the streams gave before the secret was mixed in: anyone could compute these from a
    # guessed seed, compare them with one rating id, and then replay the shuffle.
    study = make_study({SRC_A: 12, SRC_B: 12}, seed=20261126)  # fully crossed
    design = make_design(study)
    for prefix in ("", "|"):  # the old stream names, and the new ones with an empty secret
        ids = DeterministicRNG(study.seed, f"{prefix}ids")
        seed_only_codes = {"I-" + ids.code(ALPHA, 6) for _ in study.items}
        seed_only_rating_ids = [ids.code(ALPHA, 4) + "-" + ids.code(ALPHA, 4) for _ in design.assignments]
        assert design.assignments[0].rating_id != seed_only_rating_ids[0]
        assert not {a.rating_id for a in design.assignments} & set(seed_only_rating_ids)
        assert not set(design.item_codes.values()) & seed_only_codes
        for r in study.raters:
            replay = DeterministicRNG(study.seed, f"{prefix}order|{r}").shuffled([i.item_id for i in study.items])
            assert [a.item_id for a in design.assignments if a.rater_id == r] != replay, f"{r}: seed-only order"


def test_design_can_be_regenerated_from_seed_and_secret() -> None:
    # The streams are named "<secret>|ids" and "<secret>|order|<rater>": with the key (seed and
    # nonce) anyone can recompute the codes and the order, independently of this package's code.
    study = make_study({SRC_A: 12, SRC_B: 12}, seed=20261126)  # fully crossed
    design = make_design(study, OTHER_NONCE)
    ids = DeterministicRNG(study.seed, f"{OTHER_NONCE}|ids")
    assert list(design.item_codes.values()) == ["I-" + ids.code(ALPHA, 6) for _ in study.items]
    assert [a.rating_id for a in design.assignments] == [
        ids.code(ALPHA, 4) + "-" + ids.code(ALPHA, 4) for _ in design.assignments
    ]
    for r in study.raters:
        replay = DeterministicRNG(study.seed, f"{OTHER_NONCE}|order|{r}").shuffled([i.item_id for i in study.items])
        assert [a.item_id for a in design.assignments if a.rater_id == r] == replay


def test_changing_the_seed_changes_the_design() -> None:
    a = make_design(make_study({SRC_A: 12, SRC_B: 12}, rpi=2, seed=1))
    b = make_design(make_study({SRC_A: 12, SRC_B: 12}, rpi=2, seed=2))
    assert a.assignments != b.assignments
    assert a.item_codes != b.item_codes
    assert {(x.rater_id, x.item_id) for x in a.assignments} != {(x.rater_id, x.item_id) for x in b.assignments}


def test_renamed_raters_still_give_a_valid_design() -> None:
    s1 = make_study({SRC_A: 8, SRC_B: 8}, raters=("A", "B", "C"), rpi=2)
    check_design(s1, make_design(s1), "renamed raters")


def test_raters_have_independent_orders() -> None:
    study = make_study({SRC_A: 15, SRC_B: 15}, raters=("R1", "R2", "R3"))  # fully crossed
    design = make_design(study)
    seqs = {r: [a.item_id for a in design.assignments if a.rater_id == r] for r in study.raters}
    assert len({tuple(s) for s in seqs.values()}) == 3, "every rater should get a different order"


# ---- group_by_case does not change who rates what -----------------------------------------


@pytest.mark.parametrize("n_raters,rpi", [(3, 2), (4, 1), (5, 3), (2, 2), (6, 4)])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_group_by_case_does_not_change_who_rates_which_item(n_raters: int, rpi: int, seed: int) -> None:
    raters = tuple(f"R{i}" for i in range(1, n_raters + 1))
    rows = make_item_rows({SRC_A: 9, SRC_B: 8, SRC_C: 4}, with_case=True, cases_of=3)
    plain = make_study(rows=rows, raters=raters, rpi=rpi, seed=seed, group_by_case=False)
    grouped = make_study(rows=rows, raters=raters, rpi=rpi, seed=seed, group_by_case=True)
    d1, d2 = make_design(plain), make_design(grouped)
    p1 = {(a.rater_id, a.item_id) for a in d1.assignments}
    p2 = {(a.rater_id, a.item_id) for a in d2.assignments}
    assert p1 == p2, f"raters={n_raters} rpi={rpi} seed={seed}: group_by_case changed the assignment"


def test_grouped_design_never_splits_cases_with_cases_of_four() -> None:
    rows = make_item_rows({SRC_A: 12, SRC_B: 12}, with_case=True, cases_of=4)
    raters = ("R1", "R2")
    grouped = make_design(make_study(rows=rows, raters=raters, group_by_case=True))
    case_of = {r["item_id"]: r["case_id"] for r in rows}
    for r in raters:
        seq = [case_of[a.item_id] for a in grouped.assignments if a.rater_id == r]
        assert len({k for k, _ in itertools.groupby(seq)}) == len(list(itertools.groupby(seq)))


# ---- ids ------------------------------------------------------------------------------------


def test_alphabet_constant() -> None:
    assert RATING_ID_ALPHABET == ALPHA
    assert not set("ILO01") & set(RATING_ID_ALPHABET)


def test_id_formats_and_uniqueness_in_a_big_design() -> None:
    study = make_study({SRC_A: 40, SRC_B: 40, SRC_C: 40}, raters=tuple(f"R{i}" for i in range(1, 9)), rpi=8)
    design = make_design(study)
    assert len(design.assignments) == 120 * 8
    rids = [a.rating_id for a in design.assignments]
    assert len(set(rids)) == len(rids)
    assert all(RATING_ID_RE.match(r) for r in rids)
    codes = list(design.item_codes.values())
    assert len(codes) == 120 and len(set(codes)) == 120
    assert all(ITEM_CODE_RE.match(c) for c in codes)


def test_ids_do_not_contain_item_ids_or_sources() -> None:
    study = make_study({SRC_A: 6, SRC_B: 6})
    design = make_design(study)
    blob = " ".join([a.rating_id for a in design.assignments] + list(design.item_codes.values())).lower()
    for item in study.items:
        assert item.item_id.lower() not in blob
    for src in study.sources:
        assert src.lower() not in blob


def test_ids_do_not_follow_item_file_order() -> None:
    # Codes are random: sorted codes should not reproduce the file order of items.
    study = make_study({SRC_A: 30, SRC_B: 30})
    design = make_design(study)
    in_file_order = [design.item_codes[i.item_id] for i in study.items]
    assert in_file_order != sorted(in_file_order)
    assert in_file_order != sorted(in_file_order, reverse=True)


def test_item_ids_in_assignments_exist() -> None:
    study = make_study({SRC_A: 5, SRC_B: 5}, rpi=2)
    design = make_design(study)
    ids = {i.item_id for i in study.items}
    assert {a.item_id for a in design.assignments} == ids


# ---- single rater and fully crossed edge cases -------------------------------------------


def test_one_rater_gets_everything() -> None:
    study = make_study({SRC_A: 5, SRC_B: 4}, raters=("Solo",), rpi=1)
    design = make_design(study)
    assert [a.rater_id for a in design.assignments] == ["Solo"] * 9
    assert sorted(a.item_id for a in design.assignments) == sorted(i.item_id for i in study.items)
    assert [a.position for a in design.assignments] == list(range(1, 10))


def test_fully_crossed_everyone_rates_everything() -> None:
    study = make_study({SRC_A: 5, SRC_B: 4}, raters=("R1", "R2", "R3"), rpi=3)
    design = make_design(study)
    for r in study.raters:
        assert sorted(a.item_id for a in design.assignments if a.rater_id == r) == sorted(
            i.item_id for i in study.items
        )


# ---- design_summary ------------------------------------------------------------------------


@pytest.mark.parametrize("n_raters,rpi", [(1, 1), (3, 2), (4, 4), (5, 2), (6, 3)])
def test_design_summary(n_raters: int, rpi: int) -> None:
    raters = tuple(f"R{i}" for i in range(1, n_raters + 1))
    study = make_study({SRC_A: 9, SRC_B: 5, SRC_C: 3}, raters=raters, rpi=rpi)
    design = make_design(study)
    s = design_summary(study, design)
    assert s["n_items"] == 17 and s["n_raters"] == n_raters
    assert s["raters_per_item"] == rpi and s["n_assignments"] == 17 * rpi
    assert set(s["per_rater"]) == set(raters)
    assert sum(s["per_rater"].values()) == 17 * rpi
    source_of = {i.item_id: i.source for i in study.items}
    expected = {r: Counter() for r in raters}
    for a in design.assignments:
        expected[a.rater_id][source_of[a.item_id]] += 1
    for r in raters:
        assert s["per_rater"][r] == sum(expected[r].values())
        assert set(s["per_rater_source"][r]) == set(study.sources), "every source is a key, 0 if none"
        for src in study.sources:
            assert s["per_rater_source"][r][src] == expected[r][src]
    rated_by: dict[str, list[str]] = defaultdict(list)
    for a in design.assignments:
        rated_by[a.item_id].append(a.rater_id)
    expected_pairs: Counter[str] = Counter()
    for rs in rated_by.values():
        for x, y in itertools.combinations(sorted(rs, key=raters.index), 2):
            expected_pairs[f"{x}|{y}"] += 1
    for key, val in s["pair_counts"].items():
        assert re.fullmatch(r"[^|]+\|[^|]+", key), key
        a, b = key.split("|")
        assert raters.index(a) < raters.index(b), f"pair key {key} not in study order"
        assert val == expected_pairs.get(key, 0), key
    for key, val in expected_pairs.items():
        assert s["pair_counts"].get(key) == val, f"missing pair {key}"
    assert sum(s["pair_counts"].values()) == 17 * rpi * (rpi - 1) // 2


def test_fully_crossed_pair_counts() -> None:
    study = make_study({SRC_A: 4, SRC_B: 4}, raters=("R1", "R2", "R3"), rpi=3)
    s = design_summary(study, make_design(study))
    assert s["pair_counts"] == {"R1|R2": 8, "R1|R3": 8, "R2|R3": 8}
