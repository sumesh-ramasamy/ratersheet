"""Pinned outputs: a study built today must be regenerated identically in future versions.

The values below were produced by ratersheet 0.1.0. If one of these tests fails, the
change has altered the random stream or the assignment algorithm, which silently changes
every existing study's design. That needs a new stream name (and a changelog entry), not
an updated expected value.

A design is a function of the study and of the secret that ``build`` draws and stores in
the key, so the design pins fix both.
"""

from __future__ import annotations

import hashlib
import json

from ratersheet import DeterministicRNG, Design, Item, Scale, Study, make_design

SECRET = "0123456789abcdef" * 4  # stands for the nonce a build stores in the key


def _study(group_by_case: bool) -> Study:
    items = tuple(
        Item(f"it{n:02d}", ("alpha", "beta", "gamma")[n % 3], f"Text number {n}.", f"case{n // 3}", None)
        for n in range(17)
    )
    scale = Scale("quality", "Quality", "ordinal", (1, 2, 3, 4, 5), "", True)
    return Study("Pinned study", 987654321, "", ("A", "B", "C", "D", "E"), 3, group_by_case, (scale,), False, items, ())


def _digest(design: Design) -> str:
    rows = [[a.rating_id, a.rater_id, a.item_id, a.position] for a in design.assignments]
    blob = json.dumps([rows, sorted(design.item_codes.items())], sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def test_random_stream_is_pinned() -> None:
    rng = DeterministicRNG(20261126, "assign")
    assert [rng.randbelow(1000) for _ in range(8)] == [5, 57, 817, 763, 549, 126, 249, 91]
    assert DeterministicRNG(1, "ids").code("ABCDEFGHJKMNPQRSTUVWXYZ23456789", 12) == 'Y2F62MYE9BXA'
    assert DeterministicRNG(42, "x").shuffled(list(range(10))) == [4, 1, 9, 5, 3, 0, 8, 6, 2, 7]


def test_design_is_pinned() -> None:
    design = make_design(_study(group_by_case=False), secret=SECRET)
    first = [[a.rater_id, a.item_id, a.position, a.rating_id] for a in design.assignments[:4]]
    assert first == [['A', 'it07', 1, 'VKH6-525U'], ['A', 'it00', 2, 'UKXN-W6FC'], ['A', 'it04', 3, 'QSQA-TJ75'], ['A', 'it15', 4, 'GKJG-2AM8']]
    assert _digest(design) == '10d0ef46b25fb37aecf2e0dc92a8d8b5debc5e9adf00386e262317e5d9fc14ac'


def test_grouped_design_is_pinned() -> None:
    grouped = make_design(_study(group_by_case=True), secret=SECRET)
    assert _digest(grouped) == 'db29c38673d053c81ae7f1f6c7525f791277cecf4ffaf9783bc32fab7b36eb58'
