"""Pixel-space dialogs, menus, and tooltips composited over the ASCII world."""
from __future__ import annotations

import re
import time

from PIL import Image, ImageDraw

from omnisim.ui.ui_renderer import UIRenderer, _rgb


class OverlayRenderer:
    """Draw a scrollable, screen-space modal without changing map cell layout."""

    GOOD = (105, 210, 135)
    WARNING = (235, 195, 80)
    DANGER = (235, 100, 95)
    INFO = (100, 190, 220)
    MUTED = (105, 105, 105)
    MEASURE = (230, 190, 80)

    def __init__(self, ui: UIRenderer):
        self.ui = ui
        self.lines = []
        self.footer = ""
        self.choice_start: int | None = None
        self.choice_count = 0
        self.selected_index = 0
        self.scroll_offset = 0
        self.choice_rects: list[tuple[int, tuple[int, int, int, int]]] = []
        # Pre-colored art rows (the tree stage's drawing) as (row_index, rect)
        # — mouse-only aim targets; the keyboard never selects them.
        self.art_rects: list[tuple[int, tuple[int, int, int, int]]] = []
        self.columns: tuple[list[str], list[str]] | None = None
        self.column_titles = ("PACK", "STORAGE")
        self.column_pane = 0
        self.column_index = 0
        # Per-pane masks of selectable row indices; None means every row.
        self.column_selectable: tuple[frozenset[int] | None,
                                      frozenset[int] | None] = (None, None)
        self.column_title = ""
        self.column_details = ""
        self.inventory_mode = False
        self.inventory_hint = ""
        self.inventory_stats = ""
        self.inventory_filter = ""
        self.inventory_filter_edit = False
        self.inventory_view_mode = "categories"
        self.inventory_navigation_mode = "item"
        self.screen_kind = "none"
        self.fishing_rows: list[list[tuple[str, tuple, tuple]]] = []
        self.fishing_title = ""
        self.fishing_status = ""
        self.fishing_map_rect: tuple[int, int, float, int, int] | None = None
        self.fishing_popup = ""
        self.fishing_popup_id = None
        self.fishing_popup_started = 0.0
        self.anvil_hud = None
        self.anvil_control_rects: dict[str, tuple[int, int, int, int]] = {}
        self.anvil_slider_rect: tuple[int, int, int, int] | None = None
        self.active = False

    def open_fishing_scene(self, title: str, rows, status: str, footer: str,
                           popup: str = "", popup_id=None) -> None:
        """Open the full-screen colored ASCII water field."""
        self.fishing_rows = list(rows)
        self.fishing_title = title
        self.fishing_status = status
        self.footer = footer
        if popup != self.fishing_popup or popup_id != self.fishing_popup_id:
            self.fishing_popup = popup
            self.fishing_popup_id = popup_id
            self.fishing_popup_started = time.perf_counter() if popup else 0.0
        self.columns = None
        self.screen_kind = "fishing"
        self.fishing_map_rect = None
        self.active = True

    def open_anvil_scene(self, title: str, rows, status: str, footer: str,
                         hud: dict) -> None:
        """Open the forge canvas with live meters and mouse-operable controls."""
        self.open_fishing_scene(title, rows, status, footer)
        self.screen_kind = "anvil"
        self.anvil_hud = hud
        self.anvil_control_rects = {}
        self.anvil_slider_rect = None

    def _draw_fishing_scene(self, image: Image.Image) -> Image.Image:
        result = image.copy()
        draw = ImageDraw.Draw(result)
        margin = 14
        panel = (margin, margin, image.width - margin, image.height - margin)
        draw.rounded_rectangle(panel, radius=10, fill=self.ui.chrome((3, 16, 29, 252)),
                               outline=self.ui.chrome((70, 130, 160, 255)), width=2)
        width, height = panel[2] - panel[0], panel[3] - panel[1]
        size = min(18 if self.screen_kind == "anvil" else 19,
                   max(8, int((height - (210 if self.screen_kind == "anvil" else 100))
                              / (25 if self.screen_kind == "anvil" else 29))))
        font = self.ui.font(size, mono=True)
        line_h = max(size + 2, self.ui.line_height(size))
        char_w = font.getlength("M")
        while size > 8 and char_w * 78 > width - 32:
            size -= 1
            font = self.ui.font(size, mono=True)
            line_h = max(size + 2, self.ui.line_height(size))
            char_w = font.getlength("M")
        map_w = round(char_w * 78)
        map_h = line_h * len(self.fishing_rows)
        title_y = panel[1] + 12
        self.ui.draw_text(result, self.fishing_title, x=panel[0] + 18,
                          y=title_y, size=18, color=(225, 240, 245), bold=True)
        self.ui.draw_text(result, self.fishing_status, x=panel[0] + 18,
                          y=title_y + 25, size=12, color=(151, 198, 213),
                          max_width=width - 36)
        x0 = panel[0] + (width - map_w) // 2
        y0 = title_y + 52
        if self.screen_kind == "anvil" and self.anvil_hud:
            shake = float(self.anvil_hud.get("shake", 0))
            if shake > 0:
                phase = float(self.anvil_hud.get("shake_phase", 0))
                x0 += 2 if int(phase) % 2 else -2
                y0 += 1 if int(phase * 1.7) % 2 else -1
        self.fishing_map_rect = (x0, y0, char_w, line_h, len(self.fishing_rows))
        map_bottom = min(panel[3] - 37, y0 + map_h)
        draw.rectangle((x0 - 5, y0 - 3, x0 + map_w + 5, map_bottom + 2),
                       fill=self.ui.chrome((4, 27, 43, 255)),
                       outline=self.ui.chrome((19, 62, 78, 255)))
        for row_index, segments in enumerate(self.fishing_rows):
            y = y0 + row_index * line_h
            if y + line_h > map_bottom:
                break
            x = x0
            for text, foreground, background in segments:
                if background is not None:
                    draw.rectangle((round(x), y, round(x + len(text) * char_w),
                                    y + line_h), fill=(*background, 255))
                draw.text((round(x), y), text, font=font,
                          fill=(*foreground, 255), stroke_width=0)
                x += len(text) * char_w
        if self.screen_kind == "anvil" and self.anvil_hud is not None:
            self._draw_anvil_hud(result, draw, panel, x0, map_bottom)
        self.ui.draw_text(result, self.footer, x=panel[0] + 18,
                          y=panel[3] - 27, size=11, color=(169, 184, 194),
                          max_width=width - 36)
        if self.fishing_popup:
            age = time.perf_counter() - self.fishing_popup_started
            if age < 2.4:
                slide = min(1.0, age / .22)
                rise = round((1.0 - slide) * 22)
                alpha = round(255 * min(1.0, (2.4 - age) / .45))
                popup_font = self.ui.font(13)
                popup_width = min(width - 30,
                                  round(popup_font.getlength(self.fishing_popup)) + 30)
                popup_height = 34
                popup_x = (image.width - popup_width) // 2
                popup_y = panel[3] - 68 + rise
                popup = Image.new("RGBA", (popup_width, popup_height),
                                  (5, 12, 18, 0))
                popup_draw = ImageDraw.Draw(popup)
                popup_draw.rounded_rectangle(
                    (0, 0, popup_width - 1, popup_height - 1), radius=7,
                    fill=self.ui.chrome((5, 12, 18, round(.94 * alpha))),
                    outline=(255, 238, 94, alpha), width=1)
                popup_draw.text((15, 8), self.fishing_popup, font=popup_font,
                                fill=(240, 246, 239, alpha))
                result.alpha_composite(popup, (popup_x, popup_y))
        return result

    def _draw_anvil_hud(self, image, draw, panel, map_x: int,
                        map_bottom: int) -> None:
        hud = self.anvil_hud or {}
        left, right = panel[0] + 20, panel[2] - 20
        width = max(1, right - left)
        meter_y = map_bottom + 7
        self.anvil_control_rects = {}
        self.anvil_slider_rect = None

        def meter(label: str, value: float, y: int, color) -> None:
            value = max(0.0, min(1.0, float(value)))
            self.ui.draw_text(image, f"{label:9}", x=left, y=y, size=10,
                              color=(175, 190, 198), mono=True)
            bar_x, bar_y = left + 82, y + 2
            bar_w = max(40, width - 150)
            bar_h = 9
            draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
                                   radius=3, fill=self.ui.chrome((22, 34, 41)),
                                   outline=self.ui.chrome((71, 91, 100)), width=1)
            draw.rounded_rectangle((bar_x + 1, bar_y + 1,
                                    bar_x + 1 + round((bar_w - 2) * value), bar_y + bar_h - 1),
                                   radius=2, fill=color)
            if label == "HEAT":
                # Gold ticks bracket the forgiving working range without
                # hiding the current fill amount.
                start, end = bar_x + round(bar_w * .55), bar_x + round(bar_w * .82)
                draw.line((start, bar_y, start, bar_y + bar_h),
                          fill=(255, 220, 132), width=2)
                draw.line((end, bar_y, end, bar_y + bar_h),
                          fill=(255, 220, 132), width=2)
            label_value = f"{round(value * 100):3}%"
            self.ui.draw_text(image, label_value, x=bar_x + bar_w + 8, y=y,
                              size=10, color=(221, 228, 222), mono=True)

        temp = float(hud.get("temperature", 20))
        meter("HEAT", temp / 1350, meter_y, (255, 145, 45))
        meter_y += 15
        meter("SHAPE", hud.get("shape", 0) / 100, meter_y, (104, 188, 220))
        meter_y += 15
        meter("INTEGRITY", hud.get("integrity", 100) / 100, meter_y,
              (125, 205, 132))
        meter_y += 15
        meter("FINISH", hud.get("finish", 100) / 100, meter_y, (200, 164, 230))
        meter_y += 16
        if hud.get("fusion_visible"):
            slider_x, slider_y = left + 122, meter_y + 1
            slider_w = max(70, width - 260)
            self.ui.draw_text(image, "FUSION TEST", x=left, y=meter_y - 1,
                              size=10, color=(181, 158, 224), mono=True)
            draw.rounded_rectangle((slider_x, slider_y + 3, slider_x + slider_w,
                                    slider_y + 11), radius=4, fill=(37, 31, 50),
                                   outline=(128, 100, 168), width=1)
            knob = slider_x + round(slider_w * max(0, min(1,
                                                       float(hud.get("fusion_power", 0)) / 100)))
            draw.ellipse((knob - 6, slider_y, knob + 6, slider_y + 14),
                         fill=(202, 177, 255), outline=(238, 225, 255), width=1)
            self.ui.draw_text(image, f"{round(float(hud.get('fusion_power', 0))):3}",
                              x=slider_x + slider_w + 10, y=meter_y - 1,
                              size=10, color=(208, 195, 229), mono=True)
            self.anvil_slider_rect = (slider_x, slider_y - 3,
                                      slider_x + slider_w, slider_y + 17)

        button_y = min(meter_y + (25 if hud.get("fusion_visible") else 8),
                       panel[3] - 61)
        controls = (("heat", "HEATING  H" if hud.get("heating") else "HEAT  H"),
                    ("strike", "HAMMER  SPACE"),
                    ("quench", "QUENCH  Q"), ("stop", "STOP  ESC"))
        button_font = self.ui.font(11, mono=True)
        widths = [round(button_font.getlength(label)) + 24 for _, label in controls]
        gap = 10
        total = sum(widths) + gap * (len(widths) - 1)
        bx = panel[0] + max(12, (panel[2] - panel[0] - total) // 2)
        for (key, label), button_w in zip(controls, widths):
            rect = (bx, button_y, bx + button_w, button_y + 30)
            fill = (86, 53, 35) if key == "heat" else (
                (82, 64, 39) if key == "quench" else (14, 29, 37))
            outline = (255, 153, 62) if key == "heat" else (
                (238, 183, 83) if key == "quench" else (76, 120, 137))
            draw.rounded_rectangle(rect, radius=5, fill=fill, outline=outline, width=1)
            tw = round(button_font.getlength(label))
            draw.text((bx + (button_w - tw) // 2, button_y + 8), label,
                      font=button_font, fill=(231, 236, 231))
            self.anvil_control_rects[key] = rect
            bx += button_w + gap

    def open(self, lines, *, footer: str = "", choice_start: int | None = None,
             choice_count: int = 0, selected_index: int = 0) -> None:
        self.lines = list(lines)
        self.footer = footer
        self.choice_start = choice_start
        self.choice_count = max(0, int(choice_count))
        self.selected_index = max(0, min(max(0, self.choice_count - 1), int(selected_index)))
        self.columns = None
        self.scroll_offset = 0
        self.screen_kind = "list"
        self.active = True

    def open_columns(self, title: str, left_title: str, right_title: str,
                     left_rows: list[str], right_rows: list[str], pane: int,
                     selected_index: int, details: str = "", footer: str = "",
                     *, screen_kind: str = "columns",
                     left_selectable=None, right_selectable=None) -> None:
        """Two-column browser.

        The optional selectable masks name which rows of each pane respond to
        the mouse and keyboard; rows outside the mask are plain information
        (facts, notes) and never become the highlighted choice.
        """
        self.columns = (list(left_rows), list(right_rows))
        self.column_titles = (left_title, right_title)
        self.column_pane = int(bool(pane))
        self.column_index = max(0, int(selected_index))
        self.column_title = title
        self.column_details = details
        self.footer = footer
        self.inventory_mode = False
        self.screen_kind = screen_kind
        self.column_selectable = (
            None if left_selectable is None else frozenset(left_selectable),
            None if right_selectable is None else frozenset(right_selectable))
        self.choice_rects.clear()
        self.art_rects.clear()
        self.active = True

    def open_inventory(self, title: str, hint: str, left_title: str, right_title: str,
                       left_rows: list[dict], right_rows: list[dict], pane: int,
                       left_index: int, right_index: int, details: str,
                       stats: str, filter_text: str = "", view_mode: str = "categories",
                       navigation_mode: str = "item", footer: str = "") -> None:
        """Open the CDDA-inspired character inventory selector."""
        self.column_selectable = (None, None)
        self.columns = (list(left_rows), list(right_rows))
        self.column_titles = (left_title, right_title)
        self.column_pane = int(bool(pane))
        self.column_index = max(0, int(left_index if pane == 0 else right_index))
        self.column_title = title
        self.column_details = details
        self.inventory_hint = hint
        self.inventory_stats = stats
        self.inventory_filter = filter_text
        self.inventory_filter_edit = False
        self.inventory_view_mode = view_mode
        self.inventory_navigation_mode = navigation_mode
        self.footer = footer
        self.inventory_mode = True
        self.screen_kind = "inventory"
        self.choice_rects.clear()
        self.active = True

    def update_inventory_filter(self, value: str) -> None:
        self.inventory_filter = value
        self.inventory_filter_edit = True

    def close(self) -> None:
        self.active = False
        self.lines = []
        self.columns = None
        self.inventory_mode = False
        self.inventory_filter_edit = False
        self.choice_rects.clear()
        self.art_rects.clear()
        self.screen_kind = "none"

    def scroll(self, amount: int) -> None:
        self.scroll_offset = max(0, self.scroll_offset + int(amount))

    def select(self, index: int) -> None:
        if self.choice_count:
            self.selected_index = max(0, min(self.choice_count - 1, int(index)))

    @classmethod
    def _semantic_color(cls, text: str, default: tuple[int, int, int]):
        normalized = text.strip().casefold()
        wear = re.search(r"(?:condition|durability):\s*(\d{1,3})%", normalized)
        if wear:
            value = max(0, min(100, int(wear.group(1))))
            return cls.GOOD if value >= 81 else cls.WARNING if value >= 41 else cls.DANGER
        for marker, color in (("· || ·", cls.GOOD), ("· |\\ ·", cls.WARNING),
                              ("· \\\\ ·", cls.WARNING), ("· \\- ·", cls.DANGER),
                              ("· -- ·", cls.DANGER)):
            if marker in normalized:
                return color
        if normalized.startswith("pack fit: one item fits"):
            return cls.WARNING
        if normalized.startswith(("[ready]", "[fits]", "status: ready", "craftable",
                                 "pack fit: full stack fits")):
            return cls.GOOD
        if normalized.startswith(("[missing]", "status: missing", "[blocked]", "[no fit]")) or any(
                marker in normalized for marker in
                ("over capacity", "too large", "too big", "cannot fit", "no fit",
                 "does not fit", "missing:")):
            return cls.DANGER
        if normalized.startswith(("[partial]", "[warning]", "[near full]")) or any(
                marker in normalized for marker in
                ("near capacity", "not yet learned", "too tired")):
            return cls.WARNING
        if normalized.startswith(("[info]", "[session]", "[fits]")):
            return cls.INFO if normalized.startswith(("[info]", "[session]")) else cls.GOOD
        return default

    @classmethod
    def _capacity_color(cls, current: float, capacity: float):
        if current > capacity or (capacity == 0 and current > 0):
            return cls.DANGER
        if current <= 0:
            return cls.MUTED
        if capacity > 0 and current / capacity >= 0.85:
            return cls.WARNING
        return cls.GOOD

    def _draw_detail_line(self, image: Image.Image, text: str, *, x: int, y: int,
                           color: tuple[int, int, int], max_width: int,
                           mono: bool = False) -> None:
        if color == (8, 8, 8) and self.ui.settings.world_tileset == "everrogue":
            # Semantic mint/gold on a pale mint selection has poor contrast.
            # Keep the status words but use dark ink on the selected row.
            self.ui.draw_text(image, text, x=x, y=y, color=color,
                              max_width=max_width, mono=mono)
            return
        if "  " in text:
            # ASCII art: mono face at fixed cell advance and no re-flow, so
            # the picture keeps every column it was drawn with.
            mono_font = self.ui.font(mono=True)
            keep = max(1, int(max_width // max(1.0, mono_font.getlength("M"))))
            self.ui.draw_text(image, text[:keep], x=x, y=y, mono=True,
                              color=color)
            return
        status_color = self._semantic_color(text, color)
        if status_color != color:
            self.ui.draw_text(image, text, x=x, y=y, color=status_color,
                              max_width=max_width, mono=mono)
            return
        pattern = re.compile(r"(×\s*\d+|\d+(?:\.\d+)?\s*(?:g|kg|ml|l|xp|ticks?))", re.I)
        cursor = x
        for piece in pattern.split(text):
            if not piece:
                continue
            value_piece = bool(pattern.fullmatch(piece))
            available = max(1, x + max_width - cursor)
            value_color = self.MEASURE
            if value_piece:
                number = re.search(r"\d+(?:\.\d+)?", piece)
                if number is not None and float(number.group()) <= 0:
                    value_color = self.MUTED
            advance, _height = self.ui.draw_text(
                image, piece, x=cursor, y=y,
                color=value_color if value_piece else color,
                max_width=available, mono=mono)
            cursor += advance

    def _draw_segment_line(self, image: Image.Image, segments, *, x: int,
                           y: int, line_height: int, max_width: int) -> None:
        """Draw one pre-colored (text, fg, bg) run row — the tree stage's art.

        Runs advance by the mono face's own character cell, so every glyph
        keeps the exact column the grid gave it; backgrounds paint first so
        highlights and the blinking aim cursor read as part of the drawing.
        """
        mono = self.ui.font(mono=True)
        draw = ImageDraw.Draw(image)
        cursor = float(x)
        limit = x + max(1, max_width)
        for text, fg, bg in segments:
            advance = mono.getlength(text)
            if bg is not None and advance > 0:
                draw.rectangle((cursor, y, min(limit, cursor + advance),
                                y + line_height), fill=(*bg, 255))
            if cursor >= limit:
                break
            clipped = text
            if cursor + advance > limit:
                keep = max(0, int((limit - cursor) // max(1.0, mono.size * 0.6)))
                clipped = text[:keep]
            if clipped.strip():
                self.ui.draw_text(image, clipped, x=cursor, y=y, mono=True,
                                  color=fg)
            cursor += advance

    def _draw_inventory_stats(self, image: Image.Image, text: str, *, x: int,
                              y: int, color: tuple[int, int, int],
                              max_width: int, font_size: int) -> None:
        ratio = re.compile(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)(\s*(?:g|ml))", re.I)
        cursor = x
        for piece in ratio.split(text):
            if not piece:
                continue
            match = ratio.fullmatch(piece)
            if match is None:
                advance, _height = self.ui.draw_text(
                    image, piece, x=cursor, y=y, size=font_size,
                    color=color, mono=True,
                    max_width=max(1, x + max_width - cursor))
                cursor += advance
                continue
            current, capacity = float(match.group(1)), float(match.group(2))
            current_color = self._capacity_color(current, capacity)
            advance, _height = self.ui.draw_text(
                image, match.group(1), x=cursor, y=y, size=font_size,
                color=current_color, mono=True,
                max_width=max(1, x + max_width - cursor))
            cursor += advance
            advance, _height = self.ui.draw_text(
                image, "/" + match.group(2) + match.group(3),
                x=cursor, y=y, size=font_size, color=color, mono=True,
                max_width=max(1, x + max_width - cursor))
            cursor += advance

    def draw(self, image: Image.Image) -> Image.Image:
        """Composite the active modal and return the same RGBA frame."""
        if not self.active or image.width < 1 or image.height < 1:
            return image
        if self.screen_kind in {"fishing", "anvil"}:
            return self._draw_fishing_scene(image)
        if self.columns is not None:
            if self.inventory_mode:
                return self._draw_inventory(image)
            return self._draw_columns(image)
        result = image.copy()
        font = self.ui.font()
        line_height = self.ui.line_height()
        pad_x, pad_y = 18, 14
        max_panel_width = max(1, min(image.width - 24,
                                     round(image.width * 0.82)))
        min_panel_width = min(240, max_panel_width)
        inner_width = max(1, max_panel_width - pad_x * 2)

        wrapped = []
        for line_index, segments in enumerate(self.lines):
            line_rows: list[list[tuple[str, tuple[int, int, int], tuple[int, int, int]]]] = [[]]
            for text, fg, bg in segments:
                rows = self.ui.wrap_text(str(text), font, inner_width)
                for row_index, row in enumerate(rows):
                    while len(line_rows) <= row_index:
                        line_rows.append([])
                    line_rows[row_index].append((
                        row, self._semantic_color(row, _rgb(fg)), (12, 12, 12)))
            for row_index, pieces in enumerate(line_rows):
                wrapped.append((line_index, row_index, pieces or [
                    ("", (220, 220, 220), (12, 12, 12))]))

        visible_lines = max(1, (image.height - 2 * pad_y - 55) // line_height)
        max_scroll = max(0, len(wrapped) - visible_lines)
        if self.choice_start is not None and self.choice_count:
            selected_row = self.choice_start + self.selected_index
            selected_line = next((index for index, row in enumerate(wrapped)
                                  if row[0] == selected_row and row[1] == 0), None)
            if selected_line is not None:
                if selected_line < self.scroll_offset:
                    self.scroll_offset = selected_line
                elif selected_line >= self.scroll_offset + visible_lines:
                    self.scroll_offset = selected_line - visible_lines + 1
        self.scroll_offset = min(self.scroll_offset, max_scroll)
        shown = wrapped[self.scroll_offset:self.scroll_offset + visible_lines]
        body_width = min(inner_width, max((
            sum(round(font.getlength(text)) for text, _fg, _bg in pieces)
            for _index, _row, pieces in shown), default=1))
        footer = self.footer or "Esc closes · arrows or wheel scroll"
        footer_width = round(self.ui.font(12).getlength(footer))
        panel_width = min(max_panel_width,
                          max(min_panel_width, body_width + pad_x * 2,
                              min(max_panel_width, footer_width + pad_x * 2)))
        body_height = len(shown) * line_height
        footer_height = self.ui.line_height(12) + 10
        panel_height = max(1, min(image.height - 24,
                                  body_height + 2 * pad_y + footer_height))
        x0 = max(0, (image.width - panel_width) // 2)
        y0 = max(0, (image.height - panel_height) // 2)
        panel = Image.new("RGBA", (panel_width, panel_height), self.ui.chrome((8, 8, 8, 250)))
        draw = ImageDraw.Draw(panel)
        draw.rectangle((0, 0, panel_width - 1, panel_height - 1),
                       outline=self.ui.chrome((190, 190, 190, 255)), width=1)
        self.choice_rects = []
        y = pad_y
        for original_index, row_index, pieces in shown:
            if self.choice_start is not None and row_index == 0:
                choice_index = original_index - self.choice_start
                if 0 <= choice_index < self.choice_count:
                    if choice_index == self.selected_index:
                        draw.rectangle((pad_x - 4, y - 1, panel_width - pad_x,
                        y + line_height - 1), fill=self.ui.chrome((235, 235, 235, 255)))
                    self.choice_rects.append((choice_index, (
                        x0 + pad_x - 4, y0 + y - 1,
                        x0 + panel_width - pad_x, y0 + y + line_height - 1)))
            x = pad_x
            is_selected_choice = (self.choice_start is not None and row_index == 0 and
                                  original_index - self.choice_start == self.selected_index)
            for text, fg, bg in pieces:
                is_art = "  " in text
                piece_font = self.ui.font(mono=True) if is_art else font
                text_width = min(inner_width, round(piece_font.getlength(text)))
                if bg not in (UIRenderer.PANEL, UIRenderer.BAND, (12, 12, 12)):
                    draw.rectangle((x, y, min(panel_width - pad_x, x + text_width),
                                    y + line_height), fill=self.ui.chrome((*bg, 255)))
                rendered_width, _ = self.ui.draw_text(
                    panel, text, x=x, y=y, mono=is_art,
                    color=(8, 8, 8) if is_selected_choice else fg)
                x += rendered_width
            y += line_height
        footer_font = self.ui.font(12)
        footer_y = panel_height - pad_y - self.ui.line_height(12)
        self.ui.draw_text(panel, footer, x=pad_x, y=footer_y, size=12,
                          color=(130, 130, 130),
                          max_width=max(1, panel_width - pad_x * 2))
        if max_scroll:
            counter = "%d/%d" % (self.scroll_offset + 1, max_scroll + 1)
            self.ui.draw_text(
                panel, counter,
                x=panel_width - pad_x - round(footer_font.getlength(counter)),
                y=footer_y, size=12, color=(155, 160, 175))
        result.alpha_composite(panel, (x0, y0))
        return result

    def _draw_columns(self, image: Image.Image) -> Image.Image:
        left_rows, right_rows = self.columns or ([], [])
        result = image.copy()
        font = self.ui.font()
        line_height = self.ui.line_height()
        pad_x, pad_y = 16, 14
        panel_width = min(image.width - 24, max(460, round(image.width * 0.9)))
        panel_height = min(image.height - 24, max(260, round(image.height * 0.82)))
        x0, y0 = (image.width - panel_width) // 2, (image.height - panel_height) // 2
        panel = Image.new("RGBA", (panel_width, panel_height), self.ui.chrome((8, 8, 8, 250)))
        draw = ImageDraw.Draw(panel)
        draw.rectangle((0, 0, panel_width - 1, panel_height - 1),
                       outline=self.ui.chrome((190, 190, 190, 255)), width=1)
        inner_width = panel_width - 2 * pad_x
        gap = 12
        column_width = (inner_width - gap) // 2
        # When a pane carries pre-colored art (the tree stage), size the
        # split to the content: the drawing keeps exactly the mono columns it
        # needs, and the text pane takes the rest — no orphan wrapped words.
        right_has_art = any(isinstance(row, list) for row in right_rows)
        left_has_art = any(isinstance(row, list) for row in left_rows)
        left_width, right_width = column_width, column_width
        if right_has_art or left_has_art:
            art_columns = max(
                (sum(len(text) for text, _fg, _bg in row)
                 for rows in (left_rows, right_rows)
                 for row in rows if isinstance(row, list)), default=0)
            art_px = round(art_columns * self.ui.font(mono=True).getlength("M")) + 56
            art_px = max(220, min(column_width, art_px))
            if right_has_art:
                right_width = art_px
                left_width = max(200, inner_width - gap - art_px)
            else:
                left_width = art_px
                right_width = max(200, inner_width - gap - art_px)
        pane_widths = (left_width, right_width)
        pane_x = (pad_x, pad_x + left_width + gap)
        title_y = pad_y
        self.ui.draw_text(panel, self.column_title, x=pad_x, y=title_y,
                          color=(235, 235, 235), bold=True, max_width=inner_width)
        draw.line((pad_x, title_y + line_height + 2,
                   panel_width - pad_x, title_y + line_height + 2),
                  fill=self.ui.chrome((125, 125, 125, 255)))
        head_y = title_y + line_height + 6
        for pane, label in enumerate(self.column_titles):
            x = pane_x[pane]
            header_color = self.GOOD if pane == self.column_pane else (145, 145, 145)
            self._draw_inventory_stats(
                panel, label, x=x + 5, y=head_y + 1, color=header_color,
                font_size=font.size, max_width=pane_widths[pane] - 10)
        list_y = head_y + line_height + 8
        footer_height = self.ui.line_height(10) + 12
        detail_lines = self.ui.wrap_text(self.column_details, font, inner_width)
        detail_capacity = max(3, min(10, int(panel_height * 0.30 // line_height)))
        # The strip reserves only what its own text needs (capped by the
        # panel's share), so tall panes keep their lower rows on screen.
        detail_height = (min(detail_capacity, max(1, len(detail_lines)))
                         * line_height + 8)
        visible = max(1, (panel_height - list_y - detail_height - footer_height - pad_y) // line_height)
        self.choice_rects = []
        self.art_rects = []

        # Wrap every row once and reserve its full height, so a wrapped line
        # can never paint over the neighbouring row below it. A row may also
        # arrive pre-colored as a list of (text, fg, bg) segments — ASCII art
        # drawn cell-aligned in the mono face, never word-wrapped.
        selectable = self.column_selectable
        pane_layout: list[list[tuple[int, list, int]]] = []
        pane_totals: list[int] = []
        for pane, rows in enumerate((left_rows, right_rows)):
            text_width = max(20, pane_widths[pane] - 10)
            layout: list[tuple[int, list, int]] = []
            cursor = 0
            for row in rows:
                if isinstance(row, list):
                    layout.append((cursor, [row], 1))
                    cursor += 1
                    continue
                if "  " in row:
                    # Art or column-aligned text: one verbatim mono line.
                    layout.append((cursor, [row], 1))
                    cursor += 1
                    continue
                lines = self.ui.wrap_text(row, font, text_width) or [""]
                layout.append((cursor, lines, len(lines)))
                cursor += len(lines)
            pane_layout.append(layout)
            pane_totals.append(cursor)

        for pane, rows in enumerate((left_rows, right_rows)):
            layout = pane_layout[pane]
            selected = self.column_index if pane == self.column_pane else -1
            total_lines = pane_totals[pane]
            offset_lines = 0
            if 0 <= selected < len(layout) and total_lines > visible:
                start_line, _lines, height = layout[selected]
                end_line = start_line + height
                offset_lines = max(0, end_line - visible)
                offset_lines = min(offset_lines, start_line,
                                   max(0, total_lines - visible))
            x = pane_x[pane]
            pane_text_width = max(20, pane_widths[pane] - 10)
            pane_mask = selectable[pane]
            window_end = offset_lines + visible
            for row_index, (start_line, lines, height) in enumerate(layout):
                if start_line >= window_end or start_line + height <= offset_lines:
                    continue
                shown = min(height, window_end - start_line)
                y = list_y + (start_line - offset_lines) * line_height
                row_selectable = pane_mask is None or row_index in pane_mask
                is_selected = row_index == selected and row_selectable
                if is_selected:
                    draw.rectangle((x, y, x + pane_widths[pane],
                                    y + shown * line_height),
                                   fill=self.ui.chrome((225, 225, 225, 255)))
                row_is_art = isinstance(rows[row_index], list)
                row_color = ((195, 195, 195) if row_is_art else
                             self._semantic_color(rows[row_index], (195, 195, 195)))
                for line_no, line_text in enumerate(lines[:shown]):
                    if isinstance(line_text, list):
                        self._draw_segment_line(
                            panel, line_text, x=x + 5,
                            y=y + line_no * line_height,
                            line_height=line_height, max_width=pane_text_width)
                        continue
                    self._draw_detail_line(
                        panel, line_text, x=x + 5, y=y + line_no * line_height,
                        color=(8, 8, 8) if is_selected else row_color,
                        max_width=pane_text_width)
                if not row_selectable:
                    if row_is_art:
                        # The drawing itself takes mouse aim even though the
                        # keyboard never selects it: remember where its first
                        # glyph sits and how many mono cells it spans.
                        mono = self.ui.font(mono=True)
                        art_px = sum(round(mono.getlength(text))
                                     for text, _fg, _bg in rows[row_index])
                        self.art_rects.append((row_index, (
                            x0 + x + 5, y0 + y,
                            x0 + x + 5 + art_px,
                            y0 + y + shown * line_height)))
                    continue
                if self.screen_kind == "inspect":
                    if pane == 1:
                        self.choice_rects.append((row_index, (
                            x0 + x, y0 + y, x0 + x + pane_widths[pane],
                            y0 + y + shown * line_height)))
                else:
                    global_index = row_index if pane == 0 else len(left_rows) + row_index
                    self.choice_rects.append((global_index, (
                        x0 + x, y0 + y, x0 + x + pane_widths[pane],
                        y0 + y + shown * line_height)))
        detail_y = list_y + visible * line_height + 4
        draw.line((pad_x, detail_y, panel_width - pad_x, detail_y),
                  fill=self.ui.chrome((125, 125, 125, 255)))
        display_details = detail_lines[:detail_capacity]
        if len(detail_lines) > detail_capacity:
            display_details[-1] = display_details[-1].rstrip(" .") + " …"
        for index, line in enumerate(display_details):
            self._draw_detail_line(
                panel, line, x=pad_x, y=detail_y + 4 + index * line_height,
                color=(190, 190, 190), max_width=inner_width)
        footer_y = panel_height - pad_y - self.ui.line_height(10)
        self.ui.draw_text(panel, self.footer, x=pad_x, y=footer_y, size=10,
                          color=(130, 130, 130), max_width=inner_width)
        result.alpha_composite(panel, (x0, y0))
        return result

    def _draw_inventory(self, image: Image.Image) -> Image.Image:
        left_rows, right_rows = self.columns or ([], [])
        result = image.copy()
        font = self.ui.font(mono=True)
        line_height = max(self.ui.line_height(), round(font.size * 1.35))
        pad_x, pad_y = 20, 18
        panel_width = min(image.width - 20, max(540, round(image.width * 0.94)))
        panel_height = min(image.height - 20, max(360, round(image.height * 0.9)))
        x0, y0 = (image.width - panel_width) // 2, (image.height - panel_height) // 2
        panel = Image.new("RGBA", (panel_width, panel_height), self.ui.chrome((7, 7, 7, 251)))
        draw = ImageDraw.Draw(panel)
        border = self.ui.chrome((190, 190, 190, 255))
        dark_gray = (105, 105, 105)
        light_gray = (190, 190, 190)
        white = (240, 240, 240)
        active_colors = (self.GOOD, self.INFO, self.WARNING)
        draw.rectangle((0, 0, panel_width - 1, panel_height - 1), outline=border, width=1)
        inner_width = panel_width - 2 * pad_x

        self.ui.draw_text(panel, self.column_title, x=pad_x, y=pad_y,
                          color=white, bold=True, mono=True, max_width=inner_width)
        hint_y = pad_y + line_height
        self.ui.draw_text(panel, self.inventory_hint, x=pad_x, y=hint_y,
                          size=12, color=dark_gray, mono=True, max_width=inner_width)
        stats_font = self.ui.font(12, mono=True)
        stats_width = round(stats_font.getlength(self.inventory_stats))
        stats_x = max(pad_x, panel_width - pad_x - stats_width)
        self._draw_inventory_stats(
            panel, self.inventory_stats, x=stats_x, y=hint_y,
            font_size=12, color=dark_gray,
            max_width=max(1, panel_width - pad_x - stats_x))
        header_rule_y = hint_y + self.ui.line_height(12) + 5
        draw.line((1, header_rule_y, panel_width - 2, header_rule_y), fill=border)

        gap = 28
        column_width = (inner_width - gap) // 2
        head_y = header_rule_y + 9
        for pane, label in enumerate(self.column_titles):
            x = pad_x + pane * (column_width + gap)
            color = active_colors[pane] if pane == self.column_pane else light_gray
            self.ui.draw_text(panel, label, x=x, y=head_y, color=color,
                              mono=True, bold=pane == self.column_pane,
                              max_width=column_width)
            if pane == self.column_pane:
                # Flag rides on the header line itself; drawing it below used
                # to clip into the first list row.
                self.ui.draw_text(panel, "ACTIVE", x=x, y=head_y + 2,
                                  color=active_colors[2], size=12, mono=True,
                                  align="right", max_width=column_width - 4)
        list_y = head_y + line_height + 7
        footer_line_height = self.ui.line_height(10)
        footer_y = panel_height - pad_y - 2 * footer_line_height
        detail_lines = self.ui.wrap_text(self.column_details, font, inner_width)
        detail_height = min(3, max(1, len(detail_lines))) * line_height + 12
        list_bottom = footer_y - detail_height - 9
        visible = max(1, (list_bottom - list_y) // line_height)
        self.choice_rects = []
        key_width = round(font.getlength("a  "))
        condition_width = round(font.getlength("||")) + 8
        item_text_width = max(20, column_width - key_width - condition_width - 5)

        for pane, rows in enumerate((left_rows, right_rows)):
            selected = self.column_index if pane == self.column_pane else -1
            if not any(row.get("kind") == "item" for row in rows):
                x = pad_x + pane * (column_width + gap)
                empty_text = "Your inventory is empty." if pane == 0 else "No worn gear."
                self.ui.draw_text(panel, empty_text, x=x + 3, y=list_y,
                                  color=dark_gray, mono=True,
                                  max_width=column_width - 6)
                continue
            # Wrap each row once; long item names reserve two slots instead of
            # painting over their neighbour.
            layout: list[tuple[int, dict, list[str], int]] = []
            cursor = 0
            for row in rows:
                if row.get("kind") == "category":
                    layout.append((cursor, row, [row.get("text", "")], 1))
                    cursor += 1
                else:
                    lines = self.ui.wrap_text(row.get("text", ""), font,
                                              item_text_width) or [""]
                    layout.append((cursor, row, lines, len(lines)))
                    cursor += len(lines)
            total_lines = cursor
            selected_position = next((i for i, (_start, row, _lines, _height)
                                      in enumerate(layout)
                                      if row.get("kind") == "item"
                                      and row.get("index") == selected), None)
            offset_lines = 0
            if (selected_position is not None and total_lines > visible):
                start_line, _row, _lines, height = layout[selected_position]
                end_line = start_line + height
                offset_lines = max(0, end_line - visible)
                offset_lines = min(offset_lines, start_line,
                                   max(0, total_lines - visible))
            x = pad_x + pane * (column_width + gap)
            window_end = offset_lines + visible
            for start_line, row, lines, height in layout:
                if start_line >= window_end or start_line + height <= offset_lines:
                    continue
                shown = min(height, window_end - start_line)
                y = list_y + (start_line - offset_lines) * line_height
                if row.get("kind") == "category":
                    self.ui.draw_text(panel, row.get("text", ""), x=x + 3, y=y,
                                      color=self.INFO, mono=True,
                                      max_width=column_width - 6)
                    continue
                item_index = int(row.get("index", -1))
                is_selected = pane == self.column_pane and item_index == selected
                if is_selected:
                    draw.rectangle((x, y, x + column_width, y + shown * line_height),
                                   fill=self.ui.chrome((235, 235, 235, 255)))
                hotkey = row.get("hotkey", " ")
                self.ui.draw_text(panel, hotkey, x=x + 2, y=y,
                                  color=(0, 100, 0) if is_selected else active_colors[2],
                                  mono=True)
                for line_no, line_text in enumerate(lines[:shown]):
                    self._draw_detail_line(
                        panel, line_text, x=x + key_width, y=y + line_no * line_height,
                        color=(8, 8, 8) if is_selected else light_gray,
                        mono=True, max_width=item_text_width)
                marker = row.get("condition_marker")
                if marker:
                    value = max(0, min(100, int(row.get("condition", 100))))
                    wear_color = (self.GOOD if value >= 81 else
                                  self.WARNING if value >= 41 else self.DANGER)
                    self.ui.draw_text(
                        panel, marker, x=x + column_width - condition_width + 2, y=y,
                        color=wear_color, mono=True, bold=True,
                        max_width=condition_width)
                self.choice_rects.append((pane * 100000 + item_index, (
                    x0 + x, y0 + y, x0 + x + column_width, y0 + y + shown * line_height)))

        detail_y = list_bottom + 4
        draw.line((pad_x, detail_y, panel_width - pad_x, detail_y),
                  fill=self.ui.chrome((125, 125, 125, 255)))
        for index, line in enumerate(detail_lines[:3]):
            self._draw_detail_line(
                panel, line, x=pad_x, y=detail_y + 5 + index * line_height,
                color=light_gray, mono=True, max_width=inner_width)
        if self.inventory_filter_edit:
            status_line = f"Filter: {self.inventory_filter}_"
            controls_line = "Enter apply · Esc cancel"
            status_color = white
        else:
            filter_label = f"Filter: {self.inventory_filter}" if self.inventory_filter else "[f or /] Filter"
            mode_label = f"[;] {self.inventory_view_mode} · [t] {self.inventory_navigation_mode} selection"
            status_line = f"{filter_label}    {mode_label}"
            controls_line = self.footer
            status_color = light_gray
        self.ui.draw_text(panel, status_line, x=pad_x, y=footer_y, size=10,
                          color=status_color, mono=True, max_width=inner_width)
        self.ui.draw_text(panel, controls_line, x=pad_x,
                          y=footer_y + footer_line_height, size=10,
                          color=light_gray, mono=True, max_width=inner_width)
        result.alpha_composite(panel, (x0, y0))
        return result

    def choice_at(self, x: int, y: int) -> int | None:
        for choice, rect in self.choice_rects:
            x0, y0, x1, y1 = rect
            if x0 <= x <= x1 and y0 <= y <= y1:
                return choice
        return None

    def art_cell_at(self, x: int, y: int) -> tuple[int, int] | None:
        """The clicked art row and its zero-based mono cell column, or None.

        The stage screen turns this into the part of the tree the click
        landed on — a limb, a fruit, the trunk — so the drawing itself is
        the mouse's target list."""
        for art_row, rect in self.art_rects:
            x0, y0, x1, y1 = rect
            if x0 <= x <= x1 and y0 <= y <= y1:
                advance = max(1.0, self.ui.font(mono=True).getlength("M"))
                return art_row, int((x - x0) // advance)
        return None

    def draw_tooltip(self, image: Image.Image, text: str,
                     x: int, y: int) -> Image.Image:
        """Draw a wrapped tooltip in screen coordinates above the UI layer."""
        result = image.copy()
        font = self.ui.font(12)
        lines = self.ui.wrap_text(text, font, min(360, image.width - 24))
        line_height = self.ui.line_height(12)
        width = max((round(font.getlength(line)) for line in lines), default=1) + 16
        height = len(lines) * line_height + 14
        x = min(max(4, x), max(4, image.width - width - 4))
        y = min(max(4, y), max(4, image.height - height - 4))
        panel = Image.new("RGBA", (width, height), self.ui.chrome((8, 8, 8, 250)))
        draw = ImageDraw.Draw(panel)
        draw.rectangle((0, 0, width - 1, height - 1),
                       outline=self.ui.chrome((145, 145, 145, 255)), width=1)
        for index, line in enumerate(lines):
            self.ui.draw_text(panel, line, x=8, y=7 + index * line_height,
                              size=12, color=(230, 235, 242))
        result.alpha_composite(panel, (x, y))
        return result
