"""Deterministic monotonic entity ID allocation."""

from __future__ import annotations

import re

_KIND = re.compile(r"^[a-z][a-z0-9_]*$")


class IdAllocator:
    def __init__(self, counters: dict[str, int] | None = None) -> None:
        self._counters: dict[str, int] = {}
        for kind, counter in sorted((counters or {}).items()):
            self._validate_kind(kind)
            if type(counter) is not int or counter < 0:
                raise ValueError("ID counters must be nonnegative integers")
            self._counters[kind] = counter

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if not isinstance(kind, str) or not _KIND.fullmatch(kind):
            raise ValueError("ID kind must be lowercase snake_case")

    def next(self, kind: str) -> str:
        self._validate_kind(kind)
        number = self._counters.get(kind, 0) + 1
        self._counters[kind] = number
        return f"{kind}_{number:06d}"

    def snapshot(self) -> dict[str, int]:
        return {kind: self._counters[kind] for kind in sorted(self._counters)}

    @classmethod
    def restore(cls, state: dict[str, int]) -> "IdAllocator":
        if not isinstance(state, dict):
            raise ValueError("ID allocator snapshot must be a mapping")
        return cls(state)
