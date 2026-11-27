"""A small random number generator that is the same on every Python version.

The standard ``random`` module does not promise identical output across Python
releases. A study must be reproducible from its seed years later, so every
random choice in ratersheet comes from this class, whose output is defined
only by SHA-256 and integer arithmetic.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

_WORD_BYTES = 8
_WORD_RANGE = 2**64


class DeterministicRNG:
    """Random integers, shuffles and codes derived from a seed and a stream name.

    Two generators with the same seed and different stream names are
    independent, which lets one part of a study (for example the order of
    items) change without disturbing another (who rates what).
    """

    def __init__(self, seed: int, stream: str) -> None:
        self._seed = seed
        self._stream = stream
        self._next_block = 0
        self._buffer = b""

    def _take(self, n_bytes: int) -> bytes:
        """Return the next ``n_bytes`` of the byte stream."""
        while len(self._buffer) < n_bytes:
            label = f"ratersheet-rng-v1|{self._seed}|{self._stream}|{self._next_block}"
            self._buffer += hashlib.sha256(label.encode("utf-8")).digest()
            self._next_block += 1
        taken, self._buffer = self._buffer[:n_bytes], self._buffer[n_bytes:]
        return taken

    def randbelow(self, n: int) -> int:
        """Return a uniform integer in ``[0, n)``, for ``n`` from 1 to 2**64.

        Values at or above the largest multiple of ``n`` are discarded, because
        a plain remainder would make small results slightly more likely. One
        draw is one 64-bit word, so a larger ``n`` is refused: every word would
        be discarded and the call would never return.
        """
        if n <= 0:
            raise ValueError(f"randbelow needs a positive n, got {n}")
        if n > _WORD_RANGE:
            raise ValueError(f"randbelow supports n up to 2**64, got {n}")
        limit = (_WORD_RANGE // n) * n
        while True:
            x = int.from_bytes(self._take(_WORD_BYTES), "big")
            if x < limit:
                return x % n

    def shuffle(self, seq: list[Any]) -> None:
        """Shuffle ``seq`` in place (Fisher-Yates, from the end)."""
        for i in range(len(seq) - 1, 0, -1):
            j = self.randbelow(i + 1)
            seq[i], seq[j] = seq[j], seq[i]

    def shuffled(self, seq: Iterable[Any]) -> list[Any]:
        """Return a new shuffled list and leave ``seq`` untouched."""
        result = list(seq)
        self.shuffle(result)
        return result

    def code(self, alphabet: str, length: int) -> str:
        """Return ``length`` characters drawn uniformly from ``alphabet``."""
        return "".join(alphabet[self.randbelow(len(alphabet))] for _ in range(length))
