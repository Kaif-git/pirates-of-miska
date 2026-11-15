"""Energy-based turn scheduler (CDDA-style). Fast actors act more often."""
from __future__ import annotations
import heapq
from dataclasses import dataclass, field


@dataclass(order=True)
class _Entry:
    time: int
    seq: int
    actor_id: int = field(compare=False)


class TimeQueue:
    """Pop next actor; re-add with `delay` = energy cost of their action."""

    def __init__(self) -> None:
        self._heap: list[_Entry] = []
        self._seq = 0
        self.now = 0

    def schedule(self, actor_id: int, delay: int) -> None:
        self._seq += 1
        heapq.heappush(self._heap, _Entry(self.now + delay, self._seq, actor_id))

    def pop_next(self) -> int:
        entry = heapq.heappop(self._heap)
        self.now = entry.time
        return entry.actor_id

    def __len__(self) -> int:
        return len(self._heap)
