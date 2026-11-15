"""Central game-time clock. One hundred simulation ticks are one in-world second.

The fine tick (10 ms) exists so work attempts, movement, and skill pacing can
be measured and shown to the player at a tenth-of-a-second resolution ("x time
elapsed") while the calendar still runs on plain seconds/minutes/hours.
"""
from __future__ import annotations

from dataclasses import dataclass

TICKS_PER_SECOND = 100
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE
TICKS_PER_MINUTE = TICKS_PER_SECOND * SECONDS_PER_MINUTE
TICKS_PER_HOUR = TICKS_PER_SECOND * SECONDS_PER_HOUR
TICKS_PER_DAY = 24 * TICKS_PER_HOUR
START_OF_DAY_TICKS = 8 * TICKS_PER_HOUR


def format_duration(ticks: int) -> str:
    """Human time span for elapsed readouts: '4.2 s', '1 m 05 s', '2 h 05 m'."""
    ticks = max(0, int(ticks))
    total_seconds = ticks / TICKS_PER_SECOND
    if total_seconds < 60:
        return f"{total_seconds:.1f} s".replace(".0 s", " s")
    minutes, seconds = divmod(round(total_seconds), 60)
    if minutes < 60:
        return f"{minutes} m {seconds:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} m"


@dataclass
class GameClock:
    total_ticks: int = START_OF_DAY_TICKS

    def __post_init__(self) -> None:
        if self.total_ticks < 0:
            raise ValueError("total_ticks cannot be negative")

    @property
    def day(self) -> int:
        return self.total_ticks // TICKS_PER_DAY + 1

    @property
    def second_of_day(self) -> int:
        """Ticks elapsed today (100 ticks per second)."""
        return self.total_ticks % TICKS_PER_DAY

    @property
    def seconds_of_day(self) -> int:
        return self.second_of_day // TICKS_PER_SECOND

    @property
    def hour_minute(self) -> tuple[int, int]:
        minutes = self.seconds_of_day // SECONDS_PER_MINUTE
        return divmod(minutes, 60)

    def advance(self, ticks: int) -> int:
        """Advance time, returning the number of day boundaries crossed."""
        if ticks < 0:
            raise ValueError("cannot advance time by a negative number of ticks")
        old_day = self.day
        self.total_ticks += int(ticks)
        return self.day - old_day

    def format(self) -> str:
        hour, minute = self.hour_minute
        return f"Day {self.day} · {hour:02d}:{minute:02d}"
