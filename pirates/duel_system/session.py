"""Application controller for a sword duel; renderer-independent commands/pacing.

Preview is free. Only confirm, hold/release, stepping and leave mutate simulation.
NPC choice and notebook key are captured before the player's same-tick choice.
"""
from copy import deepcopy
from dataclasses import asdict

from .ai import choose_action, observe
from .config import load_config
from .engine import (Choice, advance_one_tick, check_end_conditions, commit_action,
                     legality, movement_path, release_guard)
from .guide import guide, make_guide_view
from .mental import initialize_fight, refresh_behavior_modes
from .model import ActionKind, CombatState, Equipment, EventType, Fighter, Personality, Weapon, Zone, _plain
from .notebook import Notebook


class DuelSession:
    def __init__(self, state, notebook, guide_mode="hints"):
        if guide_mode not in {"off", "hints", "full"}:
            raise ValueError("Invalid guide mode")
        self.state, self.notebook, self.guide_mode = state, notebook, guide_mode
        self.selected = None
        self.inputs = []
        self._guide_cache = None
        self._exchange_cursor = len(state.event_log)
        self._victory_settled = False
        self.exchange = None

    @property
    def guide_mode(self):
        return self._guide_mode

    @guide_mode.setter
    def guide_mode(self, mode):
        modes = ("off", "hints", "full")
        if mode not in modes:
            raise ValueError("Invalid guide mode")
        self._guide_mode = mode

    @classmethod
    def new(cls, *, config=None, seed=1, guide_mode="hints", notebook=None, archetype=None):
        cfg = deepcopy(config) if config is not None else load_config()
        layout = cfg["arena"]
        fighters = []
        for identity, name, position, fixture in (("player", "Dorime", layout["player_pos"], "dorime"),
                                                   ("ronin", "Ronin", layout["enemy_pos"], "ronin")):
            fighters.append(Fighter(identity, name, cfg["defaults"]["level"],
                                    **cfg["fixtures"][fixture], pos=tuple(position),
                                     equipment=Equipment(Weapon(**cfg["gear"]["weapon"],
                                                                item_id=f"{identity}:weapon:{seed}")),
                                    bandages=layout["bandages"], food=layout["food"], water=layout["water"]))
        roster = cfg["personality"]["roster"]
        default_index = (seed - 1) % len(roster) if seed > 0 else 0
        selected = archetype if archetype is not None else roster[default_index]
        if selected not in cfg["ai_profiles"]:
            raise ValueError(f"Unknown duel personality profile: {selected}")
        profile = deepcopy(cfg["ronin"])
        profile.update(deepcopy(cfg["ai_profiles"][selected]))
        profile["archetype"] = selected
        fighters[1].personality = Personality(**profile)
        floor = {(x, y) for x in range(1, layout["width"]-1) for y in range(1, layout["height"]-1)}
        state = initialize_fight(fighters, config=cfg, rng_seed=seed, walkable=floor,
                                 exits={tuple(pos) for pos in layout["exits"]})
        return cls(state, notebook if notebook is not None else Notebook(cfg), guide_mode)

    @classmethod
    def from_state(cls, state, *, notebook=None, guide_mode="hints"):
        """Attach the player-facing controller to an already-built N-actor encounter."""
        return cls(state, notebook if notebook is not None else Notebook(state.config), guide_mode)

    @classmethod
    def new_group(cls, fighters, *, config=None, seed=1, guide_mode="hints", notebook=None,
                  walkable=None, exits=(), leaders=None):
        from .mental import initialize_fight
        cfg = deepcopy(config) if config is not None else load_config()
        state = initialize_fight(fighters, config=cfg, rng_seed=seed, walkable=walkable,
                                 exits=exits, leaders=leaders)
        return cls.from_state(state, notebook=notebook, guide_mode=guide_mode)

    @property
    def player(self):
        return self.state.fighters[0]

    @property
    def enemy(self):
        if len(self.state.fighters) > 2:
            return self.state.other(self.player)
        return self.state.primary_opponent(self.player.id, active_only=False)

    @property
    def holding_guard(self):
        return bool(self.player.action and self.player.action.kind == ActionKind.GUARD
                    and self.player.action.phase_at(self.state.tick) == "GUARD_HOLD"
                    and not self.state.end_reason)

    @property
    def ready(self):
        return (not self.state.end_reason and self.exchange is None
                and self.player.action is None and self.player.can_act(self.state.tick))

    def select(self, choice):
        from .render_model import preview_action
        self.selected = choice
        return preview_action(self.state, choice)

    def cancel(self):
        self.selected = None

    def _record_npc(self, fighter_id, category, observation):
        fighter = self.state.fighter(fighter_id)
        witnessed = (len(self.state.fighters) <= 2 or self.state.can_see(self.player, fighter))
        if witnessed:
            if len(self.state.fighters) <= 2:
                visible = ("blood_band",) if fighter.wounds else ()
                archetype = fighter.personality.archetype if fighter.personality else "ronin"
                key, full_key = observation.public_key, observation.full_key
                if self.player.id != fighter.id:
                    self.notebook.record(archetype, fighter.id, key, category, tick=self.state.tick,
                                         full_key=full_key, visible_dimensions=visible)
        self.state.emit(EventType.DECISION, fighter.id,
                        deltas={"category": category, "witnessed": witnessed})

    def _npc_plan(self, snapshot, fighter_id):
        """Read one actor's legal choice from the shared, pre-commit snapshot."""
        fighter = snapshot.fighter(fighter_id)
        observation = observe(snapshot, fighter_id)
        if fighter.behavior_modes["pain"] == "BREAK" or fighter.behavior_modes["fear"] == "BREAK":
            exits = snapshot.exits
            if exits:
                options = []
                current_distance = min(max(abs(fighter.pos[0]-x), abs(fighter.pos[1]-y))
                                       for x, y in exits)
                threat = snapshot.perceived_hostile_position(fighter.id)
                movement_kinds = (ActionKind.WALK, ActionKind.DASH, ActionKind.RUN,
                                  ActionKind.DISENGAGE)
                for kind in movement_kinds:
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            if dx == dy == 0:
                                continue
                            max_tiles = snapshot.config["actions"][kind.value]["max_tiles"]
                            for tiles in range(1, max_tiles + 1):
                                choice = Choice(kind, direction=(dx, dy), tiles=tiles)
                                if legality(snapshot, fighter.id, choice):
                                    continue
                                destination = movement_path(snapshot, fighter, choice)[-1]
                                exit_distance = min(max(abs(destination[0]-x), abs(destination[1]-y))
                                                    for x, y in exits)
                                if exit_distance >= current_distance:
                                    continue
                                threat_distance = (max(abs(destination[0]-threat[1][0]),
                                                       abs(destination[1]-threat[1][1]))
                                                   if threat else 0)
                                options.append((exit_distance, -threat_distance, kind.value,
                                                dx, dy, tiles, choice))
                if options:
                    *_, choice = min(options, key=lambda row: row[:-1])
                    category = ({ActionKind.DASH: "DASH_OUT", ActionKind.RUN: "RUN_OUT",
                                 ActionKind.WALK: "WALK_OUT", ActionKind.DISENGAGE: "DISENGAGE"}
                                [choice.kind])
                    return observation, category, choice
            if fighter.morale <= snapshot.config["behavior_thresholds"]["yield_morale_max"]:
                choice = Choice(ActionKind.YIELD)
                if not legality(snapshot, fighter.id, choice):
                    return observation, "YIELD", choice
        decision = choose_action(snapshot, fighter_id)
        return observation, decision.category, decision.choice

    def _npc_choice(self):
        """Compatibility hook for single-opponent harnesses and deterministic UI tests."""
        enemy = self.enemy
        if enemy is None or enemy.action is not None or not enemy.can_act(self.state.tick):
            return None
        snapshot = self.state.clone()
        observation, category, choice = self._npc_plan(snapshot, enemy.id)
        self._record_npc(enemy.id, category, observation)
        return choice

    def _start_exchange(self, player_choice=None):
        if self.exchange is not None or self.state.end_reason:
            return False
        from .perception import refresh_contacts
        refresh_contacts(self.state)
        for fighter in self.state.fighters:
            if self.state.combat_active(fighter) and fighter.can_act(self.state.tick):
                refresh_behavior_modes(self.state, fighter, "DECISION_BOUNDARY")
        event_cursor = len(self.state.event_log)
        participants = [fighter for fighter in self.state.fighters
                        if self.state.combat_active(fighter)
                        and (fighter.action is not None or fighter.can_act(self.state.tick))]
        if player_choice is None and self.player.action is None and self.player.can_act(self.state.tick):
            participants = [fighter for fighter in participants if fighter.id != self.player.id]
        if not participants:
            return False
        if player_choice is not None and self.player not in participants:
            return False
        snapshot = self.state.clone()
        plans = {}
        npc_participants = [fighter for fighter in participants if fighter.id != self.player.id]
        patched_single_choice = (len(npc_participants) == 1 and "_npc_choice" in self.__dict__)
        if patched_single_choice:
            fighter = npc_participants[0]
            snapshot_for_choice = self.state.clone()
            observation = observe(snapshot_for_choice, fighter.id)
            plans[fighter.id] = (observation, "WAIT", self._npc_choice())
        for fighter in participants:
            if fighter.id == self.player.id or fighter.action is not None:
                continue
            if fighter.id not in plans:
                plans[fighter.id] = self._npc_plan(snapshot, fighter.id)

        start_tick = self.state.tick
        self.exchange = {
            "start_tick": start_tick,
            "guard_deadline": start_tick + self.state.config["group"]["guard_exchange_ticks"],
            "participants": [fighter.id for fighter in participants],
            "committed": [fighter.id for fighter in participants if fighter.action is not None],
            "event_cursor": event_cursor,
        }
        # All policies saw the same snapshot; modes are refreshed once at this
        # simulation decision boundary, never by a render/observation query.
        for fighter in participants:
            if fighter.id not in plans:
                continue
            observation, category, choice = plans[fighter.id]
            if patched_single_choice and fighter.id == npc_participants[0].id:
                # An injected legacy choice hook owns this NPC decision; preserve
                # its test/harness semantics rather than layering AI morale logic.
                self._record_npc(fighter.id, category, observation)
                continue
            self._record_npc(fighter.id, category, observation)

        if self.state.end_reason:
            self.exchange = None
            self.settle_victory()
            return False

        choices = []
        if player_choice is not None:
            choices.append((self.player.id, player_choice))
        choices.extend((fighter.id, plans[fighter.id][2]) for fighter in participants
                       if fighter.id in plans and plans[fighter.id][2] is not None)
        for fighter_id, choice in choices:
            if self.state.end_reason:
                break
            commit_action(self.state, fighter_id, choice)
            self.exchange["committed"].append(fighter_id)
        if player_choice is not None:
            self.inputs.append({"tick": start_tick, "choice": asdict(player_choice)})
        refresh_contacts(self.state, self.state.event_log[event_cursor:])
        return True

    def _exchange_complete(self):
        if self.exchange is None:
            return True
        for fighter_id in self.exchange["participants"]:
            fighter = self.state.fighter(fighter_id)
            if self.state.combat_active(fighter) and fighter.action is not None:
                return False
        return True

    def _apply_exchange_morale(self):
        if self.exchange is None or len(self.state.fighters) <= 2:
            return
        from .perception import apply_witnessed_morale
        cursor = self.exchange.get("event_cursor", self._exchange_cursor)
        apply_witnessed_morale(self.state, self.state.event_log[cursor:],
                               self.exchange["start_tick"])

    def confirm(self):
        if (not self.ready or self.selected is None
                or legality(self.state, self.player.id, self.selected)):
            return False
        choice = self.selected
        if not self._start_exchange(choice):
            return False
        self.selected = None
        return True

    def _release_npc_guard(self):
        action = self.enemy.action
        if action and action.kind == ActionKind.GUARD and action.phase_at(self.state.tick) == "GUARD_HOLD":
            visible_heavy = observe(self.state, self.enemy.id).public_key.opp_state == "WINDUP_H"
            bonus = self.state.config["guard"]["heavy_window_bonus"] if visible_heavy else 0
            if self.enemy.riposte or self.state.tick >= action.guard_ready_tick + action.parry_window + bonus:
                release_guard(self.state, self.enemy.id)

    def step(self):
        if self.state.end_reason:
            return
        if self.exchange is None:
            self._start_exchange()
            if self.state.end_reason:
                self.settle_victory()
                return
        if self.exchange is not None:
            if len(self.state.fighters) <= 2:
                self._release_npc_guard()
            for fighter_id in self.exchange["participants"]:
                fighter = self.state.fighter(fighter_id)
                action = fighter.action
                if (action and action.kind == ActionKind.GUARD and not action.released
                        and action.phase_at(self.state.tick) == "GUARD_HOLD"
                        and self.state.tick >= self.exchange["guard_deadline"]):
                    release_guard(self.state, fighter_id)
        advance_one_tick(self.state)
        self._record_exchanges()
        if self._exchange_complete():
            self._apply_exchange_morale()
            self.exchange = None
        self.settle_victory()

    def settle_victory(self):
        if self._victory_settled or not self.state.end_reason:
            return
        self._victory_settled = True
        if self.state.winner == self.player.id and len(self.state.fighters) > 2:
            from .progression import award_xp, kill_xp
            defeated = [fighter for fighter in self.state.hostiles_for(self.player.id, active_only=False)
                        if fighter.terminal_reason in {"death", "unconscious", "yield"}]
            amount = sum(kill_xp(self.player.level, fighter.level, self.state.config, self.guide_mode)
                         for fighter in defeated)
            if amount:
                award_xp(self.state, self.player, amount, guided=self.guide_mode)
            return
        if self.state.winner == self.player.id and self.state.end_reason in {"death", "unconscious", "yield"}:
            from .progression import award_xp, kill_xp
            # V1 duels end at collapse/yield; reward defeated opponents, not an
            # invented execution. The XP event is a victory award, never a kill claim.
            amount = kill_xp(self.player.level, self.enemy.level, self.state.config, self.guide_mode)
            award_xp(self.state, self.player, amount, guided=self.guide_mode)

    def _record_exchanges(self):
        events = self.state.event_log[self._exchange_cursor:]
        if len(self.state.fighters) <= 2:
            ticks = {event.tick for event in events
                     if event.type in {EventType.HIT, EventType.CLASH, EventType.BLOCK, EventType.PARRY}}
            for _ in ticks:
                self.notebook.record_exchange(self.enemy.id)
        else:
            contacts = {}
            for event in events:
                if (event.type not in {EventType.HIT, EventType.CLASH, EventType.BLOCK, EventType.PARRY}
                        or self.player.id not in event.witnesses):
                    continue
                observed_id = event.target if event.actor == self.player.id else event.actor
                if observed_id and observed_id != self.player.id:
                    contacts.setdefault(observed_id, set()).add(event.tick)
            for actor_id, ticks in contacts.items():
                for _ in ticks:
                    self.notebook.record_exchange(actor_id)
        self._exchange_cursor = len(self.state.event_log)

    def run_to_player(self):
        while not (self.ready or self.holding_guard or self.state.end_reason):
            self.step()

    def resolve_round(self, choice, *, observe=None):
        """One independent commitment each; no mid-exchange AI recommitments."""
        if not self.ready or any(f.action is not None and self.state.combat_active(f)
                                 for f in self.state.fighters if f.id != self.player.id):
            return False
        if legality(self.state, self.player.id, choice):
            return False
        self.selected = choice
        if not self.confirm():
            return False
        if observe is not None:
            observe()
        while not self.state.end_reason and self.exchange is not None:
            if len(self.state.fighters) <= 2:
                self._release_npc_guard()
            advance_one_tick(self.state)
            self._record_exchanges()
            for fighter_id in self.exchange["participants"]:
                fighter = self.state.fighter(fighter_id)
                action = fighter.action
                if (action and action.kind == ActionKind.GUARD and not action.released
                        and action.phase_at(self.state.tick) == "GUARD_HOLD"
                        and self.state.tick >= self.exchange["guard_deadline"]):
                    release_guard(self.state, fighter_id)
            if self._exchange_complete():
                self._apply_exchange_morale()
                self.exchange = None
            if observe is not None:
                observe()
        self.settle_victory()
        if self.exchange is not None and (self.state.end_reason or self._exchange_complete()):
            self._apply_exchange_morale()
            self.exchange = None
        return True

    def release_guard(self):
        release_guard(self.state, self.player.id)
        self.selected = None

    def hold_guard(self):
        if not self.holding_guard:
            raise ValueError("You are not holding a guard")
        deadline = self.state.tick + self.state.config["time"]["ticks_per_turn"]
        while self.state.tick < deadline and self.holding_guard:
            self.step()

    def leave(self):
        if not self.state.end_reason:
            self.player.fled = True
            check_end_conditions(self.state)
        self.selected = None

    def restart(self, *, carry=False):
        from .mental import postfight_state
        if carry and (not self.state.end_reason or self.player.blood <= self.state.config["blood"]["collapse"]
                      or self.player.unconscious or self.player.decapitated):
            raise ValueError("Finish this fight conscious before starting the next duel")
        archetype = None if carry else (
            self.enemy.personality.archetype if self.enemy.personality else None)
        replacement = self.new(config=self.state.config, seed=self.state.rng_seed + int(carry),
                               guide_mode=self.guide_mode, notebook=self.notebook,
                               archetype=archetype)
        replacement.player.level, replacement.player.xp = self.player.level, self.player.xp
        if not carry:
            from .stats import max_stamina
            replacement.player.stamina = max_stamina(replacement.player, replacement.state.config)
        if carry:
            previous = self.player
            new_player = Fighter(previous.id, previous.name, previous.level,
                                 **{key: getattr(previous, key) for key in ("strength", "speed", "accuracy", "stamina_stat", "endurance")},
                                  pos=replacement.player.pos, prefight=postfight_state(self.state, previous.id),
                                  equipment=deepcopy(previous.equipment), xp=previous.xp,
                                  bandages=previous.bandages, food=previous.food, water=previous.water,
                                   weapon_hand=previous.weapon_hand,
                                   team_id=previous.team_id)
            new_player.statuses = [deepcopy(status) for status in previous.statuses if status.active(self.state.tick)]
            for status in new_player.statuses:
                if status.until_tick is not None:
                    status.until_tick -= self.state.tick
            new_player.disarmed, new_player.hand_severed = previous.disarmed, previous.hand_severed
            replacement.state.ground_weapons = deepcopy(self.state.ground_weapons)
            new_player.exhausted = previous.exhausted
            new_player.adrenaline_used = previous.adrenaline_used
            new_player.adrenaline_debt_pending = previous.adrenaline_debt_pending
            new_player.adrenaline_debt_due = previous.adrenaline_debt_due
            if previous.adrenaline_until_tick is not None:
                new_player.adrenaline_until_tick = max(0, previous.adrenaline_until_tick - self.state.tick)
            replacement.state.fighters[0] = new_player
            if new_player.adrenaline_debt_pending and new_player.adrenaline_until_tick == 0:
                from .engine import resolve_adrenaline_expiry
                resolve_adrenaline_expiry(replacement.state)
        return replacement

    def render_model(self, *, timeline=True, include_advice=True, zone_kind=None):
        from .render_model import build_render_model
        advice = None
        if include_advice and self.guide_mode != "off" and self.ready and len(self.state.fighters) <= 2:
            key = (self.state.tick, len(self.state.event_log), self.guide_mode)
            if self._guide_cache is None or self._guide_cache[0] != key:
                public = self.notebook.public_view()
                advice = guide(make_guide_view(self.state, self.player.id, public), public, self.guide_mode)
                self._guide_cache = (key, advice)
            else:
                advice = self._guide_cache[1]
        return build_render_model(self.state, self.notebook.public_view(), self.selected, advice,
                                   timeline=timeline, guide_mode=self.guide_mode,
                                   zone_kind=zone_kind)

    def to_data(self):
        return _plain({"version": self.state.config["schema_version"], "state": self.state.to_data(),
                "notebook": self.notebook.to_data(), "guide_mode": self.guide_mode,
                "victory_settled": self._victory_settled,
                "selected": asdict(self.selected) if self.selected else None,
                "inputs": deepcopy(self.inputs), "exchange_cursor": self._exchange_cursor,
                "exchange": deepcopy(self.exchange)})

    @classmethod
    def from_data(cls, data):
        data = deepcopy(data)
        current_version = load_config()["schema_version"]
        if data.get("version") in set(range(1, 30)):
            data["version"] = current_version
            data["notebook"]["version"] = current_version
        elif data.get("version") != current_version:
            raise ValueError("Unsupported duel session version")
        state = CombatState.from_data(data["state"])
        if data["version"] != state.config["schema_version"]:
            raise ValueError("Unsupported duel session version")
        session = cls(state, Notebook.from_data(data["notebook"], state.config), data["guide_mode"])
        if data["selected"]:
            values = deepcopy(data["selected"])
            values["kind"] = ActionKind(values["kind"])
            values["aim"] = Zone(values["aim"]) if values["aim"] else None
            values["direction"] = tuple(values["direction"]) if values["direction"] else None
            session.selected = Choice(**values)
        session.inputs = deepcopy(data["inputs"])
        session._exchange_cursor = data["exchange_cursor"]
        session._victory_settled = data.get("victory_settled", False)
        session.exchange = deepcopy(data.get("exchange"))
        if session.exchange is None and any(fighter.action for fighter in state.fighters):
            active = [fighter.id for fighter in state.fighters if state.combat_active(fighter)]
            committed = [fighter.id for fighter in state.fighters if fighter.action is not None]
            session.exchange = {
                "start_tick": state.tick,
                "guard_deadline": state.tick + state.config["group"]["guard_exchange_ticks"],
                "participants": active,
                "committed": committed,
                "event_cursor": session._exchange_cursor,
            }
        return session
