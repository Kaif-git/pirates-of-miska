"""Pixel-positioned UI text rasterized with proportional Pillow fonts."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from omnisim.ui.render_settings import RenderSettings


Color = tuple[int, int, int]


def _rgb(color) -> Color:
    if isinstance(color, str):
        value = color.lstrip("#")
        return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))
    return tuple(max(0, min(255, int(component))) for component in color)


_FONT_PATH_CACHE: dict[tuple[str, bool], str] = {}


def resolve_font_path(family: str, *, bold: bool = False) -> str:
    """Resolve a common font family name or explicit font path to a TTF file.

    Resolutions are memoized: the lookup may walk the system font tree, and
    the UI asks for fonts hundreds of times per frame while it draws, so a
    cache here is the difference between a snappy modal and a slideshow.
    """
    cached = _FONT_PATH_CACHE.get((family, bold))
    if cached is not None and Path(cached).is_file():
        return cached
    candidate = Path(family).expanduser()
    if candidate.is_file():
        _FONT_PATH_CACHE[(family, bold)] = str(candidate)
        return str(candidate)

    windows = Path(r"C:\Windows\Fonts")
    linux = Path("/usr/share/fonts/truetype")
    names = {
        "Segoe UI": ("segoeuib.ttf" if bold else "segoeui.ttf",),
        "Segoe UI Symbol": ("seguisym.ttf",),
        "Arial": ("arialbd.ttf" if bold else "arial.ttf",),
        "Consolas": ("consolab.ttf" if bold else "consola.ttf",),
        "DejaVu Sans": ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",),
        "DejaVu Sans Mono": (
            "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf",),
        "JetBrains Mono": (
            "JetBrainsMono-Bold.ttf" if bold else "JetBrainsMono-Regular.ttf",),
    }
    filename_options = names.get(family, (family,))
    paths = [windows / name for name in filename_options]
    paths.extend(linux.rglob(name) for name in filename_options)
    for option in paths:
        candidates = option if not isinstance(option, Path) else (option,)
        for path in candidates:
            if path.is_file():
                return str(path)

    if family in ("Consolas", "DejaVu Sans Mono", "JetBrains Mono"):
        fallbacks = (
            windows / ("consolab.ttf" if bold else "consola.ttf"),
            linux / "dejavu" / ("DejaVuSansMono-Bold.ttf" if bold
                                else "DejaVuSansMono.ttf"),
            linux / "liberation" / ("LiberationMono-Bold.ttf" if bold
                                    else "LiberationMono-Regular.ttf"),
        )
    else:
        fallbacks = (
            windows / ("segoeuib.ttf" if bold else "segoeui.ttf"),
            windows / ("arialbd.ttf" if bold else "arial.ttf"),
            linux / "dejavu" / ("DejaVuSans-Bold.ttf" if bold
                                else "DejaVuSans.ttf"),
            linux / "dejavu" / "DejaVuSans.ttf",
        )
    for path in fallbacks:
        if path.is_file():
            _FONT_PATH_CACHE[(family, bold)] = str(path)
            return str(path)
    raise FileNotFoundError(
        f"No usable UI font found for {family!r}; pass a font file path")


class UIRenderer:
    """Draw ordinary text runs at pixel coordinates, never map-cell columns."""

    PANEL = (0, 0, 0)
    BAND = (41, 41, 41)

    RUN_CACHE_LIMIT = 768

    def __init__(self, settings: RenderSettings | None = None):
        self.settings = settings or RenderSettings()
        if self.settings.world_tileset == "everrogue":
            from omnisim.ui.everrogue import BACKGROUND
            self.PANEL = BACKGROUND
            self.BAND = (28, 73, 60)
        self._fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._glyph_fonts: dict[tuple[str, int], dict[str, object]] = {}
        # Pre-rendered text runs. The screens redraw the same words dozens of
        # times a second (labels, gauges, the log, every idle frame of the
        # tree and hearth stages), and PIL re-renders glyph masks on every
        # call — so each distinct run is drawn once onto a small transparent
        # tile and composited after that. Pixel-for-pixel the same picture
        # (verified against direct draw.text, including negative origins),
        # a fraction of the work.
        self._run_cache: dict[tuple,
                              tuple[Image.Image, Image.Image, int, int]] = {}
        self._family_paths: dict[tuple[str, bool], str] = {}
        self.font_path = resolve_font_path(self.settings.ui_font_family)
        self.mono_font_path = resolve_font_path(
            self.settings.ui_mono_font_family)

    def chrome(self, color):
        """Theme UI chrome only; retain semantic red/gold/green and art colors."""
        if self.settings.world_tileset != "everrogue":
            return color
        r, g, b, *alpha = color
        if max(r, g, b) - min(r, g, b) <= 4:
            if r < 24:
                rgb = self.PANEL
            elif r < 70:
                rgb = self.BAND
            elif r < 160:
                rgb = (round(r * .65), round(r * .85), round(r * .75))
            else:
                rgb = (round(r * .80), round(r * .92), round(r * .85))
        else:
            rgb = {
                (3, 16, 29): (10, 35, 31), (4, 27, 43): self.PANEL,
                (19, 62, 78): (43, 88, 71), (70, 130, 160): (81, 126, 107),
                (5, 12, 18): (10, 35, 31), (22, 34, 41): self.BAND,
                (71, 91, 100): (81, 126, 107),
            }.get((r, g, b), (r, g, b))
        return (*rgb, *alpha)

    def font(self, size: int | None = None, *, mono: bool = False,
             bold: bool = False):
        family = (self.settings.ui_mono_font_family if mono
                  else self.settings.ui_font_family)
        return self.font_for_family(family, size=size, bold=bold)

    def font_for_family(self, family: str, *, size: int | None = None,
                        bold: bool = False):
        # resolve_font_path probes the filesystem on every call; the answer
        # never changes at runtime, so memoize it per (family, bold) first.
        path_key = (family, bold)
        path = self._family_paths.get(path_key)
        if path is None:
            path = resolve_font_path(family, bold=bold)
            self._family_paths[path_key] = path
        pixel_size = max(6, round((size or self.settings.ui_font_size)
                                  * self.settings.ui_scale))
        key = (path, pixel_size)
        if key not in self._fonts:
            self._fonts[key] = ImageFont.truetype(path, pixel_size)
        return self._fonts[key]

    def font_for_character(self, character: str, font):
        """Choose the first cached UI/symbol font that contains a glyph.

        The answer is memoized per (font file, size, character): the probe
        rasterizes masks to compare against the .notdef box, and the UI asks
        about the same handful of glyphs thousands of times a second — the
        probe belongs in a cache, not on the hot path.
        """
        key = (str(getattr(font, "path", "")), int(getattr(font, "size", 0)))
        cache = self._glyph_fonts.setdefault(key, {})
        chosen = cache.get(character)
        if chosen is None:
            chosen = self._probe_font_for_character(character, font)
            cache[character] = chosen
        return chosen

    def _probe_font_for_character(self, character: str, font):
        missing = bytes(font.getmask("\U0010FFFF"))
        if bytes(font.getmask(character)) != missing:
            return font
        families = ("Segoe UI Symbol", self.settings.ui_mono_font_family,
                    "DejaVu Sans")
        seen = {str(getattr(font, "path", ""))}
        for family in families:
            try:
                fallback = self.font_for_family(family, size=font.size)
            except (FileNotFoundError, OSError):
                continue
            path = str(getattr(fallback, "path", ""))
            if path in seen:
                continue
            seen.add(path)
            if bytes(fallback.getmask(character)) != bytes(
                    fallback.getmask("\U0010FFFF")):
                return fallback
        return font

    def measure(self, text: str, size: int | None = None, *,
                mono: bool = False, bold: bool = False) -> float:
        return float(self.font(size, mono=mono, bold=bold).getlength(text))

    def line_height(self, size: int | None = None) -> int:
        font = self.font(size)
        ascent, descent = font.getmetrics()
        return max(ascent + descent, round(font.size * 1.3))

    def new_overlay(self, width: int, height: int) -> Image.Image:
        return Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))

    def draw_text(self, image: Image.Image, text: str, *, x: int, y: int,
                  size: int | None = None, color: Color = (220, 220, 220),
                  bold: bool = False, mono: bool = False,
                  max_width: int | None = None,
                  align: str = "left") -> tuple[int, int]:
        """Draw a complete text run using actual font advances and optional wrap."""
        draw = ImageDraw.Draw(image)
        font = self.font(size, mono=mono, bold=bold)
        rows = self.wrap_text(str(text), font, max_width) if max_width else [str(text)]
        row_height = self.line_height(size)
        max_advance = 0
        for index, row in enumerate(rows):
            advance = round(font.getlength(row))
            if align == "center" and max_width is not None:
                row_x = x + max(0, (max_width - advance) // 2)
            elif align == "right" and max_width is not None:
                row_x = x + max(0, max_width - advance)
            else:
                row_x = x
            max_advance = max(max_advance, self._draw_run(
                draw, row, x=row_x, y=y + index * row_height, font=font,
                color=_rgb(color), image=image))
        return max_advance, len(rows) * row_height

    def _run_tile(self, text: str, font, color: Color):
        """One cached tile per (text, color, font): the run drawn once onto a
        transparent tile, kept as a solid-color image plus its glyph-coverage
        mask, plus the paste offset that reproduces an anchor='lt' draw
        exactly. Pasting with that mask is the same blend draw.text performs,
        pixel for pixel, over any background — opaque or semi-transparent."""
        key = (text, color, str(getattr(font, "path", "")),
               int(getattr(font, "size", 0)))
        cached = self._run_cache.get(key)
        if cached is not None:
            return cached
        pad = 2
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        left, top, right, bottom = probe.textbbox(
            (0, 0), text, font=font, anchor="lt")
        tile = Image.new("RGBA",
                         (max(1, right - left + pad * 2),
                          max(1, bottom - top + pad * 2)), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text(
            (pad - left, pad - top), text, font=font, fill=(*color, 255),
            anchor="lt")
        solid = Image.new("RGBA", tile.size, (*color, 255))
        mask = tile.getchannel("A")
        if len(self._run_cache) > self.RUN_CACHE_LIMIT:
            self._run_cache.clear()
        self._run_cache[key] = (solid, mask, left - pad, top - pad)
        return self._run_cache[key]

    def _draw_run(self, draw, text: str, *, x: int, y: int, font,
                  color: Color, image: Image.Image | None = None) -> int:
        """Draw per-glyph only when the UI font lacks a Unicode character.

        Same-font runs go through the cached tile (see ``_run_tile``); a
        missing target image falls back to drawing straight onto the draw
        target, exactly as before."""
        color = self.chrome(color)
        cursor = int(x)
        run_font = font
        run_text = ""

        def flush() -> None:
            nonlocal cursor, run_text
            if not run_text:
                return
            if image is not None:
                solid, mask, paste_dx, paste_dy = self._run_tile(
                    run_text, run_font, color)
                image.paste(solid, (cursor + paste_dx, y + paste_dy),
                            mask=mask)
            else:
                draw.text((cursor, y), run_text, font=run_font,
                          fill=(*color, 255), anchor="lt")
            cursor += round(run_font.getlength(run_text))
            run_text = ""

        for character in text:
            chosen = (font if character.isspace()
                      else self.font_for_character(character, font))
            if chosen is not run_font:
                flush()
                run_font = chosen
            run_text += character
        flush()
        return cursor - int(x)

    WRAP_CACHE_LIMIT = 512

    @classmethod
    def wrap_text(cls, text: str, font, max_width: int | None) -> list[str]:
        if not max_width or max_width < 1:
            return text.splitlines() or [""]
        # The same rows re-wrap every frame of an animated screen (the words
        # did not move); keep the wrap result per (text, width, font size).
        # Callers may mutate the returned list, so hand out a fresh copy of
        # an immutable cached tuple.
        key = (text, max_width, str(getattr(font, "path", "")),
               int(getattr(font, "size", 0)))
        cached = cls._wrap_cache.get(key)
        if cached is None:
            cached = tuple(cls._wrap_text_uncached(text, font, max_width))
            if len(cls._wrap_cache) > cls.WRAP_CACHE_LIMIT:
                cls._wrap_cache.clear()
            cls._wrap_cache[key] = cached
        return list(cached)

    _wrap_cache: dict[tuple, tuple[str, ...]] = {}

    @staticmethod
    def _wrap_text_uncached(text: str, font, max_width: int | None) -> list[str]:
        result: list[str] = []
        for paragraph in text.splitlines() or [""]:
            if "  " in paragraph:
                # ASCII art (the tree portraits): re-flowing it would strip
                # the very spaces that hold the picture together — keep the
                # line verbatim.
                result.append(paragraph)
                continue
            words = paragraph.split()
            if not words:
                result.append("")
                continue
            line = ""
            for word in words:
                candidate = word if not line else f"{line} {word}"
                if line and font.getlength(candidate) > max_width:
                    result.append(line)
                    line = word
                else:
                    line = candidate
                while font.getlength(line) > max_width and len(line) > 1:
                    split_at = len(line) - 1
                    while split_at > 1 and font.getlength(line[:split_at]) > max_width:
                        split_at -= 1
                    result.append(line[:split_at])
                    line = line[split_at:]
            result.append(line)
        return result

    def draw_sidebar(self, image: Image.Image, *, x: int, y: int,
                     width: int, height: int, lines) -> None:
        """Paint colored sidebar segments in normal text layout and pixel space."""
        width, height = max(1, width), max(1, height)
        panel = Image.new("RGBA", (width, height), (*self.PANEL, 255))
        draw = ImageDraw.Draw(panel)
        draw.rectangle((0, 0, width - 1, height - 1),
                       outline=self.chrome((145, 145, 145, 255)), width=1)
        font = self.font(mono=True)
        line_height = self.line_height()
        pad_x, pad_y = 12, 10
        inner_width = max(1, width - pad_x * 2)
        y_pos = pad_y
        for segments in lines:
            if y_pos + line_height > height - pad_y:
                break
            if any(_rgb(background) in (self.BAND, (41, 41, 41), (30, 30, 62))
                   for _text, _fg, background in segments):
                draw.rectangle((0, y_pos, width, y_pos + line_height),
                               fill=(*self.BAND, 255))
            x_pos = pad_x
            for text, foreground, background in segments:
                text = str(text)
                fg = _rgb(foreground)
                source_bg = (tuple(background) if not isinstance(background, str)
                             else None)
                bg = self.BAND if source_bg == (30, 30, 62) else self.PANEL
                if not text:
                    continue
                max_run = max(1, width - pad_x - x_pos)
                leading = len(text) - len(text.lstrip(" "))
                trailing = len(text) - len(text.rstrip(" "))
                wrapped = self.wrap_text(text.strip(" "), font, max_run)
                if not text.strip(" "):
                    wrapped = [text]
                elif leading:
                    wrapped[0] = (" " * leading) + wrapped[0]
                if trailing and text.strip(" "):
                    wrapped[-1] += " " * trailing
                for run_index, run in enumerate(wrapped):
                    if run_index:
                        y_pos += line_height
                        if y_pos + line_height > height - pad_y:
                            break
                        x_pos = pad_x
                    run_width = min(max_run, max(0, round(font.getlength(run))))
                    if bg != self.PANEL and bg != self.BAND:
                        draw.rectangle((x_pos - 2, y_pos, x_pos + run_width + 2,
                                        y_pos + line_height), fill=(*bg, 255))
                    x_pos += self._draw_run(
                        draw, run, x=x_pos, y=y_pos, font=font, color=fg,
                        image=panel)
            y_pos += line_height
        image.alpha_composite(panel, (x, y))

    def draw_status(self, image: Image.Image, *, width: int, y: int,
                    height: int, lines: list[str]) -> None:
        """Draw map-area status/control lines independently of map zoom."""
        if width <= 0 or height <= 0:
            return
        panel = Image.new("RGBA", (width, height), (*self.PANEL, 250))
        ImageDraw.Draw(panel).line((0, 0, width - 1, 0),
                                  fill=self.chrome((145, 145, 145, 255)))
        pad = 8
        line_height = self.line_height(12)
        for index, text in enumerate(lines[:max(1, height // line_height)]):
            self.draw_text(panel, text, x=pad, y=pad + index * line_height,
                           size=12, color=(150, 150, 150),
                           max_width=max(1, width - pad * 2))
        image.alpha_composite(panel, (0, y))
