"""Run the ASCII-first Pirates of Miska prototype with the copied SDL engine."""
from pathlib import Path
import argparse
import sys
from PIL import Image
from tcod.event import KeySym
import tcod.event
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "engine"))

import omnisim.ui.ascii_renderer as ascii_renderer
from omnisim.ui.ascii_renderer import AsciiRenderer
from omnisim.ui.render_settings import RenderSettings
from omnisim.world import tile as T
from tales_sim.clock import TICKS_PER_SECOND
from pirates.game import World, SAVE_PATH
from pirates.management import (crew_menu, chores_menu, ship_rows, time_menu,
                                context_menu, profile_rows, events_menu)
from pirates.economy import PORTS, MERCHANTS

# Bind the game's extra command at the adapter boundary; keep the vendored
# renderer snapshot byte-for-byte intact for provenance verification.
_engine_world_action_for_key = ascii_renderer.world_action_for_key


def _pirates_world_action_for_key(sym, shift=False):
    if sym == tcod.event.KeySym.F8:
        return ("blueprint",)
    if sym == tcod.event.KeySym.V:
        return ("voyage",)
    return _engine_world_action_for_key(sym, shift)


ascii_renderer.world_action_for_key = _pirates_world_action_for_key


class PiratesRenderer(AsciiRenderer):
    def _build(self):
        # Install before tcod.context.new uploads the atlas to SDL.
        loader = ascii_renderer.tcod.tileset.load_truetype_font
        sprite_codes = {}

        def load_font_with_urizen(*args, **kwargs):
            tileset = loader(*args, **kwargs)
            from pirates.urizen_tileset import install_sprite_atlas
            sprite_codes.update(install_sprite_atlas(
                tileset, self.tile_width, self.tile_height))
            from pirates.player_sprites import install_player_sprite
            sprite_codes.update(install_player_sprite(
                tileset, self.tile_width, self.tile_height))
            return tileset

        ascii_renderer.tcod.tileset.load_truetype_font = load_font_with_urizen
        try:
            super()._build()
        finally:
            ascii_renderer.tcod.tileset.load_truetype_font = loader
        self._sprite_codepoints.update(sprite_codes)
        if self.world:
            # AsciiRenderer replaces its dictionary after atlas loading; reconnect
            # the WorldRenderer to the newly installed private-use codepoints.
            self.world.sprite_codepoints = self._sprite_codepoints
        if self.context.sdl_window:
            self.context.sdl_window.title = f"Pirates of Miska — Urizen [{self.zoom_name}]"

    def sprite_for_tile(self, game_map, x, y):
        from pirates.urizen_tileset import sprite_for_tile
        tile_id = int(game_map.tiles[y, x])
        return sprite_for_tile(tile_id, door_open=(tile_id == T.DOOR and bool(game_map.doors[y, x])))

    def sprite_for_entity(self, entity):
        from pirates.player_sprites import sprite_for_entity
        return sprite_for_entity(entity)

    def sprite_for_ground(self, world, wx, wy):
        from pirates.urizen_tileset import sprite_for_ground
        return sprite_for_ground(world.ground_items.get((wx, wy), ()))

    def _draw_lit_cell(self, sx, sy, glyph, *, fg, bg, sprite, brightness):
        if sprite and sprite in self._sprite_codepoints:
            # Urizen artwork is already colored; avoid tcod tinting its palette.
            self.world.draw_cell(sx, sy, glyph, fg=(255, 255, 255), bg=bg, sprite=sprite)
        else:
            super()._draw_lit_cell(sx, sy, glyph, fg=fg, bg=bg,
                                   sprite=sprite, brightness=brightness)

    def _sidebar_lines(self, world, width, height):
        ship = world.ship
        status = world.voyages.voyage["status"] if world.voyages.voyage else "moored"
        place = "OPEN SEA" if world.at_sea else PORTS[world.economy.port]['name']
        lines = [f"THE WAYWARD | {place} | {status}", f"{world.economy.money} Marks", world.clock.format(),
                 world.room_name(world.player.x, world.player.y),
                 f"Hull {ship.hull:.0f}% | Bilge {ship.bilge:.0f} | Rig {ship.rigging:.0f}%",
                 f"Meals {ship.meals} | Water {ship.stores['water']} | Planks {ship.stores['planks']}", "", "CREW"]
        for sailor in ship.crew:
            lines.extend([f"{sailor.glyph} {sailor.name} | {sailor.mood_label} {sailor.morale:.0f}", f"  {sailor.activity}"])
        lines += ["", "F2 Crew / labors | A Chores", "F3 Ship | F4 Pass time | F6 Events", "V Voyage / sea chart | Enter: interact"]
        return [[(line, (210, 210, 210), (16, 16, 16))] for line in lines]

    def _status_lines(self, world):
        return [world.log[-1], "Move: arrows/HJKL | Enter interact | F2 crew | A chores | F4 wait | F8 blueprint | +/- zoom | F1 help"]

    def _ship_mark(self, world, sx, sy, wx, wy, skip_actors=False):
        if not world.game_map.visible[wy, wx]:
            return
        if skip_actors and any((e.x, e.y) == (wx, wy) for e in world.entities()):
            return
        glyph = None
        color = (185, 170, 125)
        station_glyphs = {"helm": "O", "rigging": "|", "port_cannon": "C", "starboard_cannon": "C",
                          "galley": "&", "water": "W", "hold": "H", "lookout": "^"}
        for name, at in world.ship.stations.items():
            if at == (wx, wy) and name in station_glyphs and world.fixtures.available(name):
                glyph = station_glyphs[name]
        if world.ship.dirt.get(f"{wx},{wy}", 0) >= 20:
            glyph, color = "%", (175, 145, 75)
        if (wx, wy) == world.ship.stations["repair"] and world.ship.hull < 100:
            glyph, color = "!", (240, 140, 80)
        if (wx, wy) == world.ship.stations["bilge"] and world.ship.bilge >= 3:
            glyph, color = "~", (100, 175, 225)
        for job in world.ship.jobs:
            if (job.relocation and job.state not in ("done", "cancelled")
                    and tuple(job.target) == (wx, wy)):
                glyph, color = "F", (100, 220, 245)
            if (job.construction and job.state not in ("done", "cancelled")
                    and tuple(job.target) == (wx, wy)):
                glyph = "x" if job.construction["action"] == "dismantle" else "+" if job.construction["kind"] == "door" else "#"
                color = (100, 220, 245)
        change = next((row for row in world.blueprint.changes
                       if (row["x"] + 69, row["y"] + 12) == (wx, wy)), None)
        if change:
            glyph = "x" if change["action"] == "dismantle" else "+" if change["kind"] == "door" else "#" if change["kind"] == "wall" else "."
            color = (225, 150, 245)
        if glyph:
            self._draw_lit_cell(sx, sy, glyph, fg=color, bg=(0, 0, 0), sprite=None, brightness=1)
        for pile in world.ship.storage.piles.values():
            if world.ship.storage.position(pile.id, world.fixtures) == (wx, wy):
                self._draw_lit_cell(sx, sy, "*", fg=(220, 190, 100), bg=(0, 0, 0), sprite=None, brightness=1)

    def _frame_cell_extras(self, world):
        cx, cy, ox, oy = self._active_camera
        for at in (set(world.ship.stations.values()) | {tuple(map(int, k.split(','))) for k in world.ship.dirt}
                   | {tuple(j.target) for j in world.ship.jobs if j.construction or j.relocation}
                   | {tuple(j.relocation["source"]) for j in world.ship.jobs if j.relocation}
                   | {world.ship.storage.position(p.id, world.fixtures) for p in world.ship.storage.piles.values()}
                   | {(r["x"] + 69, r["y"] + 12) for r in world.blueprint.changes}):
            x, y = at
            sx, sy = x - cx + ox, y - cy + oy
            if 0 <= sx < self.console.width and 0 <= sy < self.console.height:
                self._ship_mark(world, sx, sy, x, y, skip_actors=True)

    def _draw_world_cell(self, world, sx, sy, wx, wy):
        super()._draw_world_cell(world, sx, sy, wx, wy)
        if world.game_map.in_bounds(wx, wy):
            self._ship_mark(world, sx, sy, wx, wy)

    def _refresh_motion_cache(self, world, cache, camera_start, camera_end, offset):
        super()._refresh_motion_cache(world, cache, camera_start, camera_end, offset)
        # Job completion can remove a dirt/leak marker during held movement.
        ax, ay = cache['anchor']
        ox, oy = cache['offset']
        bw, bh = cache['buffer_size']
        for x, y in [*world.ship.stations.values(), *(tuple(map(int, k.split(','))) for k in world.ship.dirt),
                     *(tuple(j.target) for j in world.ship.jobs if j.construction or j.relocation),
                     *(tuple(j.relocation["source"]) for j in world.ship.jobs if j.relocation),
                     *(world.ship.storage.position(p.id, world.fixtures) for p in world.ship.storage.piles.values()),
                     *((r["x"] + 69, r["y"] + 12) for r in world.blueprint.changes)]:
            sx, sy = x - ax + ox, y - ay + oy
            if 0 <= sx < bw and 0 <= sy < bh:
                self._draw_world_cell(world, sx, sy, x, y)
                self._draw_world_overlays_at(world, sx, sy, x, y)


def panel(renderer, world, title, rows):
    renderer.render_list(world, title, rows, "Any key to return", selectable=False)
    renderer.wait_any()


def inspect(renderer, world, x, y):
    gm = world.game_map
    if not gm.in_bounds(x, y) or not gm.visible[y, x]:
        world.message("That tile is out of sight.")
        return
    rows = [T.tile_name(gm.tile_id(x, y))]
    rows += [o["name"] for o in world.objects_at.get((x, y), [])]
    rows += [f"{i['name']} x{i.get('quantity', 1)}" for i in world.ground_items.get((x, y), [])]
    rows += ["", f"Area: {world.room_name(x, y)}"]
    pile = next((p for p in world.ship.storage.piles.values()
                 if world.ship.storage.position(p.id, world.fixtures) == (x, y)), None)
    if pile:
        from pirates.ship_storage import bundle_mass
        rows += [f"Stockpile {pile.id}: {pile.contents or 'empty'}",
                 f"Mass/capacity: {bundle_mass(pile.contents)}/{pile.capacity_kg} kg"]
    for sailor in world.ship.crew:
        if (sailor.x, sailor.y) == (x, y):
            rows += ["", *profile_rows(sailor, world)]
    for merchant in world.economy.merchants():
        if (merchant.x, merchant.y) == (x, y):
            record = MERCHANTS[merchant.id]
            rows += ["", record["title"], *record["dialogue"], "Stand nearby and press Enter to talk or trade."]
    panel(renderer, world, f"Inspect ({x}, {y})", rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new", action="store_true", help="Ignore the existing save")
    parser.add_argument("--smoke-render", action="store_true", help="Render, exercise movement and exit without saving")
    parser.add_argument("--screenshot", type=Path, help="Save a native SDL screenshot")
    args = parser.parse_args()
    world = World()
    if not args.new and not args.smoke_render and SAVE_PATH.exists():
        try:
            world = World.load()
        except (ValueError, KeyError, TypeError, OSError) as error:
            # Do not silently replace a broken slot with a fresh autosave.
            parser.exit(1, f"Cannot load {SAVE_PATH}: {error}\nUse --new for a fresh game.\n")
    renderer = PiratesRenderer(settings=RenderSettings(
        world_tileset="ascii", world_font_size=20,
        world_cell_width=20, world_cell_height=24))
    try:
        renderer.init_for(world.game_map.width, world.game_map.height)
        if args.smoke_render:
            from pirates.ship_building import queue
            queue(world, (78, 27), "build", "door")
            world.spaces.designate(world, world.player.x, world.player.y, "working deck")
        renderer.render(world)
        if args.smoke_render:
            def smoke_step():
                for dx, dy in ((-1, 0), (0, 1), (0, -1), (1, 0)):
                    x, y = world.player.x + dx, world.player.y + dy
                    if world.game_map.is_walkable(x, y) and not any((c.x, c.y) == (x, y) for c in world.ship.crew):
                        assert world.move(dx, dy)
                        return
                raise AssertionError("Smoke movement is blocked in every direction")
            start = world.player.x, world.player.y
            smoke_step()
            renderer.animate_move(world, start, (world.player.x, world.player.y))
            renderer.render(world)
            from pirates.ship_spaces import report_rows
            renderer.render_list(world, "Room report", report_rows(world, world.player.x, world.player.y), selectable=False)
            renderer.render(world)
            renderer._present_layers()
            # Check both zoom rebuilds, then held-motion cache reuse on the pier.
            for direction in (1, -1, -1, 1):
                renderer.zoom(direction, world.game_map.width, world.game_map.height)
                renderer.render(world)
            renderer._move_repeater.press(KeySym.RIGHT, ("move", 1, 0), time.perf_counter())
            for _ in range(3):
                start = world.player.x, world.player.y
                smoke_step()
                renderer.animate_move(world, start, (world.player.x, world.player.y))
            renderer._move_repeater.cancel()
            renderer._finish_motion_cache()
            renderer.render(world)
            renderer._present_layers()
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(renderer.context.sdl_renderer.read_pixels()).save(args.screenshot)
            print("Urizen SDL render, movement tween and modal smoke passed.")
            return
        while True:
            renderer.render(world)
            action = renderer.wait_action()
            name = action[0]
            if name == "quit":
                try:
                    world.save()
                except OSError as error:
                    world.message(f"Cannot quit safely: save failed ({error}). Try F5 again.")
                    continue
                break
            if name == "move":
                start = world.player.x, world.player.y
                if world.move(*action[1:]):
                    renderer.animate_move(world, start, (world.player.x, world.player.y))
            elif name == "zoom":
                renderer.zoom(action[1], world.game_map.width, world.game_map.height)
            elif name == "wait":
                world.advance(TICKS_PER_SECOND)
                world.message("You wait a moment.")
            elif name == "context_menu":
                context_menu(renderer, world)
            elif name == "open_container":
                world.toggle_door()
            elif name in ("tutorial_journal", "progression"):
                crew_menu(renderer, world)
            elif name == "tutorial_guidance":
                panel(renderer, world, "Ship ledger", ship_rows(world))
            elif name == "tutorial_track":
                time_menu(renderer, world)
            elif name == "activities":
                chores_menu(renderer, world)
            elif name == "activity_history":
                events_menu(renderer, world)
            elif name == "blueprint":
                from pirates.management import blueprint_menu
                blueprint_menu(renderer, world)
            elif name == "voyage":
                from pirates.management import voyage_menu
                voyage_menu(renderer, world)
            elif name == "get":
                world.pickup()
            elif name in ("pack", "drop"):
                rows = [f"{i['name']} x{i.get('quantity', 1)}" for i in world.inventory]
                if name == "pack":
                    panel(renderer, world, "Captain's satchel", rows or ["Empty. G picks up supplies; D drops them."])
                elif rows:
                    renderer.render_list(world, "Drop which item?", rows, "Enter drops; Esc cancels")
                    index = renderer.choose(len(rows))
                    if index is not None:
                        world.drop(index)
                else:
                    world.message("Your satchel is empty.")
            elif name in ("inspect", "inspect_at"):
                inspect(renderer, world, *(action[1:] if name == "inspect_at" else (world.player.x, world.player.y)))
            elif name == "save_game":
                try:
                    world.save()
                except OSError as error:
                    world.message(f"Save failed: {error}")
            elif name == "load_game":
                try:
                    world = World.load()
                except (ValueError, KeyError, TypeError, OSError) as error:
                    world.message(f"Load failed: {error}")
            elif name == "help":
                panel(renderer, world, "Controls", [
                    "Arrows or HJKL: move (hold to repeat)", ". / keypad 5: wait",
                    "Enter: nearby crew / door interactions   O: toggle door", "G: pick up supplies at your feet",
                    "I: inventory   D: drop an item", "E: inspect your tile; click: inspect visible tile",
                    "+ / -: zoom   F5: save   F9: load", "Esc / Q: save and quit",
                    "F2: crew profiles and labor priorities (0 off, 1 high, 5 low)",
                    "A: chore board, priorities, cancellations and blocked reasons",
                    "F3: ship ledger   F4: pass 10s / 1m / 10m   F6: crew events",
                    "V: consult officers, order a voyage, set free-time and watch sea chart.",
                    "Menus freeze time; movement / waiting advances the whole crew.",
                    "Needs preempt work; carried supplies and progress are retained.",
                    "$: merchant. Stand within two tiles, then Enter to talk / buy / sell.",
                    "Enter > Trade cargo / crew equipment: allocate supplies at the hold.",
                    "Enter > Island guide / coastal passage: arrange travel at helm / harbor office.",
                    "F8: ship blueprint editor — B cycle, Space mark, C copy, M mirror, R rotate, Z undo, Enter queue.",
                    "Sea voyages run the ship simulation; nearby ships interrupt. Tactical combat is not built yet."])
            else:
                world.message("That system is not part of the foundation yet. F1 lists available controls.")
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
