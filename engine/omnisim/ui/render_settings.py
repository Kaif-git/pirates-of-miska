"""Independent world-cell and pixel-UI typography settings."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RenderSettings:
    """World zoom and ordinary UI scaling are independent."""

    world_font_family: str = "Consolas"
    world_font_size: int = 16
    world_cell_width: int | None = None
    world_cell_height: int | None = None
    world_tileset: str = "ascii"
    movement_animation_ms: int = 95
    ui_font_family: str = "Consolas"
    ui_font_size: int = 15
    ui_mono_font_family: str = "Consolas"
    ui_scale: float = 1.0
    world_zoom: float = 1.0

    def __post_init__(self) -> None:
        if self.world_font_size < 6 or self.ui_font_size < 6:
            raise ValueError("font sizes must be at least 6 px")
        if self.world_cell_width is not None and self.world_cell_width < 1:
            raise ValueError("world_cell_width must be positive")
        if self.world_cell_height is not None and self.world_cell_height < 1:
            raise ValueError("world_cell_height must be positive")
        if self.world_tileset not in ("ascii", "retrodays", "kenney", "everrogue"):
            raise ValueError("world_tileset must be ascii, retrodays, kenney, or everrogue")
        if self.movement_animation_ms < 0:
            raise ValueError("movement_animation_ms cannot be negative")
        if self.ui_scale <= 0 or self.world_zoom <= 0:
            raise ValueError("render scales must be positive")
