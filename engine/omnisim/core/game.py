"""Top-level game: owns the Village state, applies actions, drives the renderer.

Turn-based: every move/wait costs 1 turn (and rekindles sight + lantern light).
NPCs stand still for now — their agenda ticks arrive in Phase 2.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from omnisim.core import actions
from omnisim.systems.lighting import compute_light
from omnisim.ui.ascii_renderer import AsciiRenderer
from omnisim.ui.renderer import IRenderer
from omnisim.world.fov import refresh_visibility
from omnisim.world.worldgen_scripted import Village, generate_scripted

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "world_scripted"


@dataclass
class Game:
    renderer: IRenderer = field(default_factory=AsciiRenderer)
    village: Village | None = None

    def refresh_senses(self) -> None:
        assert self.village is not None
        refresh_visibility(self.village)
        self.village.light = compute_light(self.village)

    def setup(self) -> Village:
        self.village = generate_scripted(DATA_DIR)
        self.refresh_senses()
        return self.village

    def step_move(self, dx: int, dy: int) -> str:
        assert self.village is not None
        msg = actions.try_move(self.village, dx, dy)
        if msg:
            self.village.say(msg)
        self.refresh_senses()
        return msg

    def step_wait(self) -> str:
        assert self.village is not None
        msg = actions.wait(self.village)
        self.village.say(msg)
        self.refresh_senses()
        return msg

    def step_eat(self, prefer=None) -> str:
        assert self.village is not None
        msg = actions.eat(self.village, prefer)
        self.refresh_senses()
        if msg:
            self.village.say(msg)
        return msg

    def step_work_menu(self) -> str:
        """W opens the work menu: every task here, flow meter, or where to go."""
        from omnisim.core.actions import work_options
        from omnisim.ui.sidebar import nearest_work_sites
        assert self.village is not None
        v = self.village
        opts = work_options(v)
        if self.renderer.console is None:  # headless: engine decides, as always
            return self._step_simple(actions.work)
        rows = []
        flow_n = int(v.player.get("rhythm_n", 0))
        if flow_n > 1:
            left = "bonus banked!" if flow_n % 4 == 0 else f"{4 - flow_n % 4} more for +1 XP"
            rows.append(f"Flow x{flow_n} at the {v.player.get('rhythm_site')} ({left})")
            rows.append("")
        for i, o in enumerate(opts):
            tag = "" if o["ready"] else " [resting]"
            rows.append(f"{chr(97 + i)}) {o['label']} — {o['detail']}{tag}")
        if not opts:
            rows.append("No honest work where you stand.")
            for s in nearest_work_sites(v)[:4]:
                rows.append(f"  {s['name']} {s['dir']} ({s['verb']})")
        self.renderer.render_list(v, "Work", rows, footer="letter:work esc:close")
        if not opts:
            self.renderer.wait_any()
            return "You size up the day. (The board [M] knows where the work is.)"
        pick = self.renderer.choose(len(opts))
        if pick is None:
            return "You pocket your hands."
        if not opts[pick]["ready"]:
            return "That patch still needs its rest."
        return self._step_simple(actions.work)

    def step_board(self) -> str:
        """M: the village board — wants, labor, market, workplaces."""
        from omnisim.ui.sidebar import village_board
        assert self.village is not None
        v = self.village
        if self.renderer.console is None:
            return "\n".join(village_board(v))
        self.renderer.render_list(v, "Village board", village_board(v)[1:], footer="M:close")
        self.renderer.wait_any()
        return "You read the village like a ledger."

    def step_pack(self) -> str:
        """P: the pack, lettered. Pick a meal to eat it here."""
        from omnisim.entities.items import defs, pack_of
        assert self.village is not None
        v = self.village
        pack = pack_of(v.player)
        items = defs()["items"]
        rows = [(i, q) for i, q in sorted(pack.items()) if q > 0]
        if self.renderer.console is None:
            return "Pack: " + (", ".join(f"{i} x{q}" for i, q in rows) or "empty")
        nice = []
        for i, (item_id, q) in enumerate(rows):
            info = items.get(item_id, {})
            tag = f" ({info.get('food', 0)} food)" if info.get("food", 0) > 0 else ""
            nice.append(f"{chr(97 + i)}) {info.get('name', item_id)} x{q}{tag}")
        self.renderer.render_list(v, "Pack", nice or ["empty pockets"], footer="letter:eat esc:close")
        if not rows:
            self.renderer.wait_any()
            return "Empty pockets."
        pick = self.renderer.choose(len(rows))
        if pick is None:
            return "You tie the pack shut."
        return self.step_eat(rows[pick][0])

    def step_work(self) -> str:
        assert self.village is not None
        msg = actions.work(self.village)
        self.refresh_senses()
        if msg:
            self.village.say(msg)
        return msg

    def step_watch(self) -> str:
        assert self.village is not None
        ids = [None] + [n.id for n in self.village.npcs if not n.comps.get("dead")]
        try:
            cur = ids.index(self.village.watch_id)
        except ValueError:
            cur = 0
        nxt = ids[(cur + 1) % len(ids)]
        self.village.watch_id = nxt
        if nxt is None:
            msg = "You stop watching."
        else:
            npc = next(n for n in self.village.npcs if n.id == nxt)
            msg = f"You watch {npc.name} the {npc.get('role', 'someone')}."
        self.village.say(msg)
        return msg

    def step_inspect(self) -> str:
        assert self.village is not None
        v = self.village
        target = next((n for n in v.npcs if n.id == v.watch_id), None)
        if target is None:
            best, best_d = None, 9
            for n in v.npcs:
                if not v.game_map.in_bounds(n.x, n.y) or not v.game_map.visible[n.y, n.x]:
                    continue
                d = max(abs(n.x - v.player.x), abs(n.y - v.player.y))
                if d < best_d:
                    best, best_d = n, d
            target = best
        if target is None:
            target = v.player
            if self.renderer.console is not None:
                self.renderer.render_profile(v, target)
                self.renderer.wait_any()
            return "You take stock of yourself."
        if self.renderer.console is not None:
            rows = self.renderer.render_inspect(v, target)
            if rows and target.get("role") != "player" and not target.comps.get("dead"):
                pick = self.renderer.choose(len(rows))
                if pick is not None:
                    return self.step_steal(rows[pick][0])
            else:
                self.renderer.wait_any()
        return f"You study {target.name} the {target.get('role', 'someone')}."

    def step_get(self) -> str:
        assert self.village is not None
        msg = actions.get_items(self.village)
        self.refresh_senses()
        if msg:
            self.village.say(msg)
        return msg

    def _step_simple(self, fn) -> str:
        assert self.village is not None
        msg = fn(self.village)
        self.refresh_senses()
        if msg:
            self.village.say(msg)
        return msg

    def step_buy(self, prefer=None) -> str:
        assert self.village is not None
        msg = actions.buy(self.village, prefer)
        self.refresh_senses()
        if msg:
            self.village.say(msg)
        return msg

    def step_sell(self, prefer=None) -> str:
        assert self.village is not None
        msg = actions.sell(self.village, prefer)
        self.refresh_senses()
        if msg:
            self.village.say(msg)
        return msg

    def step_sleep(self) -> str:
        return self._step_simple(actions.sleep_till_dawn)

    def step_steal(self, item=None) -> str:
        assert self.village is not None
        msg = actions.steal(self.village, item)
        self.refresh_senses()
        if msg:
            self.village.say(msg)
        return msg

    def step_give(self, item=None) -> str:
        assert self.village is not None
        msg = actions.give(self.village, item)
        self.refresh_senses()
        if msg:
            self.village.say(msg)
        return msg

    def step_repair(self) -> str:
        return self._step_simple(actions.repair_station)

    def step_drink(self) -> str:
        return self._step_simple(actions.drink)

    def step_wash(self) -> str:
        return self._step_simple(actions.wash)

    def step_build_fire(self) -> str:
        return self._step_simple(actions.build_campfire)

    def step_build_cabin(self) -> str:
        return self._step_simple(actions.build_cabin)

    def step_chest(self, item=None, take=False) -> str:
        assert self.village is not None
        msg = actions.chest_use(self.village, item, take)
        self.refresh_senses()
        if msg:
            self.village.say(msg)
        return msg

    def run(self) -> None:
        v = self.setup()
        self.renderer.init_for(v.game_map.width, v.game_map.height)
        try:
            while True:
                self.renderer.render(v)
                cmd = self.renderer.wait_action()
                if cmd[0] == "quit":
                    break
                elif cmd[0] == "wait":
                    self.step_wait()
                elif cmd[0] == "eat":
                    self.step_eat()
                elif cmd[0] == "get":
                    self.step_get()
                elif cmd[0] == "work":
                    self.step_work_menu()
                elif cmd[0] == "board":
                    self.step_board()
                elif cmd[0] == "pack":
                    self.step_pack()
                elif cmd[0] == "watch":
                    self.step_watch()
                elif cmd[0] == "buy":
                    self.step_buy()
                elif cmd[0] == "sell":
                    self.step_sell()
                elif cmd[0] == "sleep":
                    self.step_sleep()
                elif cmd[0] == "steal":
                    self.step_steal()
                elif cmd[0] == "give":
                    self.step_give()
                elif cmd[0] == "build_fire":
                    self.step_build_fire()
                elif cmd[0] == "build_cabin":
                    self.step_build_cabin()
                elif cmd[0] == "chest":
                    self.step_chest()
                elif cmd[0] == "move":
                    self.step_move(cmd[1], cmd[2])
                elif cmd[0] == "zoom":
                    self.renderer.zoom(cmd[1], v.game_map.width, v.game_map.height)
                elif cmd[0] == "inspect":
                    self.step_inspect()
                from omnisim.systems.barks import check_barks
                check_barks(v)
        finally:
            self.renderer.shutdown()
        print(f"Left {v.name} after {v.turn} turns. Farewell, traveller.")



