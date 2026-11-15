"""Browser UI backend (WEB v1): stdlib HTTP + JSON. No new dependencies.

Run:  PYTHONPATH=src python -m omnisim.web.server  ->  http://127.0.0.1:8471
The ASCII/tcod client is untouched; this is a second front door to the same sim.
"""
from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from omnisim.core.game import Game

HERE = Path(__file__).resolve().parent
PORT = 8471


def _rgb(c):
    return [int(c[0]), int(c[1]), int(c[2])]


def item_lore(ent, item_id):
    lore = (ent.get("lore", {}) or {}).get(item_id, {})
    return {"q": int(lore.get("q", 1)), "by": lore.get("by", "unknown"),
            "origin": lore.get("origin", "?"), "stolen": bool(lore.get("stolen", False))}


def pack_list(ent):
    from omnisim.entities.items import defs, lore_q
    foods = defs()["items"]
    pack = ent.get("pack", ent.get("pocket", {}) or {}) or {}
    out = []
    for item_id, qty in pack.items():
        if qty <= 0:
            continue
        info = foods.get(item_id, {})
        out.append({"id": item_id, "name": info.get("name", item_id), "qty": qty,
                    "food": info.get("food", 0), "price": info.get("price", 0),
                    "q": lore_q(ent, item_id), "lore": item_lore(ent, item_id)})
    return out


def npc_card(n):
    return {"id": n.id, "name": n.name, "glyph": n.glyph, "color": _rgb(n.color),
            "x": n.x, "y": n.y, "role": n.get("role", "?"),
            "age": n.get("age", "?"), "gender": n.get("gender", "?"),
            "hp": int(n.get("hp", 10)), "max_hp": int(n.get("max_hp", 10)),
            "hunger": round(float(n.get("hunger", 0)), 1),
            "fatigue": round(float(n.get("fatigue", 0)), 1),
            "activity": n.get("activity", "idle"), "dead": bool(n.comps.get("dead")),
            "pocket": pack_list(n)}


def work_sites(v):
    """Every place of honest work: name, tile, direction from you. (Nothing hidden.)"""
    from omnisim.ui.sidebar import compass
    from omnisim.world import tile as T
    gm = v.game_map
    px, py = v.player.x, v.player.y
    found = []

    def centroid(tid, name, verb):
        pts = [(x, y) for y in range(gm.height) for x in range(gm.width)
               if int(gm.tiles[y, x]) == tid]
        if pts:
            cx = sum(p[0] for p in pts) // len(pts)
            cy = sum(p[1] for p in pts) // len(pts)
            found.append({"name": name, "x": cx, "y": cy, "verb": verb,
                          "dir": compass(cx - px, cy - py)})

    def first(tid, name, verb):
        for y in range(gm.height):
            for x in range(gm.width):
                if int(gm.tiles[y, x]) == tid:
                    found.append({"name": name, "x": x, "y": y, "verb": verb,
                                  "dir": compass(x - px, y - py)})
                    return

    centroid(T.FARM, "Fields", "W farm wheat")
    if "firewood" in (v.work_posts or {}):
        ax, ay = v.work_posts["firewood"]["at"]
        found.append({"name": "Wilds", "x": ax, "y": ay, "verb": "W fell trees for firewood",
                      "dir": compass(ax - px, ay - py)})
    else:
        centroid(T.TREE, "Wilds", "W fell trees for firewood")
    if "fish" in (v.work_posts or {}):
        ax, ay = v.work_posts["fish"]["at"]
        found.append({"name": "Stillpond shore", "x": ax, "y": ay, "verb": "W fish",
                      "dir": compass(ax - px, ay - py)})
    else:
        centroid(T.WATER, "Stillpond", "W fish")
    first(T.ANVIL, "Smithy anvil", "W forge / mend")
    first(T.WORKBENCH, "Store workbench", "W build beams")
    first(T.HEARTH, "Boar's hearth", "W cook meals")
    for s in v.stalls:
        found.append({"name": "Market stall", "x": s["x"], "y": s["y"],
                      "verb": "T buy / Shift+T sell",
                      "dir": compass(s["x"] - px, s["y"] - py)})
    for p in v.places:
        if "tavern" in p.name.lower() or "boar" in p.name.lower():
            found.append({"name": p.name, "x": (p.x1 + p.x2) // 2,
                          "y": (p.y1 + p.y2) // 2, "verb": "beds, hearth, meals",
                          "dir": ""})
    return found


def state_of(game):
    from omnisim.core.actions import BUY_ORDER
    from omnisim.core.clock import day_of, fmt_clock
    from omnisim.systems.pricing import scarcity_of, trend_word
    from omnisim.systems.skills import skill_level
    from omnisim.ui.sidebar import available_actions, nearest_stall_obj, trade_hints
    from omnisim.world import tile as T
    from omnisim.world.tile import BY_ID
    v = game.village
    gm = v.game_map
    trading = skill_level(v.player, "Trading")
    stall = nearest_stall_obj(v, v.player.x, v.player.y)
    stalls = []
    for s in v.stalls:
        lines = []
        for item_id, (qty, _listed) in s["stock"].items():
            from omnisim.core.actions import trade_price
            from omnisim.entities.items import defs
            base = defs()["items"].get(item_id, {}).get("price", 0)
            lines.append({"id": item_id,
                          "name": defs()["items"].get(item_id, {}).get("name", item_id),
                          "qty": qty, "base": base,
                          "buy": trade_price(item_id, trading, True, s, village=v),
                          "sell": trade_price(item_id, trading, False, s, village=v),
                          "word": trend_word(s, item_id),
                          "mult": round(scarcity_of(s, item_id), 1)})
        stalls.append({"x": s["x"], "y": s["y"], "till": s.get("till", 0), "stock": lines})
    tiles = gm.tiles.tolist()
    glyphs = {str(t.id): {"g": t.glyph, "fg": list(t.fg), "name": t.name}
              for t in BY_ID.values()}
    chest = None
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            x, y = v.player.x + dx, v.player.y + dy
            if gm.in_bounds(x, y) and int(gm.tiles[y, x]) == int(T.CHEST) and (x, y) in v.chests:
                c = v.chests[(x, y)]
                items = []
                for item_id, qty in c["pack"].items():
                    if qty <= 0:
                        continue
                    from omnisim.entities.items import defs
                    info = defs()["items"].get(item_id, {})
                    lore = (c.get("lore", {}) or {}).get(item_id, {})
                    items.append({"id": item_id, "name": info.get("name", item_id),
                                  "qty": qty, "food": info.get("food", 0),
                                  "q": int(lore.get("q", 1)), "lore": lore})
                chest = {"x": x, "y": y, "pack": items}
                break
    return {
        "clock": fmt_clock(v.clock_pts), "day": day_of(v.clock_pts), "turn": v.turn,
        "player": {"x": v.player.x, "y": v.player.y,
                   "hp": int(v.player.get("hp", 20)), "max_hp": int(v.player.get("max_hp", 20)),
                   "hunger": round(float(v.player.get("hunger", 0)), 1),
                   "skills": v.player.get("skills", {}), "skillxp": v.player.get("skillxp", {}),
                   "pack": pack_list(v.player),
                   "rhythm_site": v.player.get("rhythm_site"),
                   "rhythm_n": int(v.player.get("rhythm_n", 0))},
        "npcs": [npc_card(n) for n in v.npcs],
        "watch_id": v.watch_id,
        "tiles": tiles, "glyphs": glyphs,
        "width": gm.width, "height": gm.height,
        "visible": gm.visible.tolist(),
        "stalls": stalls, "near_stall": stall is not None,
        "chest": chest,
        "menu": available_actions(v), "trade": trade_hints(v),
        "sites": work_sites(v),
        "log": v.log[-40:],
    }


class Handler(BaseHTTPRequestHandler):
    game = None

    def _send(self, body, ctype="application/json", code=200):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send((HERE / "index.html").read_bytes(), "text/html")
        elif self.path == "/api/state":
            self._send(json.dumps(state_of(self.game)))
        else:
            self._send(json.dumps({"error": "nope"}), code=404)

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        except Exception:
            body = {}
        g = self.game
        cmd, msg = body.get("cmd", ""), ""
        try:
            if cmd == "move":
                msg = g.step_move(int(body.get("dx", 0)), int(body.get("dy", 0)))
            elif cmd == "wait":
                msg = g.step_wait()
            elif cmd == "eat":
                msg = g.step_eat()
            elif cmd == "get":
                msg = g.step_get()
            elif cmd == "work":
                msg = g.step_work()
            elif cmd == "watch":
                msg = g.step_watch()
            elif cmd == "buy":
                msg = g.step_buy(body.get("item"))
            elif cmd == "sell":
                msg = g.step_sell()
            elif cmd == "sleep":
                msg = g.step_sleep()
            elif cmd == "steal":
                msg = g.step_steal(body.get("item"))
            elif cmd == "give":
                msg = g.step_give(body.get("item"))
            elif cmd == "chest":
                msg = g.step_chest(body.get("item"), bool(body.get("take", False)))
            elif cmd == "build_fire":
                msg = g.step_build_fire()
            elif cmd == "build_cabin":
                msg = g.step_build_cabin()
            elif cmd == "inspect":
                from omnisim.ui.sidebar import plain_profile
                t = next((n for n in g.village.npcs if n.id == int(body.get("id", -1))), g.village.player)
                msg = "\n".join(plain_profile(g.village, t))
            elif cmd == "work_options":
                from omnisim.core.actions import work_options
                self._send(json.dumps({"options": work_options(g.village)}))
                return
            else:
                msg = f"unknown cmd {cmd}"
        except Exception as e:  # never black-screen the UI on sim errors
            msg = f"oops: {e}"
        self._send(json.dumps({"msg": msg}))

    def log_message(self, *a):
        pass


def main(port=PORT):
    game = Game()
    game.setup()
    Handler.game = game
    print(f"Thornhollow web UI on http://127.0.0.1:{port}  (Ctrl+C to close)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
