"""Native SDL 40-column sword duel. Reads RenderModel; writes controller commands.

No combat formulas, simulation mutation, or hidden enemy state belongs here.
"""
import re
import textwrap
import time
from dataclasses import replace

import numpy as np
from PIL import Image, ImageDraw
import tcod.event
from tcod.event import KeySym

from pirates.duel_system.engine import Choice
from pirates.duel_system.model import ActionKind, Zone
from pirates.duel_system.render_model import format_ascii, map_lines
from pirates.duel_system.session import DuelSession
from pirates.duel_system.exchange_log import format_exchange, summarize_exchange
from pirates.duel_coach import coach, strategy_rows


DIRECTIONS = {KeySym.UP: (0, -1), KeySym.DOWN: (0, 1), KeySym.LEFT: (-1, 0), KeySym.RIGHT: (1, 0),
              KeySym.KP_1: (-1, 1), KeySym.KP_2: (0, 1), KeySym.KP_3: (1, 1),
              KeySym.KP_4: (-1, 0), KeySym.KP_6: (1, 0), KeySym.KP_7: (-1, -1),
              KeySym.KP_8: (0, -1), KeySym.KP_9: (1, -1)}


def pickup_options(model):
    """Stable, read-only selectable items currently within weapon pickup range."""
    if model.player_hand_severed:
        return ()
    player = model.player_pos
    return tuple(row for row in model.ground_weapons
                 if max(abs(player[0]-row[2][0]), abs(player[1]-row[2][1])) <= model.pickup_range
                 and row[3] > 0)


class DuelUI:
    def __init__(self, session, *, rounds=False, coach_enabled=False):
        self.session = session
        self.rounds = rounds
        self.round_result = ()
        self.round_summary = ()
        self.stat_name, self.stat_enemy = "Speed", False
        self.coach_enabled = coach_enabled
        if rounds and session.inputs:
            starts = [index for index, event in enumerate(session.state.event_log)
                      if event.type.value == "ACTION_START" and event.actor == session.player.id
                      and event.tick == session.inputs[-1]["tick"]]
            if starts:
                self.round_result = format_exchange(session.state.event_log[starts[-1]:],
                    {fighter.id: "You" if fighter.id == session.player.id else fighter.name for fighter in session.state.fighters})
                self.round_summary = summarize_exchange(session.state.event_log[starts[-1]:],
                    {fighter.id: "You" if fighter.id == session.player.id else fighter.name for fighter in session.state.fighters},
                    deep_threshold=session.state.config['wound_net']['deep'])
        self.playback = ()
        self._model_cache = None
        self.panel = "main"
        self.kind = ActionKind.LIGHT
        self.direction = (1, 0)
        self.tiles = 1
        self.pending = None
        self.target_id = None
        self.target_page = 0
        self._dash_light_choices = {}
        self.closed = self.quit = False
        self.timeline = True
        self.manual = False
        self.scroll = 0
        self.message = ("Choose one action. Each active fighter acts once per exchange."
                        if rounds and len(session.state.fighters) > 2 else
                        "Choose one action. Both fighters act once per exchange." if rounds else
                        "Arrows: move preview | Space: light | H: help | Esc: pause")

    def model(self):
        state = self.session.state
        # The live decision state is paused. Include mutable fighter fields so
        # debug/test edits at the same tick cannot leave stale resource cards.
        key = (id(self.session), state.tick, len(state.event_log), repr(state.fighters),
               self.session.selected, self.session.guide_mode, self.timeline, state.end_reason, state.winner,
               self.panel, self.target_id, self.target_page,
               self.panel == 'zones' and self.kind == ActionKind.HEAVY)
        if self._model_cache is None or self._model_cache[0] != key:
            # The round dashboard displays observed outcomes, not a tactical
            # recommendation. Never run its unused combinatorial solver here.
            self._model_cache = (key, self.session.render_model(timeline=self.timeline, include_advice=not self.rounds))
            if self.panel == 'zones' and self.kind == ActionKind.HEAVY and not (self.session.selected and self.session.selected.kind == ActionKind.HEAVY):
                base = self._model_cache[1]
                extra = dict(base.combat_rules).get('heavy_accuracy', 0)
                self._model_cache = (key, replace(base, zones=tuple(replace(z, difficulty=z.difficulty+extra) for z in base.zones)))
        return self._model_cache[1]

    def _preview(self, choice):
        if self.rounds:
            before = {f.id: (f.blood, f.stamina, f.fatigue) for f in self.session.state.fighters}
            cursor = len(self.session.state.event_log)
            preview = self.session.select(choice)
            self.session.cancel()
            if not preview.valid:
                self.message = preview.error
                return
            frames = []
            from pirates.duel_system.render_model import build_render_model
            def observe():
                frames.append(build_render_model(self.session.state, self.session.notebook.public_view(), timeline=False,
                                                guide_mode=self.session.guide_mode, playback=True))
            if self.session.resolve_round(choice, observe=observe):
                self.playback = tuple(frames)
                names = {f.id: "You" if f.id == self.session.player.id else f.name for f in self.session.state.fighters}
                events = self.session.state.event_log[cursor:]
                self.round_result = format_exchange(events, names, before=before, fighters=self.session.state.fighters)
                self.round_summary = summarize_exchange(events, names, deep_threshold=self.session.state.config['wound_net']['deep'])
                self.message = "Round resolved. Choose your next action."
                self.panel, self.pending = "main", None
            else:
                self.message = "Cannot act yet."
            return
        if not self.session.ready:
            self.message = self.model().instructions
            self.panel, self.pending = "main", None
            self.session.cancel()
            return
        if self.pending == choice and self.panel == "preview":
            self._confirm()
            return
        self.pending = choice
        preview = self.session.select(choice)
        self.panel = "preview"
        self.message = "Enter confirms; Esc cancels." if preview.valid else preview.error

    def _confirm(self):
        if self.session.confirm():
            self.panel, self.pending = "main", None
            self.target_id = None
            self.message = "Resolving your committed action..."
        else:
            self.message = "Cannot commit. Check the preview explanation."

    def activate(self, label):
        if label == "Coach" or label == "Guide" and self.rounds:
            self.coach_enabled = not self.coach_enabled
            self.message = "Strategy coach " + ("enabled: conditional advice, no future enemy choices." if self.coach_enabled else "off.")
            return
        if label == "Strategy":
            self.panel, self.scroll = "strategy", 0
            return
        if label.startswith(("Stat:", "Enemy stat:")):
            from pirates.duel_stat_help import STATS
            name = label.split(":", 1)[1]
            if name in STATS:
                self.stat_name, self.stat_enemy = name, label.startswith("Enemy")
                self.panel, self.scroll = "stat", 0
            return
        if label == "Details":
            self.panel, self.scroll = "details", 0
            return
        compass = {"NW": (-1, -1), "N": (0, -1), "NE": (1, -1), "W": (-1, 0),
                   "E": (1, 0), "SW": (-1, 1), "S": (0, 1), "SE": (1, 1)}
        if label in compass and self.panel == "direction":
            self.choose_direction(compass[label])
            return
        if label in {"1 tile", "2 tiles", "3 tiles"}:
            self.tiles = int(label[0])
            self.message = f"Distance set to {self.tiles} tile{'s' if self.tiles != 1 else ''}. Choose a direction to resolve the exchange."
            return
        if label.startswith("Pick "):
            if not self.session.ready:
                self.message = self.model().instructions
                return
            try:
                index = int(label.split()[-1]) - 1
                item = pickup_options(self.model())[index]
            except (ValueError, IndexError):
                self.message = "That ground weapon is no longer available."
                return
            self._preview(Choice(ActionKind.PICKUP, item_id=item[0]))
            return
        if label.startswith("Target "):
            try:
                index = int(label.split()[-1]) - 1
                target_options = self._current_target_options()
                target = target_options[index]
            except (ValueError, IndexError):
                self.message = "That target is no longer visible."
                return
            self.target_id = target.actor_id
            if self.kind == ActionKind.DASH_LIGHT:
                choice = self._dash_light_choices.get(self.target_id)
                if choice is None:
                    self.message = "That hostile is no longer in Dash+Light range."
                    self.panel = "main"
                    return
                self.direction, self.tiles = choice.direction, choice.tiles
                self.panel = "zones"
                self.message = (f"Dash+Light target locked: {target.name}. "
                                "The approach path is automatic; choose a strike zone.")
            elif self.kind in {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.THRUST}:
                self.panel = "zones"
            elif self.kind in {ActionKind.DASH, ActionKind.RUN, ActionKind.WALK,
                               ActionKind.DISENGAGE, ActionKind.CRAWL}:
                self.panel = "direction"
            else:
                aim = Zone.HAND if self.kind == ActionKind.DISARM else None
                self._preview(Choice(self.kind, aim, target_id=self.target_id))
            return
        if label in {"Prev Targets", "Next Targets"}:
            count = len(self._current_target_options())
            self.target_page = max(0, min((count-1)//5, self.target_page + (-1 if label == "Prev Targets" else 1)))
            self._model_cache = None
            return
        combat = {"Light", "Heavy", "Guard", "Dash", "Move", "Run", "Dash+Light", "Wait",
                  "Feint", "Shove", "Disengage", "Thrust", "Disarm", "Adrenaline",
                  "Rise", "Crawl", "Cover",
                  "Bandage", "Eat", "Drink", "Pickup", "Eye", "Head", "Torso", "Arm", "Hand", "Leg"}
        unavailable = next((option for option in self.model().actions
                            if option.label == label and not option.enabled), None)
        if unavailable is not None:
            self.message = unavailable.details
            return
        if label in combat and not self.session.ready and not (label in {"Guard", "Cover"} and self.session.holding_guard):
            self.message = self.model().instructions
            self.panel, self.pending = "main", None
            self.session.cancel()
            return
        if label in {"Light", "Heavy", "Thrust"}:
            self.target_id = None
            self.kind = {"Light": ActionKind.LIGHT, "Heavy": ActionKind.HEAVY,
                         "Thrust": ActionKind.THRUST}[label]
            self._choose_target_panel("zones")
        elif label == "Dash+Light":
            self.kind = ActionKind.DASH_LIGHT
            self._begin_dash_light()
        elif label in {"Dash", "Move", "Run", "Disengage"}:
            self.target_id = None
            self.kind = {"Dash": ActionKind.DASH, "Move": ActionKind.WALK, "Run": ActionKind.RUN,
                         "Disengage": ActionKind.DISENGAGE}[label]
            self.tiles = 1 if self.kind in {ActionKind.WALK, ActionKind.DISENGAGE} else 3
            if self.kind == ActionKind.DISENGAGE:
                self._choose_target_panel("direction")
            else:
                self.panel = "direction"
            self.message = (f"{label}: {self.tiles} tiles selected. Choose 1/2/3 tiles before direction if needed; "
                            "an occupied tile or arena edge rejects the route.")
        elif label in {"Guard", "Cover"}:
            if self.session.holding_guard:
                self.session.release_guard()
                self.pending, self.panel = None, "main"
            else:
                self._preview(Choice(ActionKind.GUARD))
        elif label == "Rise":
            self.target_id = None
            self._preview(Choice(ActionKind.RISE))
        elif label == "Crawl":
            self.target_id = None
            self.kind, self.tiles, self.panel = ActionKind.CRAWL, 1, "direction"
            self.message = "Crawl one slow tile. Choose a clear direction; you remain grounded."
        elif label in {"Feint", "Shove"}:
            self.target_id = None
            self.kind = ActionKind(label.upper())
            self._choose_target_panel("preview")
        elif label == "Disarm":
            self.target_id = None
            self.kind = ActionKind.DISARM
            self._choose_target_panel("preview")
        elif label == "Adrenaline":
            self._preview(Choice(ActionKind.ADRENALINE))
        elif label == "Pickup":
            self.panel, self.scroll = "pickup", 0
            self.message = "Select a nearby usable weapon; replacing one drops your current weapon here."
        elif label in {"Wait", "Bandage", "Eat", "Drink"}:
            self._preview(Choice(ActionKind(label.upper())))
        elif label in {"More", "Tactics", "Notebook", "Log", "Help", "Sheet"}:
            self.panel = label.lower()
            self.scroll = 0
        elif label == "Guide":
            mode = self.model().guide_mode
            self.session.guide_mode = {"off": "hints", "hints": "full", "full": "off"}[mode]
            self.message = f"Guide: {self.session.guide_mode}"
        elif label == "Timeline":
            self.timeline = not self.timeline
        elif label == "Confirm":
            self._confirm()
        elif label == "Back":
            self.panel, self.pending = "main", None
            self.target_id = None
            self._dash_light_choices = {}
            self.session.cancel()
        elif label == "Retry":
            self.session = self.session.restart()
            self.panel, self.pending = "main", None
            self.round_result = ()
            self.round_summary = ()
            self.playback = ()
            self.message = "New duel. Choose one action."
        elif label == "Next":
            try:
                self.session = self.session.restart(carry=True)
                self.panel, self.pending = "main", None
                self.round_result = ()
                self.round_summary = ()
            except ValueError as error:
                self.message = str(error)
        elif label in {"Eye", "Head", "Torso", "Arm", "Hand", "Leg"}:
            dash_choice = (self._dash_light_choices.get(self.target_id)
                           if self.kind == ActionKind.DASH_LIGHT else None)
            self._preview(Choice(self.kind, Zone(label.upper()),
                                 dash_choice.direction if dash_choice else None,
                                 dash_choice.tiles if dash_choice else 1,
                                 target_id=self.target_id))

    def _current_target_options(self):
        options = self.model().target_options
        if self.panel == "targets" and self.kind == ActionKind.DASH_LIGHT:
            return tuple(option for option in options if option.actor_id in self._dash_light_choices)
        return options

    def _dash_light_path(self, target_id):
        """Choose the shortest clear straight dash that ends in strike reach."""
        from pirates.duel_system.engine import legality, movement_path
        from pirates.duel_system.stats import weapon_reach

        state, player = self.session.state, self.session.player
        target = state.fighter(target_id)
        before = max(abs(player.pos[0]-target.pos[0]), abs(player.pos[1]-target.pos[1]))
        reach = weapon_reach(player, state.config)
        maximum = state.config["actions"][ActionKind.DASH_LIGHT.value]["max_tiles"]
        dx_target, dy_target = target.pos[0]-player.pos[0], target.pos[1]-player.pos[1]
        options = []
        for dx, dy in ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)):
            for tiles in range(1, maximum+1):
                choice = Choice(ActionKind.DASH_LIGHT, Zone.TORSO, (dx, dy), tiles,
                                target_id=target_id)
                if legality(state, player.id, choice):
                    continue
                endpoint = movement_path(state, player, choice)[-1]
                if any(other.id != player.id and state.occupies_tile(other) and other.pos == endpoint
                       for other in state.fighters):
                    continue
                after = max(abs(endpoint[0]-target.pos[0]), abs(endpoint[1]-target.pos[1]))
                if after > reach or after > before:
                    continue
                deviation = abs(dx*dy_target - dy*dx_target)
                options.append(((tiles, after, deviation, dx, dy), choice))
        return min(options, key=lambda item: item[0])[1] if options else None

    def _begin_dash_light(self):
        model = self.model()
        self.target_id = None
        self._dash_light_choices = {}
        for target in model.target_options:
            choice = self._dash_light_path(target.actor_id)
            if choice is not None:
                self._dash_light_choices[target.actor_id] = choice
        if not self._dash_light_choices:
            if not model.target_options:
                self.message = "No visible hostile in the Dash+Light target area."
            else:
                self.message = ("Out of range or no clear Dash+Light approach. "
                                "Use Dash/Run to close, or Light if already in reach.")
            self.panel = "main"
        elif len(self._dash_light_choices) == 1:
            target_id = next(iter(self._dash_light_choices))
            target = next(row for row in model.target_options if row.actor_id == target_id)
            self.target_id = target_id
            choice = self._dash_light_choices[target_id]
            self.direction, self.tiles = choice.direction, choice.tiles
            self.panel = "zones"
            self.message = (f"Dash+Light target locked: {target.name}. "
                            "The approach path is automatic; choose a strike zone.")
        else:
            self.panel = "targets"
            self.target_page = 0
            self.message = ("Choose a visible hostile in the Dash+Light target area. "
                            "Direction and distance are chosen automatically.")

    def _choose_target_panel(self, destination):
        options = self.model().target_options
        if not options:
            self.message = "No visible living hostile to target."
            self.panel = "main"
            return
        if len(options) > 1:
            self.panel = "targets"
            self.target_page = 0
            self.message = "Choose one visible hostile; attacks never switch targets."
            return
        self.target_id = options[0].actor_id if len(self.session.state.fighters) > 2 else None
        if destination == "preview":
            aim = Zone.HAND if self.kind == ActionKind.DISARM else None
            self._preview(Choice(self.kind, aim, target_id=self.target_id))
        else:
            self.panel = destination

    def handle_key(self, sym, *, repeat=False):
        if repeat:
            return
        if self.rounds and sym in {KeySym.N, KeySym.A}:
            self.message = "Choose one action. Rounds resolve automatically; no tick stepping."
            return
        if sym in {KeySym.PAGEUP, KeySym.PAGEDOWN}:
            page = dict(self.model().presentation)["panel_page_rows"]
            self.scroll = max(0, self.scroll + (page if sym == KeySym.PAGEDOWN else -page))
            return
        if sym == KeySym.ESCAPE:
            if self.panel != "main" or self.pending:
                self.activate("Back")
            else:
                self.closed = True  # Pause/resume the duel; not a fake map-exit victory/flee.
            return
        if self.rounds and sym == KeySym.I:
            self.activate("Coach")
            return
        if self.panel in {"details", "stat", "sheet", "strategy"}:
            return  # Inspecting numbers must not accidentally commit movement.
        if sym == KeySym.BACKSPACE:
            self.activate("Retry")
            return
        if self.panel == "zones":
            zones = {KeySym.E: "Eye", KeySym.H: "Head", KeySym.T: "Torso", KeySym.A: "Arm", KeySym.X: "Hand", KeySym.L: "Leg"}
            if sym in zones:
                self.activate(zones[sym])
            return
        if sym in {KeySym.N1, KeySym.N2, KeySym.N3} and self.panel == "direction":
            self.tiles = {KeySym.N1: 1, KeySym.N2: 2, KeySym.N3: 3}[sym]
            return
        if sym in DIRECTIONS:
            self.choose_direction(DIRECTIONS[sym])
            return
        if sym == KeySym.RETURN:
            if self.session.holding_guard and self.panel == "main":
                self.session.hold_guard()
            elif self.panel == "preview":
                self._confirm()
            else:
                self._preview(Choice(ActionKind.WAIT))
            return
        commands = {KeySym.SPACE: "Light", KeySym.F: "Heavy", KeySym.G: "Guard", KeySym.D: "Dash",
                    KeySym.M: "Move", KeySym.R: "Run", KeySym.J: "Dash+Light", KeySym.PERIOD: "Wait",
                    KeySym.T: "Tactics", KeySym.C: "More", KeySym.K: "Notebook", KeySym.L: "Log", KeySym.H: "Help",
                    KeySym.I: "Guide", KeySym.TAB: "Timeline", KeySym.B: "Bandage", KeySym.E: "Eat",
                    KeySym.W: "Drink", KeySym.P: "Pickup", KeySym.V: "Next", KeySym.O: "Details"}
        if self.panel == "main" and self.session.player.grounded:
            grounded_commands = {KeySym.U: "Rise", KeySym.C: "Crawl", KeySym.G: "Cover"}
            if sym in grounded_commands:
                self.activate(grounded_commands[sym])
                return
        if sym == KeySym.N:
            self.manual = True
            self.session.step()
            self.message = "Single tick. N: step | A: resume automatic time"
        elif sym == KeySym.A:
            self.manual = False
        elif sym in commands:
            self.activate(commands[sym])

    def choose_direction(self, direction):
        if self.panel == "direction":
            self.direction = direction
            if self.kind == ActionKind.DASH_LIGHT:
                self.panel = "zones"
            else:
                self._preview(Choice(self.kind, direction=direction, tiles=self.tiles,
                                     target_id=self.target_id))
        elif self.panel == "preview" and self.pending and self.pending.direction:
            self._preview(Choice(self.pending.kind, self.pending.aim, direction, self.pending.tiles,
                                 target_id=self.pending.target_id))
        else:
            self._preview(Choice(ActionKind.WALK, direction=direction))

    def tick(self):
        if self.rounds:
            return
        if not self.manual and self.panel == "main" and not self.session.ready and not self.session.holding_guard:
            self.session.step()

    def rows(self, model):
        if self.panel in {"details", "stat", "strategy"}:
            return tuple(part for row in self.sidebar_rows(model)
                         for part in textwrap.wrap(row, model.columns) or [""])
        if self.panel in {"notebook", "log", "help", "sheet", "more", "tactics", "pickup"}:
            if self.panel == "notebook":
                content = (*model.traits, *model.notebook_rows) or ("No observed patterns yet. Watch him choose.",)
            elif self.panel == "log":
                content = (("LATEST EXCHANGE", *self.round_result, "", "FULL FIGHT LOG", *model.full_log)
                           if self.round_result else model.full_log) + ("WHY", *model.why)
            elif self.panel == "sheet":
                content = ("YOUR CURRENT STATS", *(f"{key}: {value}" for key, value in model.player_sheet),
                           "", "ENEMY STATS (learned by fighting)",
                           *(f"{key}: {value}" for key, value in model.enemy_sheet))
            elif self.panel == "more":
                content = ("[Tactics][Run][Dash+Light]", "[Bandage][Eat][Drink][Pickup]",
                           "[Notebook][Guide][Log][Help]", "[Timeline][Sheet]")
            elif self.panel == "tactics":
                content = ("TACTICAL ACTIONS", model.adrenaline_status,
                           "[Feint][Shove][Disengage]", "[Thrust][Disarm][Adrenaline]")
            elif self.panel == "pickup":
                options = pickup_options(model)
                nearby = tuple(row for row in model.ground_weapons
                               if max(abs(model.player_pos[0]-row[2][0]),
                                      abs(model.player_pos[1]-row[2][1])) <= model.pickup_range)
                content = ("PICK UP WEAPON:",
                           *(f"[Pick {index}] {name} ({material}, condition {condition:.0f}%) at {position}"
                             for index, (item_id, name, position, condition, material)
                             in enumerate(options, 1)),
                           *(f"Unusable: {name} ({material}, condition {condition:.0f}%) at {position}"
                             for _, name, position, condition, material in nearby
                             if condition <= 0 or model.player_hand_severed),
                           *(("A severed hand cannot grip a weapon.",) if model.player_hand_severed else ()),
                           *(("No usable weapon is in pickup range.",) if not options else ()),
                           "Replacing a weapon drops the old one here.")
            else:
                content = (("ONE ACTION EACH PER EXCHANGE", "Choose an attack, then a target to resolve.",
                            "Choose movement, then distance and direction.",
                            "Guard, Wait and supplies resolve on click.",
                            "Both fighters commit once. Speed never grants extra attacks.",
                            "Arrows: walk one tile. D: dash. R: run.",
                            "Space: light. F: heavy. G: guard. T: tactics. Period: wait.",
                            "Esc: back; from main, pause the duel.",
                            "Backspace: retry. V: next duel carrying wounds.",
                            "Walk to a > exit to flee.") if self.rounds else
                           ("Time pauses at every player decision.", "1. Choose an action; attacks open zones.",
                           "2. Read the cost, landing tick and danger.", "3. Enter confirms; Esc cancels preview.",
                           "Arrows: WALK preview, not an instant step.", "D then arrows: Dash. J: Dash + Light.",
                           "Guard: raise, hold, then G releases.", "Enter while guarding holds one turn.",
                           "Blood kills. Stamina/fatigue slow you.", "! is committed enemy reach; ~ is a guess.",
                           "K: notebook | I: guide OFF/HINTS/FULL", "Tab: timeline | L: log | ?: enemy sheet",
                           "N: single tick | A: resume auto time", "Backspace: retry. V: next duel (carries wounds).",
                            "Esc from main pauses; C aboard resumes.", "Walk to a > exit to flee the actual fight."))
            wrapped = [part for line in content for part in textwrap.wrap(line, width=model.columns) or [""]]
            page = dict(model.presentation)["panel_page_rows"]
            self.scroll = min(self.scroll, max(0, len(wrapped)-page))
            if self.panel == "pickup":
                rows = ["PICKUP — SELECT AN ITEM", *wrapped[self.scroll:self.scroll+page],
                        "PgUp/PgDn: scroll | [Back]"]
            else:
                rows = [f"{self.panel.upper()} - {model.enemy_name}", model.player_state,
                        f"LV {model.player_level} XP {model.player_xp:.0f}/{model.player_xp_next}",
                        *wrapped[self.scroll:self.scroll+page], "PgUp/PgDn: scroll | [Back]"]
        else:
            rows = list(format_ascii(model))
            if self.panel == "targets":
                target_options = self._current_target_options()
                start = self.target_page * 5
                visible = target_options[start:start+5]
                rows.extend(["Choose a visible hostile:", *(
                    f"[Target {index}] [{actor.glyph}] {actor.name} · ~{actor.distance} "
                    f"{'tile' if actor.distance == 1 else 'tiles'} · {actor.posture}"
                    for index, actor in enumerate(visible, start+1)),
                    *(('[Prev Targets]' if start > 0 else ''),
                      ('[Next Targets]' if start+5 < len(model.target_options) else ''))])
            elif self.panel == "zones":
                rows.extend(["Choose target: [Eye][Head][Torso]", "[Arm][Hand][Leg] (E/H/T/A/X/L)"])
                rows.extend(f"{z.zone.value}: {z.difficulty:g} ACC {'OPEN' if z.exposed else 'closed'} / you {z.accuracy:.1f}" for z in model.zones)
            elif self.panel == "direction":
                rows.extend([f"{self.kind.value}: choose direction", f"Arrows/numpad or click. Tiles: {self.tiles}", "1/2/3 changes distance. Esc cancels."])
            if self.panel == "preview":
                rows.append("[Confirm][Back]")
            ended = "DEFEAT" in model.player_state or "VICTORY" in model.player_state or "FLED" in model.player_state
            if ended:
                rows.append("[Retry][Next]")
            rows.append("Fight over - combat actions disabled." if ended else self.message)
        return tuple(part for row in rows for part in (textwrap.wrap(row, width=model.columns,
                     replace_whitespace=False, drop_whitespace=False) or [""]))

    def sidebar_rows(self, model):
        if self.panel == "strategy":
            return strategy_rows(model, coach(model, self.round_summary))
        if self.panel == "details":
            return ("EXCHANGE DETAILS", *(self.round_result or ("No exchange yet.",)), "[Back]")
        if self.panel == "stat":
            from pirates.duel_stat_help import stat_rows
            return (f"STAT: {self.stat_name}", *stat_rows(model, self.stat_name, enemy=self.stat_enemy), "[Back]")
        if self.panel == "sheet":
            return ("CHARACTER STATS", *(f"{key}: {value}" for key, value in model.player_sheet),
                    "Choose a stat above to see its meaning and current influences.",
                    "ENEMY STATS (learned by fighting)", *(f"{key}: {value}" for key, value in model.enemy_sheet), "[Back]")
        if self.panel == "preview" and model.preview:
            p = model.preview
            details = (next((option.details for option in model.actions
                             if option.label.upper() == p.kind), "")
                       if p.kind in {"FEINT", "SHOVE", "DISENGAGE", "THRUST", "DISARM", "ADRENALINE"}
                       else "")
            no_strike = ("No strike: adrenaline activates only after this action completes."
                         if p.kind == "ADRENALINE" else
                         "No strike: movement, defense or recovery." if p.strike_tick is None else
                         f"Hit in {p.strike_tick-model.tick} ticks.")
            return ("REVIEW YOUR ACTION", p.kind.replace("_", " ").title(),
                    f"Target: {p.aim.title()}" if p.aim else "No body-part target.",
                    "", f"Stamina cost: {p.stamina:.1f}", f"Fatigue gained: {p.fatigue:.1f}",
                    no_strike,
                    f"Ready again in {p.end_tick-model.tick} ticks." if p.end_tick is not None else "Hold until released.",
                    "", "ACTION DETAILS", details,
                    "RISK / REQUIREMENTS", p.risk or "No specific warning. Enemy intentions remain unknown.",
                    p.error if not p.valid else "", "",
                    "Time has not advanced.", "Confirm commits; Back cancels for free.", "[Confirm][Back]")
        if self.panel == "zones":
            return (f"{self.kind.value.replace('_', ' ')} — CHOOSE TARGET",
                    "Click a body part to resolve this round." if self.rounds else "Choose a body part, then review before committing.", "",
                    *(f"{z.zone.value.title()}: needs {z.difficulty:g} accuracy; yours {z.accuracy:.1f}. "
                      f"{'Exposed' if z.exposed else 'Covered: may drift to another part'}." for z in model.zones),
                    "", "Choose target: [Head][Torso][Arm]", "[Hand][Leg]", "[Back]")
        if self.panel == "direction":
            step = dict(model.movement_timing).get(self.kind.value)
            timing = (f"Travel: {step} ticks/tile ({step*self.tiles} total); strikes resolve before same-tick movement."
                      if step is not None else "")
            distance_note = ("Disengage is exactly one tile away; distance cannot be changed."
                             if self.kind == ActionKind.DISENGAGE else
                             f"Distance: {self.tiles} tile(s). 1/2/3 changes it.")
            return (f"{self.kind.value.replace('_', ' ')} — CHOOSE DIRECTION",
                    distance_note, timing, self.message,
                    "Click map / direction button. This resolves the exchange." if self.rounds else
                    "Click map / direction; then review timing and cost.", "[Back]")
        if self.panel != "main":
            return self.rows(model)
        pools = {label: (value, maximum) for label, value, maximum in model.pools}
        blood, maximum = pools["BLD"]
        stamina, stamina_max = pools["STA"]
        fatigue, _ = pools["FAT"]
        rows = [f"ROUND {len(self.session.inputs)+1} — CHOOSE ONE ACTION" if self.rounds else "SWORD DUEL", model.player_state,
                 ("Each active fighter commits once; watch the shared exchange."
                  if len(self.session.state.fighters) > 2 else "Both fighters commit once; watch the exchange.")
                 if self.rounds else model.instructions,
                 self.message, "", *(self.round_summary if self.rounds else self.round_result), "",
                "YOU", f"Blood: {blood:.0f}/{maximum:.0f}",
                f"Stamina: {stamina:.0f}/{stamina_max:.0f} | Fatigue: {fatigue:.0f}/100",
                *(f"{name}: {value}" for name, value in model.player_sheet), "",
                "YOUR WOUNDS / BLEEDING"]
        rows.extend((f"{zone}: {bleed:g} blood/turn before exertion" for zone, bleed in model.wounds)
                    if model.wounds else ("No bleeding wounds.",))
        rows.extend(["", ("VISIBLE THREATS" if model.group_combat else
                           f"{model.enemy_name.upper()} — {model.enemy_mood}"),
                     f"Action: {model.enemy_action.lower().replace('_', ' ')}",
                     f"Action completes/hits in {model.enemy_remaining} ticks." if model.enemy_remaining is not None else "No timed action underway.",
                     "", "CURRENT STATS: YOU vs ENEMY", *model.comparison,
                     "Strength: strike force and guard contests.",
                      "Speed: affects this exchange, never extra actions." if self.rounds else "Speed: faster windup, recovery and movement.",
                     "Accuracy: which body parts can be hit reliably.",
                     "", "LATEST EXCHANGE", *(model.log or ("No strikes exchanged yet.",)),
                     "", "Click an action below; hover for details.",
                     "Green: you. Red: Ronin. >: exit."])
        if model.danger:
            rows.append("Amber: possible reach, not an area hit.")
        if "DEFEAT" in model.player_state or "VICTORY" in model.player_state or "FLED" in model.player_state:
            rows.append("[Retry][Next]")
        return tuple(rows)


class DuelDisplay:
    """PIL -> existing SDL renderer, including native click targets and hover sheet."""
    def __init__(self):
        self.buttons = []
        self.map_rect = None
        self.header_rect = None
        self.hover_sheet = False
        self._cache = None
        self._seen_big = set()
        self._flash_until = 0
        self.hover_action = None
        self._art = None

    def _map_art(self):
        if self._art is None:
            from pirates.urizen_tileset import ASSET_ROOT, _records
            from pirates.player_sprites import ASSET
            paths = {row["label"]: ASSET_ROOT / row["paths"]["transparent"]
                     for row in _records() if row["label"] in {"floor_plank", "wall_plank"}}
            paths["captain"] = ASSET
            self._art = {}
            for label, path in paths.items():
                with Image.open(path) as source:
                    self._art[label] = source.convert("RGBA")
        return self._art

    @property
    def flashing(self):
        return time.monotonic() < self._flash_until

    def draw(self, renderer, model, rows, *, present=True):
        sdl = renderer.context.sdl_renderer
        width, height = sdl.output_size
        style = dict(model.presentation)
        big = {(popup.tick, popup.text, popup.pos) for popup in model.popups if popup.big}
        if big - self._seen_big:
            self._flash_until = time.monotonic() + style["flash_ms"] / 1000
        self._seen_big = big
        padding, gap = style["padding_px"], style["line_gap_px"]
        font = None
        for size in range(style["font_max"], style["font_min"]-1, -1):
            candidate = renderer.ui.font(size=size, mono=True)
            cell_w = candidate.getlength("M")
            if cell_w * model.columns + padding*2 <= width and (size+gap)*len(rows)+padding*2 <= height:
                font = candidate
                break
        if font is None:
            font = renderer.ui.font(size=style["font_min"], mono=True)
        cell_w = font.getlength("M")
        line_h = font.size + gap
        left = max(padding, round((width - cell_w * model.columns) / 2))
        top = padding
        self.buttons = []
        self.header_rect = (left, top, model.columns * cell_w, line_h * 4)
        known_buttons = {"Light", "Heavy", "Guard", "Dash", "Move", "Wait", "More", "Run", "Dash+Light",
                         "Bandage", "Eat", "Drink", "Pickup", "Notebook", "Guide", "Log", "Help", "Timeline",
                          "Tactics", "Feint", "Shove", "Disengage", "Thrust", "Disarm", "Adrenaline",
                          "Sheet", "Back", "Eye", "Head", "Torso", "Arm", "Hand", "Leg", "Confirm", "Retry", "Next"}
        known_buttons.update(match.group(1) for line in rows
                             for match in re.finditer(r"\[([^\]]+)\]", line)
                             if match.group(1).startswith("Pick ")
                             and match.group(1)[5:].isdigit())
        for index, line in enumerate(rows):
            for match in re.finditer(r"\[([^\]]+)\]", line):
                if match.group(1) in known_buttons:
                    self.buttons.append(((left + match.start()*cell_w, top+index*line_h,
                                          len(match.group())*cell_w, line_h), match.group(1)))
        maps = map_lines(model)
        self.map_rect = None
        if maps and maps[0] in rows:
            self.map_rect = (left, top+rows.index(maps[0])*line_h, cell_w*3, line_h)
        key = (width, height, rows, font.size, model.popups, self.hover_sheet, self.hover_action, model.enemy_sheet, self.flashing)
        if self._cache is None or self._cache[0] != key:
            image = Image.new("RGBA", (width, height), (8, 13, 17, 255))
            draw = ImageDraw.Draw(image)
            for index, line in enumerate(rows):
                color = (225, 230, 220, 255)
                if "DEAD" in line or "UNCONSCIOUS" in line:
                    color = (255, 110, 100, 255)
                elif "ALIVE" in line or "VICTORY" in line:
                    color = (130, 245, 170, 255)
                elif line.startswith("GUIDE") or "confirm" in line.lower():
                    color = (245, 215, 120, 255)
                if "FAT" in line and model.speed_penalty <= -style["penalty_warn_pct"]:
                    color = (255, 110, 100, 255) if model.speed_penalty <= -style["penalty_severe_pct"] else (245, 215, 120, 255)
                draw.text((left, top+index*line_h), line, font=font, fill=color)
            if self.map_rect:
                mx, my, tw, th = self.map_rect
                art = self._map_art()
                for y in range(model.height):
                    for x in range(model.width):
                        pos = (x, y)
                        px, py = round(mx+x*tw), round(my+y*th)
                        size = (max(1, round(tw)), max(1, round(th)))
                        draw.rectangle((px, py, px+size[0], py+size[1]), fill=(15, 40, 39, 255))
                        tile = art["floor_plank" if pos in model.floor else "wall_plank"]
                        image.alpha_composite(tile.resize(size, Image.Resampling.NEAREST), (px, py))
                        if pos in model.danger:
                            draw.rectangle((px, py, px+size[0]-1, py+size[1]-1), outline=(235, 175, 70, 255))
                        if pos in {model.player_pos, model.enemy_pos}:
                            sprite = art["captain"].resize(size, Image.Resampling.NEAREST)
                            image.alpha_composite(sprite, (px, py))
                            draw.rectangle((px, py, px+size[0]-1, py+size[1]-1),
                                           outline=(110, 225, 170, 255) if pos == model.player_pos else (250, 105, 85, 255))
                        elif pos in model.exits:
                            draw.text((px, py), ">", font=font, fill=(150, 240, 180, 255))
            availability = {action.label: action.enabled for action in model.actions}
            for (x, y, _, _), label in self.buttons:
                if label in availability and not availability[label]:
                    draw.text((x, y), f"[{label}]", font=font, fill=(100, 112, 115, 255))
            if self.map_rect:
                mx, my, tw, th = self.map_rect
                for popup in model.popups[-2:]:
                    x, y = mx + popup.pos[0]*tw, my + (popup.pos[1]+1)*th
                    text = ("* " if popup.big else "") + popup.text
                    x = min(x, left + model.columns*cell_w - font.getlength(text))
                    bounds = draw.textbbox((x, y), text, font=font)
                    draw.rectangle(bounds, fill=(35, 18, 12, 245))
                    draw.text((x, y), text, font=font, fill=(255, 205, 115, 255))
            if self.hover_sheet:
                tooltip = [f"{key}: {value}" for key, value in model.enemy_sheet]
                box_top = top + line_h * 4
                draw.rectangle((left, box_top, left+cell_w*model.columns, box_top+line_h*len(tooltip)),
                               fill=(15, 35, 38, 255), outline=(140, 210, 190, 255))
                for index, line in enumerate(tooltip):
                    draw.text((left, box_top+index*line_h), line, font=font, fill=(245, 245, 225, 255))
            if self.hover_action:
                descriptions = {
                    "Light": "Quick sword strike. Choose a body part, then read the timing and Confirm. Can interrupt a heavy windup; may be parried.",
                    "Heavy": "Slow, powerful strike. Strong against guard or long recovery; risky if a faster attack lands first. Choose target for exact timing.",
                    "Guard": "Raise your guard, then hold it. Early blocks can parry. Hand and leg attacks bypass guard. Click Guard again to release.",
                    "Dash": "Fast movement, not invincibility. Choose direction and distance for exact time and stamina cost. Attacks can interrupt you.",
                    "Move": "Walk one tile. Choose a direction to preview time and cost before confirming. You are vulnerable while moving.",
                    "Wait": "Rest briefly to recover stamina and fatigue. Risky while an enemy attack is about to land.",
                    "Sheet": "View your current strength, speed, accuracy and endurance, plus learned enemy stats.",
                    "Confirm": "Commit the previewed action. Time advances and you cannot cancel its windup or recovery.",
                    "Back": "Cancel this preview without spending time or resources.",
                    "More": "Open running, dash-strike, supplies, character stats, notebook and help.",
                }
                text = descriptions.get(self.hover_action, f"{self.hover_action}: click to inspect or preview. No action is committed until Confirm.")
                option = next((action for action in model.actions if action.label == self.hover_action), None)
                if option and self.hover_action != "More":
                    text += " " + option.details
                tooltip = textwrap.wrap(text, width=model.columns)
                box_top = max(top, height-padding-line_h*(len(tooltip)+1))
                draw.rectangle((left, box_top, left+cell_w*model.columns, height-padding),
                               fill=(15, 35, 38, 255), outline=(140, 210, 190, 255))
                for index, line in enumerate(tooltip):
                    draw.text((left, box_top+index*line_h), line, font=font, fill=(245, 245, 225, 255))
            if self.flashing:
                draw.rectangle((left-1, top-1, left+cell_w*model.columns+1, min(height-1, top+len(rows)*line_h)),
                               outline=(255, 220, 120, 255))
            texture = sdl.upload_texture(np.asarray(image, dtype=np.uint8))
            self._cache = (key, texture)
        sdl.draw_color = (8, 13, 17, 255)
        sdl.clear()
        sdl.copy(self._cache[1], dest=(0, 0, width, height))
        if present:
            sdl.present()

    def button_at(self, pos):
        x, y = pos
        for (left, top, width, height), label in self.buttons:
            if left <= x < left+width and top <= y < top+height:
                return label
        return None

    def tile_at(self, pos):
        if getattr(self, "reading_modal", False):
            return None
        if self.map_rect is None:
            return None
        left, top, width, height = self.map_rect
        return int((pos[0]-left)//width), int((pos[1]-top)//height)


class GameDuelDisplay(DuelDisplay):
    """Use the game's WorldRenderer/atlas and pixel UI, not a text-map image."""

    def __init__(self):
        super().__init__()
        self.scroll = 0
        self.max_scroll = 0

    def draw(self, renderer, model, rows, *, present=True):
        from pirates.duel_layout import draw_dashboard
        draw_dashboard(self, renderer, model, rows, present=present)

    def tile_at(self, pos):
        bounds = getattr(self, "arena_rect", None)
        if bounds:
            x, y, w, h = bounds
            if not (x <= pos[0] < x+w and y <= pos[1] < y+h):
                return None
        return super().tile_at(pos)


def _run_sword_duel(renderer, world):
    session = (DuelSession.from_data(world.duel_journal) if world.duel_journal else
               DuelSession.new(seed=world.clock.total_ticks))
    ui, display = DuelUI(session, rounds=True, coach_enabled=bool((world.duel_journal or {}).get('coach_enabled', False))), GameDuelDisplay()
    # Finish an older saved tick-mode commitment before entering round mode.
    if any(fighter.action is not None for fighter in session.state.fighters):
        for fighter in session.state.fighters:
            if fighter.action and fighter.action.kind == ActionKind.GUARD:
                from pirates.duel_system.engine import release_guard
                release_guard(session.state, fighter.id)
        while not session.state.end_reason and any(f.action is not None or not f.can_act(session.state.tick) for f in session.state.fighters):
            from pirates.duel_system.engine import advance_one_tick
            advance_one_tick(session.state)
    renderer._move_repeater.cancel()
    initial = ui.model()
    style = dict(initial.presentation)
    renderer.context.sdl_window.min_size = (640, 600)
    pressed_enemy = None
    while not ui.closed:
        frame_started = time.per