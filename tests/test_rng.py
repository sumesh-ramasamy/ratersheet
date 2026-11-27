"""DeterministicRNG: behaviour, and a pin of the exact algorithm from the contract."""

from __future__ import annotations

import hashlib
import threading

import pytest

from ratersheet import DeterministicRNG


# ---- independent reimplementation, written from the contract only -------------------------


class RefRNG:
    def __init__(self, seed: int, stream: str) -> None:
        self.seed, self.stream = seed, stream
        self.buf = b""
        self.block = 0

    def _take(self, k: int) -> bytes:
        while len(self.buf) < k:
            self.buf += hashlib.sha256(
                f"ratersheet-rng-v1|{self.seed}|{self.stream}|{self.block}".encode("utf-8")
            ).digest()
            self.block += 1
        out, self.buf = self.buf[:k], self.buf[k:]
        return out

    def randbelow(self, n: int) -> int:
        limit = (2**64 // n) * n
        while True:
            x = int.from_bytes(self._take(8), "big")
            if x < limit:
                return x % n

    def shuffle(self, seq: list) -> None:
        for i in range(len(seq) - 1, 0, -1):
            j = self.randbelow(i + 1)
            seq[i], seq[j] = seq[j], seq[i]

    def code(self, alphabet: str, length: int) -> str:
        return "".join(alphabet[self.randbelow(len(alphabet))] for _ in range(length))


SEEDS = [0, 1, 42, 20261126, 7391846, 12345678901234567890]
STREAMS = ["assign", "ids", "order|R1", "order|R2", "x", ""]
NS = [1, 2, 3, 5, 7, 10, 31, 100, 1000, 65537, 2**31 - 1, 2**32, 2**63 - 1, 2**63 + 5, 2**64 - 1]


# ---- behaviour ------------------------------------------------------------------------------


def test_same_seed_and_stream_give_same_sequence() -> None:
    a = DeterministicRNG(5, "s")
    b = DeterministicRNG(5, "s")
    assert [a.randbelow(10**6) for _ in range(50)] == [b.randbelow(10**6) for _ in range(50)]


def test_different_seeds_differ() -> None:
    a = DeterministicRNG(5, "s")
    b = DeterministicRNG(6, "s")
    assert [a.randbelow(10**9) for _ in range(20)] != [b.randbelow(10**9) for _ in range(20)]


def test_different_streams_differ() -> None:
    a = DeterministicRNG(5, "assign")
    b = DeterministicRNG(5, "ids")
    assert [a.randbelow(10**9) for _ in range(20)] != [b.randbelow(10**9) for _ in range(20)]


@pytest.mark.parametrize("n", [1, 2, 3, 7, 100, 12345])
def test_randbelow_stays_in_range(n: int) -> None:
    rng = DeterministicRNG(3, "range")
    values = [rng.randbelow(n) for _ in range(500)]
    assert all(isinstance(v, int) and 0 <= v < n for v in values), f"n={n}"


def test_randbelow_covers_small_range() -> None:
    rng = DeterministicRNG(3, "cover")
    assert {rng.randbelow(4) for _ in range(400)} == {0, 1, 2, 3}


@pytest.mark.parametrize("n", [0, -1, -100])
def test_randbelow_rejects_non_positive(n: int) -> None:
    with pytest.raises(ValueError):
        DeterministicRNG(1, "x").randbelow(n)


@pytest.mark.parametrize("n", [2**64 + 1, 2**64 + 2**63, 2**100])
def test_randbelow_rejects_n_above_one_word_instead_of_never_returning(n: int) -> None:
    # One draw is one 64-bit word; for a larger n every word would be discarded, forever. The
    # call runs in a thread so that a version that never returns fails this test, not hangs it.
    raised: list[ValueError] = []

    def call() -> None:
        try:
            DeterministicRNG(1, "a").randbelow(n)
        except ValueError as exc:
            raised.append(exc)

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "randbelow did not return within 5 seconds"
    assert raised and "2**64" in str(raised[0]), "a ValueError that names the limit"


def test_randbelow_accepts_exactly_one_word() -> None:
    first8 = hashlib.sha256(b"ratersheet-rng-v1|1|a|0").digest()[:8]
    assert DeterministicRNG(1, "a").randbelow(2**64) == int.from_bytes(first8, "big")


@pytest.mark.parametrize("size", [0, 1, 2, 3, 10, 57])
def test_shuffle_is_a_permutation_in_place(size: int) -> None:
    seq = list(range(size))
    same_object = seq
    DeterministicRNG(9, "shuf").shuffle(seq)
    assert seq is same_object
    assert sorted(seq) == list(range(size))


def test_shuffle_actually_moves_things() -> None:
    seq = list(range(30))
    DeterministicRNG(9, "shuf").shuffle(seq)
    assert seq != list(range(30))


def test_shuffled_returns_new_list_and_leaves_input_alone() -> None:
    original = list("abcdefghij")
    snapshot = list(original)
    out = DeterministicRNG(9, "shuf").shuffled(original)
    assert original == snapshot
    assert out is not original
    assert sorted(out) == sorted(original)


def test_shuffled_accepts_any_iterable_sequence() -> None:
    out = DeterministicRNG(9, "shuf").shuffled(("a", "b", "c", "d"))
    assert isinstance(out, list) and sorted(out) == ["a", "b", "c", "d"]


def test_code_uses_alphabet_and_length() -> None:
    c = DeterministicRNG(2, "code").code("ABC", 40)
    assert len(c) == 40 and set(c) <= set("ABC")
    assert DeterministicRNG(2, "code").code("ABC", 0) == ""


# ---- pin of the exact algorithm -------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("stream", STREAMS)
def test_randbelow_matches_reference_for_many_n(seed: int, stream: str) -> None:
    real, ref = DeterministicRNG(seed, stream), RefRNG(seed, stream)
    for n in NS:
        for _ in range(3):
            assert real.randbelow(n) == ref.randbelow(n), f"seed={seed} stream={stream!r} n={n}"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("stream", ["assign", "order|R1", "z"])
def test_long_run_crosses_hash_block_boundaries(seed: int, stream: str) -> None:
    # 200 draws of 8 bytes need 50 sha256 blocks; mixes sizes so consumption stays aligned.
    real, ref = DeterministicRNG(seed, stream), RefRNG(seed, stream)
    for i in range(200):
        n = (i % 13) + 1
        assert real.randbelow(n) == ref.randbelow(n), f"seed={seed} stream={stream!r} draw={i}"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("stream", ["assign", "order|R2"])
@pytest.mark.parametrize("size", [0, 1, 2, 3, 8, 31, 100])
def test_shuffle_matches_reference(seed: int, stream: str, size: int) -> None:
    a, b = list(range(size)), list(range(size))
    DeterministicRNG(seed, stream).shuffle(a)
    RefRNG(seed, stream).shuffle(b)
    assert a == b, f"seed={seed} stream={stream!r} size={size}"


@pytest.mark.parametrize("seed", SEEDS)
def test_shuffled_matches_reference(seed: int) -> None:
    src = [f"x{i}" for i in range(25)]
    ref = list(src)
    RefRNG(seed, "ids").shuffle(ref)
    assert DeterministicRNG(seed, "ids").shuffled(src) == ref


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("stream", ["ids", "other"])
@pytest.mark.parametrize(
    "alphabet,length",
    [("ABCDEFGHJKMNPQRSTUVWXYZ23456789", 4), ("ABCDEFGHJKMNPQRSTUVWXYZ23456789", 6), ("01", 17), ("é€x", 9)],
)
def test_code_matches_reference(seed: int, stream: str, alphabet: str, length: int) -> None:
    assert DeterministicRNG(seed, stream).code(alphabet, length) == RefRNG(seed, stream).code(alphabet, length)


def test_interleaved_operations_match_reference() -> None:
    real, ref = DeterministicRNG(77, "mix"), RefRNG(77, "mix")
    a, b = list(range(10)), list(range(10))
    for _ in range(5):
        assert real.randbelow(9) == ref.randbelow(9)
        real.shuffle(a)
        ref.shuffle(b)
        assert real.code("ABCD", 5) == ref.code("ABCD", 5)
    assert a == b


def test_known_first_block_value() -> None:
    # randbelow(2**64 - 1) with the first 8 bytes of block 0 is fully determined by the contract.
    first8 = hashlib.sha256(b"ratersheet-rng-v1|1|abc|0").digest()[:8]
    x = int.from_bytes(first8, "big")
    n = 2**64 - 1
    limit = (2**64 // n) * n  # == n
    if x < limit:
        assert DeterministicRNG(1, "abc").randbelow(n) == x % n
    else:  # astronomically unlikely; the rejection loop must then use the next 8 bytes
        second = int.from_bytes(hashlib.sha256(b"ratersheet-rng-v1|1|abc|0").digest()[8:16], "big")
        assert DeterministicRNG(1, "abc").randbelow(n) == second % n
