"""IRenderer — THE tilesheet seam. Sim only talks to these methods.
Swap AsciiRenderer -> TilesheetRenderer later; zero sim changes."""
from __future__ import annotations
from typing import Protocol


class IRenderer(Protocol):
    console: object | None

    def init_for(self, map_width: int, map_height: int) -> None: ...
    def render(self, village) -> None: ...  # type: ignore[no-untyped-def]
    def wait_action(self) -> tuple: ...  # type: ignore[no-untyped-def]
    def zoom(self, direction: int, map_width: int, map_height: int) -> None: ...
    def render_list(self, village, title: str, rows: list[str],
                    footer: str = "") -> None: ...
    def render_profile(self, village, npc) -> None: ...  # type: ignore[no-untyped-def]
    def render_inspect(self, village, npc): ...  # type: ignore[no-untyped-def]
    def choose(self, count: int) -> int | None: ...
    def wait_any(self) -> None: ...
    def shutdown(self) -> None: ...
