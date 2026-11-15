"""The play API: sit at the table like a player, not a scripter.

PlaySession wraps a live Game with a command language and a player-view —
the same ASCII, sidebar, and menu a human sees. Served over localhost by
play/server.py so the game persists across turns (one process, many calls).

Commands (do("...")):
  n/s/e/w/ne/nw/se/sw          step (honest 1-turn moves)
  walk <seq>                   stacked steps, e.g. "walk e10 n5" (stops on block)
  go <where>                   auto-walk, turn by turn (fields/pond/market/
                               tavern/anvil/bench/hearth/stall/<visible npc>)
  wait [n] / work [n]          batches report every distinct outcome, not just the last
  eat [item] / get / buy [item] / sell / sleep / watch
  inspect [name]               study someone (or yourself)
  board / pack                 village board / your pack
  steal [item] / give [item]   adjacent pockets only, like hands
  campfire / cabin / chest     build + storage
  day                          wait until next dawn, with an overnight digest
  look                         just look again

Parity rule: batching only queues honest steps. Every leg costs what your
legs cost; eyes see only visible tiles; go-to-name needs line of sight.
"""
from __future__ import annotations
from collections import deque

DIRS = {"n": (0, -1), "s": (0, 1), "e": (1, 0), "w": (-1, 0),
        "ne": (1, -1), "nw": (-1, -1), "se": (1, 1), "sw": (-1, 1)}


class PlaySession:
    def __init__(self) -> None:
        from omnisim.core.game import Game
        self.game = Game()
        self.game.setup()
        self.last = "Welcome to Thornhollow."

    @property
    def v(self):
        return self.game.village

    # -- the player-view: everything a human at the screen knows --
    def view(self) -> dict:
        from omnisim.core.clock import day_of, fmt_clock
        from omnisim.entities.items import pack_of
        from omnisim.ui.sidebar import available_actions, plain
        from omnisim.world.worldgen_scripted import render_ascii
        v = self.v
        p = v.player
        gm = v.game_map
        near = []
        for n in v.npcs:
            if not gm.in_bounds(n.x, n.y) or not gm.visible[n.y, n.x]:
                continue  # parity: no wallhacks — unseen is unseen
            d = max(abs(n.x - p.x), abs(n.y - p.y))
            if d > 8:
                continue
            near.append({"name": n.name, "role": n.get("role"), "x": n.x, "y": n.y,
                         "activity": n.get("activity"), "hp": n.get("hp"),
                         "hunger": round(float(n.get("hunger", 0))),
                         "fatigue": round(float(n.get("fatigue", 0))),
                         "dead": bool(n.comps.get("dead"))})
        return {"clock": fmt_clock(v.clock_pts), "day": day_of(v.clock_pts),
                "turn": v.turn, "at": (p.x, p.y),
                "hp": (p.get("hp"), p.get("max_hp")),
                "hunger": round(float(p.get("hunger", 0))),
                "thirst": round(float(p.get("thirst", 0))),
                "pack": dict(pack_of(p)), "skills": dict(p.get("skills", {})),
                "map": render_ascii(v), "side": plain(v, 60, 30),
                "menu": available_actions(v), "nearby": near,
                "log": v.log[-8:], "msg": self.last}

    # -- walking like legs, not teleports --
    def _path(self, tx: int, ty: int, limit: int = 200) -> list:
        from omnisim.systems.schedule import bfs_path
        gm = self.v.game_map
        if not gm.in_bounds(tx, ty):
            return []
        if (self.v.player.x, self.v.player.y) == (tx, ty):
            return []
        return bfs_path(gm, (self.v.player.x, self.v.player.y), (tx, ty))[:limit]

    def _dest_named(self, where: str):
        from omnisim.systems.schedule import standable_near
        from omnisim.ui.sidebar import nearest_work_sites
        w = where.lower()
        for s in nearest_work_sites(self.v):
            if w in s["name"].lower() or w in s["verb"].lower():
                from omnisim.systems.schedule import passable, standable_near
                dest = (s["x"], s["y"])
                if not passable(self.v.game_map, *dest):
                    dest = standable_near(self.v.game_map, *dest)
                return dest, s["name"]
        for n in self.v.npcs:
            if w in n.name.lower() and not n.comps.get("dead"):
                gm = self.v.game_map
                if gm.in_bounds(n.x, n.y) and gm.visible[n.y, n.x]:
                    return standable_near(self.v.game_map, n.x, n.y), n.name
                return "unseen", n.name  # parity: no walking to the unseen
        if w in ("market", "stall", "stalls") and self.v.stalls:
            s = self.v.stalls[0]
            return standable_near(self.v.game_map, s["x"], s["y"]), "Market stall"
        return None, None

    def _seen_ids(self) -> set:
        gm = self.v.game_map
        p = self.v.player
        return {n.id for n in self.v.npcs
                if gm.in_bounds(n.x, n.y) and gm.visible[n.y, n.x]
                and max(abs(n.x - p.x), abs(n.y - p.y)) <= 8}

    def _sightings(self, before: set) -> list:
        now = {n.id: n for n in self.v.npcs}
        fresh = self._seen_ids() - before
        return [f"{now[i].name} ({now[i].get('activity', 'idle')})" for i in sorted(fresh) if i in now]

    def _walk_steps(self, steps: list, cap: int = 200) -> tuple:
        """Honest legs, one step_move each. Returns (taken, sightings, stop)."""
        seen_before = self._seen_ids()
        sighted, taken, stop = [], 0, ""
        for dx, dy in steps[:cap]:
            px, py = self.v.player.x, self.v.player.y
            msg = self.game.step_move(dx, dy)
            taken += 1
            for s in self._sightings(seen_before):
                if s not in sighted:
                    sighted.append(s)
            seen_before = self._seen_ids()
            if (self.v.player.x, self.v.player.y) == (px, py):
                stop = msg or "Blocked."
                break
        return taken, sighted, stop

    def walk_seq(self, seq: str) -> str:
        """Stacked steps, e.g. 'e10 n5': queued honest legs, stops on block."""
        import re
        steps = []
        for tok in seq.lower().split():
            m = re.fullmatch(r"(ne|nw|se|sw|n|s|e|w)(\d*)", tok)
            if not m:
                return f"Bad leg '{tok}'. (Like: walk e10 n5)"
            dx, dy = DIRS[m.group(1)]
            steps += [(dx, dy)] * (int(m.group(2)) if m.group(2) else 1)
        taken, sighted, stop = self._walk_steps(steps)
        bits = [f"Walked {taken} of {len(steps)}."]
        if stop and taken < len(steps):
            bits.append(stop)
        if sighted:
            bits.append("Saw: " + ", ".join(sighted) + ".")
        self.last = " ".join(bits)
        return self.last

    @staticmethod
    def _transcript(msgs: list) -> str:
        """Batches report every distinct outcome, in order — never just the last."""
        seen, out = [], []
        for m in msgs:
            m = m or "(quiet)"
            if m not in seen:
                seen.append(m)
                out.append(f"{m} ×{msgs.count(m)}" if msgs.count(m) > 1 else m)
        return "; ".join(out)

    def _digest(self, new_lines: list) -> str:
        """Overnight news that matters: deaths, careers, bellies, levels, trade."""
        import re
        deaths = [m for m in new_lines if "starved" in m or "carried off" in m]
        flex = [m for m in new_lines if "takes up" in m]
        hungry = [m for m in new_lines if "nothing to eat" in m or "screams" in m]
        levels = sorted({m for m in new_lines if "rises to" in m})
        sales = sum(1 for m in new_lines if "unloads" in m)
        coins = sum(int(x) for m in new_lines if "unloads" in m
                    for x in re.findall(r"\+(\d+)c", m))
        bits = []
        if deaths:
            bits.append("Deaths: " + "; ".join(deaths))
        if flex:
            bits.extend(flex)
        if hungry:
            bits.append(f"Hungry: {len(hungry)} bellies complained.")
        if levels:
            bits.extend(levels)
        if sales:
            bits.append(f"Market: {sales} unloadings (+{coins}c to pockets).")
        return " ".join(bits) if bits else "A quiet turning of the world."

    def go(self, where: str) -> str:
        dest, name = self._dest_named(where)
        if dest == "unseen":
            return f"You can't see {name} from here."
        if dest is None:
            return f"Nowhere called '{where}'. (Try: fields, pond, market, tavern, anvil, bench, hearth, wilds, or someone you can see.)"
        taken_total, sighted, stop = 0, [], ""
        for _plan in range(3):  # repath like legs do when someone blocks the way
            if (self.v.player.x, self.v.player.y) == dest:
                break
            trail = self._path(*dest)
            if not trail:
                stop = f"Can't reach the {name} on foot."
                break
            legs = [(trail[i][0] - (self.v.player.x if i == 0 else trail[i - 1][0]),
                     trail[i][1] - (self.v.player.y if i == 0 else trail[i - 1][1]))
                    for i in range(len(trail))]
            taken, fresh, stop = self._walk_steps(legs)
            taken_total += taken
            for s in fresh:
                if s not in sighted:
                    sighted.append(s)
            if (self.v.player.x, self.v.player.y) == dest or not stop:
                break
        arrived = (self.v.player.x, self.v.player.y) == dest
        bits = [f"Walked {taken_total} toward the {name}."]
        if arrived:
            bits.append("Arrived.")
        elif stop:
            bits.append(stop)
        if sighted:
            bits.append("Saw: " + ", ".join(sighted) + ".")
        self.last = " ".join(bits)
        return self.last

    # -- the command language --
    def do(self, text: str) -> dict:
        parts = text.strip().split()
        if not parts:
            return self._out("Say what? (n/s/e/w, go <where>, work, eat, board, ...)")
        cmd, args = parts[0].lower(), parts[1:]
        g = self.game
        n = int(args[0]) if args and args[0].isdigit() else 1
        word = args[0] if args and not args[0].isdigit() else None
        try:
            if cmd in DIRS:
                self.last = g.step_move(*DIRS[cmd])
            elif cmd == "walk":
                self.last = self.walk_seq(" ".join(args))
            elif cmd == "go":
                self.last = self.go(" ".join(args))
            elif cmd == "wait":
                msgs = [g.step_wait() for _ in range(min(n, 200))]
                self.last = self._transcript(msgs)
            elif cmd == "work":
                msgs = [g.step_work() for _ in range(min(n, 50))]
                self.last = self._transcript(msgs)
            elif cmd == "eat":
                self.last = g.step_eat(word)
            elif cmd == "get":
                self.last = g.step_get()
            elif cmd == "buy":
                self.last = g.step_buy(word)
            elif cmd == "sell":
                self.last = g.step_sell(word)
            elif cmd == "sleep":
                self.last = g.step_sleep()
            elif cmd == "watch":
                self.last = g.step_watch()
            elif cmd == "inspect":
                self.last = self._inspect(" ".join(args))
            elif cmd == "board":
                self.last = g.step_board()
            elif cmd == "pack":
                self.last = g.step_pack()
            elif cmd == "steal":
                self.last = g.step_steal(word)
            elif cmd == "give":
                self.last = g.step_give(word)
            elif cmd == "repair":
                self.last = g.step_repair()
            elif cmd == "drink":
                self.last = g.step_drink()
            elif cmd == "wash":
                self.last = g.step_wash()
            elif cmd == "campfire":
                self.last = g.step_build_fire()
            elif cmd == "cabin":
                self.last = g.step_build_cabin()
            elif cmd == "chest":
                self.last = g.step_chest(word, "take" in args)
            elif cmd == "day":
                self.last = self._until_dawn()
            elif cmd == "look":
                self.last = "You look around."
            else:
                self.last = f"No such move. (n/s/e/w, walk e10 n5, go, wait, work, eat, get, buy, sell, sleep, watch, inspect, board, pack, steal, give, campfire, cabin, chest, day, look)"
        except Exception as e:
            self.last = f"oops: {e}"
        return self._out(self.last)

    def _inspect(self, who: str) -> str:
        from omnisim.ui.sidebar import plain_profile
        v = self.v
        gm = v.game_map
        t = None
        if who:
            t = next((n for n in v.npcs if who.lower() in n.name.lower()
                      and gm.in_bounds(n.x, n.y) and gm.visible[n.y, n.x]), None)
        if t is None:  # parity with i: nearest VISIBLE, else yourself
            best, best_d = None, 9
            for n in v.npcs:
                if not gm.in_bounds(n.x, n.y) or not gm.visible[n.y, n.x]:
                    continue
                d = max(abs(n.x - v.player.x), abs(n.y - v.player.y))
                if d < best_d:
                    best, best_d = n, d
            t = best or v.player
        lines = plain_profile(v, t)
        if t is not v.player:
            from omnisim.ui.sidebar import inspectable_pocket
            from omnisim.entities.items import item_name
            rows = inspectable_pocket(t)
            if rows:
                lines.append("Carrying (liftable): " + ", ".join(f"{item_name(i)} ×{q}" for i, q in rows))
        return "\n".join(lines)

    def _until_dawn(self) -> str:
        from omnisim.core.clock import DAWN_MIN, MIN_PER_DAY, PTS_PER_MIN
        day_len = MIN_PER_DAY * PTS_PER_MIN
        before = (self.v.clock_pts - DAWN_MIN * PTS_PER_MIN) // day_len
        n0 = len(self.v.log)
        for _ in range(300):
            self.game.step_wait()
            after = (self.v.clock_pts - DAWN_MIN * PTS_PER_MIN) // day_len
            if after != before:
                break
        return f"Dawn comes. {self._digest(self.v.log[n0:])}"

    def _out(self, msg: str) -> dict:
        out = self.view()
        out["msg"] = msg
        return out
