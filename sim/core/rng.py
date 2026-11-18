"""Stable counter-based RNG streams isolated by named subsystem/agent."""

from __future__ import annotations

import hashlib
import re
from typing import Sequence, TypeVar

T = TypeVar("T")
_STREAM_NAME = re.compile(r"^[A-Za-z0-9_.:/-]+$")
_UINT64_SPACE = 1 << 64


def _seed_bytes(seed: int | str) -> bytes:
    if type(seed) is int:
        raw = b"int:" + str(seed).encode("ascii")
    elif isinstance(seed, str):
        raw = b"str:" + seed.encode("utf-8")
    else:
        raise ValueError("Master seed must be an integer or string")
    return hashlib.blake2b(raw, digest_size=32, person=b"MiskaSimSeedV1").digest()


class RandomStream:
    """A JSON-checkpointable stream whose draws cannot perturb other streams."""

    def __init__(self, seed_key: bytes, name: str, counter: int = 0) -> None:
        if not isinstance(name, str) or not _STREAM_NAME.fullmatch(name):
            raise ValueError("RNG stream names must use letters, digits, '.', '_', ':', '/', or '-'")
        if type(counter) is not int or counter < 0:
            raise ValueError("RNG counter must be a nonnegative integer")
        self.name = name
        self._key = hashlib.blake2b(name.encode("utf-8"), key=seed_key,
                                    digest_size=32, person=b"MiskaStreamV1").digest()
        self.counter = counter

    def _word64(self) -> int:
        encoded_counter = str(self.counter).encode("ascii")
        digest = hashlib.blake2b(encoded_counter, key=self._key, digest_size=8,
                                 person=b"MiskaDrawV1").digest()
        self.counter += 1
        return int.from_bytes(digest, "big")

    def random(self) -> float:
        """Return a deterministic IEEE-754 fraction in [0, 1)."""
        return (self._word64() >> 11) / (1 << 53)

    def randbelow(self, upper: int) -> int:
        if type(upper) is not int or upper <= 0:
            raise ValueError("randbelow upper bound must be a positive integer")
        limit = _UINT64_SPACE - (_UINT64_SPACE % upper)
        while True:
            value = self._word64()
            if value < limit:
                return value % upper

    def randint(self, lower: int, upper: int) -> int:
        if type(lower) is not int or type(upper) is not int or upper < lower:
            raise ValueError("randint requires integer bounds with upper >= lower")
        return lower + self.randbelow(upper - lower + 1)

    def choice(self, values: Sequence[T]) -> T:
        if not values:
            raise IndexError("Cannot choose from an empty sequence")
        return values[self.randbelow(len(values))]

    def snapshot(self) -> dict[str, int | str]:
        return {"name": self.name, "counter": self.counter}


class RandomStreams:
    """Derive and retain named, independent streams from one master seed."""

    def __init__(self, seed: int | str, counters: dict[str, int] | None = None) -> None:
        self.seed = seed
        self._seed_key = _seed_bytes(seed)
        self._streams: dict[str, RandomStream] = {}
        for name, counter in sorted((counters or {}).items()):
            self._streams[name] = RandomStream(self._seed_key, name, counter)

    def stream(self, name: str) -> RandomStream:
        if name not in self._streams:
            self._streams[name] = RandomStream(self._seed_key, name)
        return self._streams[name]

    def snapshot(self) -> dict[str, int]:
        return {name: self._streams[name].counter for name in sorted(self._streams)}

    @classmethod
    def restore(cls, seed: int | str, counters: dict[str, int]) -> "RandomStreams":
        if not isinstance(counters, dict):
            raise ValueError("RNG stream snapshot must be a mapping")
        return cls(seed, counters)
