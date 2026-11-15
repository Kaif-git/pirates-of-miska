"""Entry point. ASCII now, tilesheet renderer later — sim never talks to tcod directly."""
from __future__ import annotations
import argparse


def main() -> None:
    ap = argparse.ArgumentParser(description="Omni Sim — Thornhollow village")
    ap.add_argument("--smoke", action="store_true",
                    help="headless check: print village, simulate a walk, no window")
    ap.add_argument("--web", action="store_true",
                    help="browser UI: readable panels + clickable map (http://127.0.0.1:8471)")
    ap.add_argument("--renderer-diagnostic", action="store_true",
                    help="open the renderer-only ASCII/UI layer diagnostic")
    ap.add_argument("--world-font", default="Consolas",
                    help="monospaced map font family (default: Consolas)")
    ap.add_argument("--ui-font", default="Segoe UI",
                    help="ordinary interface font family (default: Segoe UI)")
    ap.add_argument("--world-font-size", type=int, default=16,
                    help="map glyph font size in pixels (default: 16)")
    ap.add_argument("--ui-font-size", type=int, default=15,
                    help="interface body font size in pixels (default: 15)")
    ap.add_argument("--ui-scale", type=float, default=1.0,
                    help="scale UI typography independently of map zoom")
    ap.add_argument("--world-cell-width", type=int,
                    help="override measured world-cell width in pixels")
    ap.add_argument("--world-cell-height", type=int,
                    help="override measured world-cell height in pixels")
    args = ap.parse_args()

    if args.renderer_diagnostic:
        from omnisim.ui.diagnostic import run_diagnostic
        run_diagnostic(args.world_font, args.ui_font,
                       args.world_font_size, args.ui_font_size,
                       args.world_cell_width, args.world_cell_height,
                       args.ui_scale)
        return

    if args.web:
        from omnisim.web.server import main as web_main
        web_main()
        return

    if args.smoke:
        import sys
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        from omnisim.core.game import Game
        from omnisim.world.worldgen_scripted import render_ascii
        g = Game()
        v = g.setup()
        print(f"Village: {v.name}  map {v.game_map.width}x{v.game_map.height}  npcs {len(v.npcs)}")
        for row in render_ascii(v):
            print(row)
        from omnisim.core.clock import fmt_clock
        px, py = v.player.x, v.player.y
        print("start:", fmt_clock(v.clock_pts))
        for dx, dy in [(0, -1)] * 14 + [(0, 1)] * 2:
            g.step_move(dx, dy)
        print(f"player ({px},{py}) -> ({v.player.x},{v.player.y})  turn {v.turn}")
        print("after walk:", fmt_clock(v.clock_pts))
        v.player.x, v.player.y = 18, 14  # below the tavern door
        print("bump:", g.step_move(0, -1), "|", fmt_clock(v.clock_pts))
        print("--- a day in Thornhollow (fast-forward) ---")
        g2 = Game()
        v2 = g2.setup()
        for _ in range(144):
            g2.step_wait()
            tod = v2.clock_pts // 100 % 1440
            if tod in (720, 1080, 120):
                print(fmt_clock(v2.clock_pts), " | ".join(
                    "%s %s@(%d,%d)" % (n.name, n.get("activity"), n.x, n.y)
                    for n in v2.npcs))
        sella = next(n for n in v2.npcs if n.name == "Sella")
        print("Sella Smithing xp:", sella.get("skillxp", {}).get("Smithing"),
              "| Mira coin:", next(n for n in v2.npcs if n.name == "Mira").get("pocket", {}).get("coin"))
        from omnisim.entities.items import pack_of
        v.player.x, v.player.y = 16, 32  # the windfall apples
        print("get:", g.step_get(), "| pack:", pack_of(v.player))
        v.player.comps["hunger"] = 60.0
        print("eat:", g.step_eat(), "| hunger:", round(float(v.player.get("hunger")), 2))
        v.player.x, v.player.y = 15, 35  # the fields
        print("farm:", g.step_work(), "| xp:", v.player.get("skillxp", {}).get("Farming"))
        v.player.x, v.player.y = 56, 35  # Stillpond shore
        print("fish:", g.step_work())
        v.player.get("pack", {})["wheat"] = 2
        v.player.x, v.player.y = 16, 12  # beside the Boar's hearth
        print("cook:", g.step_work())
        from omnisim.systems.skills import top_skill
        print("top skill:", top_skill(v.player))
        v.player.x, v.player.y = 35, 20  # beside a food stall
        print("buy:", g.step_buy(), "| coin:", pack_of(v.player).get("coin"))
        v.player.get("pack", {})["wheat"] = 3
        print("sell:", g.step_sell(), "| coin:", pack_of(v.player).get("coin"))
        v.player.x, v.player.y = 19, 9  # Mira's bunk
        from omnisim.ui.sidebar import available_actions, here_hints, plain, plain_profile
        print("hints:", here_hints(v))
        print("menu:", available_actions(v))
        mira0 = next(n for n in v.npcs if n.name == "Mira")
        print("profile:", " | ".join(plain_profile(v, mira0)[:4]))
        print("sleep:", g.step_sleep(), "|", fmt_clock(v.clock_pts))
        print("log:", " | ".join(v.log[-3:]))
        print("--- sidebar ---")
        for text in plain(v, 25, 24):
            print(text)
        from omnisim.systems.perks import unlocked_perks
        v.player.comps["skills"] = {"Farming": 6, "Woodcutting": 5}
        print("perks:", unlocked_perks(v.player, min_level=2))
        print("news:", [a for a in available_actions(v) if "perk" in a])
        v.player.x, v.player.y = 63, 26  # Sella's anvil
        v.player.get("pack", {})["firewood"] = 3
        print("forge:", g.step_work())
        from omnisim.systems.tools import kit_line
        print("kit:", kit_line(v.player))
        print("lore:", v.player.get("lore", {}))
        print("tills:", [s.get("till", 0) for s in v.stalls])
        return

    from omnisim.core.game import Game
    from omnisim.ui.ascii_renderer import AsciiRenderer
    from omnisim.ui.render_settings import RenderSettings
    settings = RenderSettings(
        world_font_family=args.world_font,
        world_font_size=args.world_font_size,
        world_cell_width=args.world_cell_width,
        world_cell_height=args.world_cell_height,
        ui_font_family=args.ui_font,
        ui_font_size=args.ui_font_size,
        ui_scale=args.ui_scale)
    Game(renderer=AsciiRenderer(settings=settings)).run()


if __name__ == "__main__":
    main()
