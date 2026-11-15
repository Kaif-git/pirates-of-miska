"""Fixed-cell world layer rendered through tcod's cached SDL console atlas."""
from __future__ import annotations

import tcod


def visible_cell_bounds(left: float, top: float, width: float, height: float,
                        cell_width: int, cell_height: int,
                        map_width: int, map_height: int,
                        margin: int = 1) -> tuple[int, int, int, int]:
    """Return clipped (x0, y0, x1, y1) cell bounds for a pixel viewport."""
    if cell_width <= 0 or cell_height <= 0:
        raise ValueError("cell dimensions must be positive")
    x0 = max(0, int(left // cell_width) - margin)
    y0 = max(0, int(top // cell_height) - margin)
    x1 = min(map_width, int((left + width) // cell_width) + margin + 1)
    y1 = min(map_height, int((top + height) // cell_height) + margin + 1)
    return x0, y0, x1, y1


class WorldRenderer:
    """One tcod console cell per map cell, using font glyphs or pixel sprites.

    The console atlas rasterizes glyphs and optional sprites; SDL returns its
    rendered texture for composition with the pixel-positioned UI layer.
    """

    def __init__(self, context: tcod.context.Context,
                 console: tcod.console.Console,
                 sprite_codepoints: dict[str, int] | None = None,
                 background=(0, 0, 0)):
        atlas = context.sdl_atlas
        if atlas is None:
            raise RuntimeError("the tcod SDL renderer did not expose a tileset atlas")
        self.context = context
        self.console = console
        self.sprite_codepoints = sprite_codepoints or {}
        self.background = background
        self._tileset = atlas.tileset
        self._faded_codepoints = {}
        self._console_renderer = tcod.render.SDLConsoleRender(atlas)

    def clear(self) -> None:
        self.console.clear(bg=self.background)

    def draw_cell(self, x: int, y: int, glyph: str = ".",
                  fg=(220, 220, 220), bg=None,
                  sprite: str | None = None, sprite_fade: float | None = None) -> None:
        """Write one glyph or optional style sprite to a fixed map cell."""
        codepoint = self.sprite_codepoints.get(sprite) if sprite else None
        if codepoint is not None and sprite_fade is not None:
            from omnisim.ui.everrogue import FADE_STEPS, fade_level, fade_sprite
            level = fade_level(sprite_fade)
            if level < FADE_STEPS:
                key = (codepoint, level)
                if key not in self._faded_codepoints:
                    # Supplementary private-use area, away from fonts/source art.
                    faded = 0xF0000 + len(self._faded_codepoints)
                    self._tileset[faded] = fade_sprite(
                        self._tileset[codepoint], level / FADE_STEPS)
                    self._faded_codepoints[key] = faded
                codepoint = self._faded_codepoints[key]
        cell_glyph = chr(codepoint) if codepoint is not None else str(glyph)[:1]
        if bg is None:
            bg = getattr(self, "background", (0, 0, 0))
        self.console.print(int(x), int(y), cell_glyph, fg=fg, bg=bg)

    def render_texture(self):
        """Return the cached SDL texture without presenting the window."""
        return self._console_renderer.render(self.console)
