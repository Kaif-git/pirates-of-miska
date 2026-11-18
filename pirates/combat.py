"""Save-safe game adapter for the deterministic combat arena.

Legacy combat skill fields are retained for save compatibility, not used to
inflate HP or roll hit chance. The simulation lives in combat_core.py.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
import json
import math
from pathlib import Path

import numpy as np
from omnisim.world.map import GameMap
from omnisim.world import tile as T
from pirates.combat_core import (Actor, Combat, ARCHETYPES, COMBAT_RULESET_VERSION, PATTERNS, WEAPONS, ray,
                                 distance, facing, movement_speed,
                                 duel_stats_for_actor, duel_weapon_for_actor)
from pirates.combat_beliefs import CombatBelief, PublicObservation
from pirates.combat_duel import (ActionKind, DuelAction, DuelDecisionState, Duelant,
                                 ACTION_COSTS, ACTION_FATIGUE, apply_action, commit_action,
                                 duel_state_from_data, effective_swing_speed,
                                 legal_actions, tile_distance)
from pirates.combat_npc import (BehaviorProfile, IntelligenceTier, NPCObservation,
                                ObservationLimitedPolicy, VisibleCommitment)


def format_combat_time(ticks):
    """Format 100-Hz combat ticks as player-facing simulated seconds."""
    seconds = max(0.0, ticks / 100.0)
    if seconds > 0:
        seconds = math.ceil(seconds * 10) / 10
    return f"{seconds:.1f}s"


def speed_band(actor, tick=0):
    effective = movement_speed(actor, tick)
    return "fast" if effective >= 125 else "slow" if effective < 80 else "steady"


def technique_summary(actor):
    return {"flanker": "looks for your side", "standard": "fights directly"}.get(
        actor.tactics, actor.tactics.replace("_", " "))


NPC_PROFILE_RANGES = {
    "speed": (25, 200), "movement_skill": (0, 100), "strength": (0, 8),
    "armor": (0, 2), "stamina": (0, 150), "max_stamina": (50, 150),
    "balance": (0, 100), "fatigue": (0, 100), "morale": (0, 100),
    "perception": (4, 15), "carried_weight_kg": (0, 24),
    "carry_capacity_kg": (10, 40), "ammo": (0, 2), "spare_shots": (0, 10),
    "smoke_charges": (0, 3),
}
NPC_PROFILE_INTEGER_FIELDS = {
    "speed", "strength", "armor", "stamina", "max_stamina", "perception",
    "carry_capacity_kg", "ammo", "spare_shots", "smoke_charges",
}


def validate_npc_profile(profile):
    """Validate editor profiles; None means inherit, while false/zero are explicit."""
    allowed_keys = set(NPC_PROFILE_RANGES) | {
        "weapon", "secondary_weapon", "personality", "tactics", "shield", "glyph",
        "target_part", *(f"condition_{part}" for part in
                         ("head", "torso", "left_arm", "right_arm", "left_leg", "right_leg")),
    }
    unknown = set(profile) - allowed_keys
    if unknown:
        raise ValueError(f"Unknown profile field: {sorted(unknown)[0]}")
    for key, value in profile.items():
        if value is None:
            continue
        if key in NPC_PROFILE_RANGES:
            low, high = NPC_PROFILE_RANGES[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
            if key in NPC_PROFILE_INTEGER_FIELDS and not isinstance(value, int):
                raise ValueError(f"{key} must be a whole number")
        elif key in {"weapon", "secondary_weapon"} and (
                not isinstance(value, str) or value not in WEAPONS):
            raise ValueError(f"{key} must name a supported weapon")
        elif key == "personality" and (
                not isinstance(value, str) or value not in PERSONALITIES):
            raise ValueError("personality must name a supported personality")
        elif key == "tactics" and (
                not isinstance(value, str) or value not in {"standard", "flanker"}):
            raise ValueError("tactics must be standard or flanker")
        elif key == "shield" and not isinstance(value, bool):
            raise ValueError("shield must be explicitly on or off")
        elif key == "target_part" and (
                not isinstance(value, str) or value not in {"head", "torso", "arm", "hand", "leg"}):
            raise ValueError("target_part must be a supported body target")
        elif key == "glyph" and (
                not isinstance(value, str) or value not in {"d", "b", "s", "g", "h", "p", "q", "m"}):
            raise ValueError("glyph must be one of the supported enemy markers")
        elif key.startswith("condition_") and (
                not isinstance(value, str) or value not in {
                    "healthy", "wounded", "maimed", "critical", "severed"}):
            raise ValueError(f"{key} must be a supported starting condition")
        elif key in {"condition_head", "condition_torso"} and value == "severed":
            raise ValueError(f"{key} cannot be severed in the current body model")
    stamina = profile.get("stamina")
    maximum = profile.get("max_stamina")
    stamina = 100 if stamina is None else stamina
    maximum = 100 if maximum is None else maximum
    if stamina > maximum:
        raise ValueError("Starting stamina cannot exceed maximum stamina")
    return True


@dataclass
class CombatArena:
    active: bool = False
    scenario: str = "skirmish"
    archetype: str = "duelist"
    squad_mode: str = "uniform"
    count: int = 1
    speed: int | None = None
    strength: int = 0
    personality: str | None = None
    weapon_override: str | None = None
    npc_overrides: dict = field(default_factory=dict)
    cover: bool = True
    tactical_doors: bool = True
    tactical_hazards: bool = True
    monochrome: bool = False
    show_intents: bool = True
    debug_step: bool = False
    selected_target: str | None = None
    engine: Combat | None = None
    _backup: tuple | None = None
    _boarding: bool = False
    _boarding_previous: tuple | None = None
    _duel_state: DuelDecisionState | None = None
    _duel_actor_ids: tuple[str, str | None] = ("captain", None)
    _duel_initial_vitality: dict = field(default_factory=dict)
    _duel_initial_blood: dict = field(default_factory=dict)
    progression: dict = field(default_factory=lambda: {
        "skills": {"sword": 12.0, "brawl": 10.0, "gun": 8.0},
        "xp": {"sword": 0.0, "brawl": 0.0, "gun": 0.0}, "traits": ["quick_learner"]})

    @property
    def fighters(self):
        return self.engine.actors if self.engine else {}

    @property
    def enemies(self):
        return [a for a in self.fighters.values() if a.faction == "enemy"]

    @property
    def finished(self):
        return self.active and (not self.fighters["captain"].active
                                or not any(a.active for a in self.enemies))

    @property
    def log(self):
        return self.engine.log if self.engine else ["Arena not running"]

    def set_scenario(self, scenario):
        if self.active or scenario not in {"skirmish", "outnumbered", "legend"}:
            return False
        self.scenario = scenario
        self.archetype = "sniper" if scenario == "legend" else "duelist"
        self.count = 3 if scenario == "outnumbered" else 1
        return True

    def start(self, world, seed=1):
        if self.active:
            return False
        self._duel_state = None
        self._duel_actor_ids = ("captain", None)
        self._duel_initial_vitality = {}
        self._duel_initial_blood = {}
        self._backup = (world.game_map, world.active_deck,
                        (world.player.x, world.player.y, world.player.z))
        self._boarding = False
        tiles = np.full_like(world.game_map.tiles, T.WALL_STONE)
        tiles[20:34, 73:89] = T.FLOOR
        if self.cover:
            tiles[22:25, 80] = T.WALL_STONE
            tiles[29:32, 82] = T.WALL_STONE
        world.game_map = GameMap(world.game_map.width, world.game_map.height, tiles)
        world.active_deck = 0
        world.player.x, world.player.y, world.player.z = 77, 26, 0
        walkable = {(x, y) for y in range(20, 34) for x in range(73, 89) if tiles[y, x] == T.FLOOR}
        doors = {(81, 26): True} if self.tactical_doors else {}
        hazards = {(75, 31): "spikes", (87, 31): "fire"} if self.tactical_hazards else {}
        self.engine = Combat(walkable, seed, doors=doors, hazards=hazards)
        self.engine.add(self._player_actor(world, (77, 26), facing=(1, 0), smoke_charges=2))
        composition = ["pack_hunter", "gunman", "shield", "spearman", "mixed"]
        for n in range(self.count):
            archetype = composition[n % len(composition)] if self.squad_mode == "mixed" else self.archetype
            self.spawn(archetype, (85 + n % 3, 26 + n // 3 * 2))
        self.selected_target = self.enemies[0].id
        self.active = True
        world.refresh()
        world.message("Combat arena ready: use the bottom action bar for the current encounter; I opens controls, Esc leaves.")
        return True

    def start_boarding(self, world, entry, count=3, seed=1):
        """Start a tactical fight on a projection of the built ship deck."""
        if self.active or type(count) is not int or not 1 <= count <= 5:
            return False
        deck = world.active_deck
        if not isinstance(entry, (tuple, list)) or len(entry) not in (2, 3):
            return False
        if len(entry) == 3 and entry[2] != deck:
            return False
        entry = tuple(entry[:2])
        original = (world.game_map, world.active_deck,
                    (world.player.x, world.player.y, world.player.z))
        self._backup = original
        source_map = world.game_map
        tiles = np.full_like(source_map.tiles, T.WALL_STONE)
        ship_cells = {(c.x, c.y, c.z) for c in world.ship_model.components
                      if c.kind == "floor" and c.z == deck}
        barriers = {(c.x, c.y, c.z): c.kind for c in world.ship_model.components
                    if c.kind in ("wall", "door") and c.z == deck}
        walkable = set()
        door_state = world.door_state(deck)
        for lx, ly, z in ship_cells:
            wx, wy = lx + 69, ly + 12
            barrier = barriers.get((lx, ly, z))
            if barrier == "wall":
                continue
            if barrier == "door" and not door_state[wy, wx]:
                tiles[wy, wx] = T.DOOR
                continue
            tiles[wy, wx] = T.DOOR if barrier == "door" else T.FLOOR
            if barrier != "door" or door_state[wy, wx]:
                walkable.add((wx, wy))
        if entry not in walkable:
            world.game_map, world.active_deck, pos = original
            world.player.x, world.player.y, world.player.z = pos
            self._backup = None
            return False
        captain_at = (world.player.x, world.player.y)
        if captain_at not in walkable:
            choices = sorted(walkable, key=lambda at: (distance(at, captain_at), at))
            if not choices:
                world.game_map, world.active_deck, pos = original
                world.player.x, world.player.y, world.player.z = pos
                self._backup = None
                return False
            captain_at = choices[0]
        world.game_map = GameMap(source_map.width, source_map.height, tiles)
        world.game_map.doors = door_state.copy()
        world.player.x, world.player.y, world.player.z = *captain_at, deck
        self.engine = Combat(walkable, seed)
        self.engine.add(self._player_actor(world, captain_at, facing=(1, 0)))
        choices = sorted(walkable - {captain_at}, key=lambda at: (distance(at, entry), at))
        for position in choices[:count]:
            self.spawn("pack_hunter", position)
        if not self.enemies:
            world.game_map, world.active_deck, pos = original
            world.player.x, world.player.y, world.player.z = pos
            self._backup = None
            return False
        self._boarding_previous = (self.scenario, self.archetype, self.count)
        self.scenario, self.archetype, self.count = "boarding", "pack_hunter", len(self.enemies)
        self.selected_target = self.enemies[0].id
        self._boarding = True
        self.active = True
        world.refresh()
        world.message(f"BOARDING ACTION: {len(self.enemies)} raiders are aboard; defend the ship!")
        return True

    def spawn(self, archetype, pos=None):
        data = ARCHETYPES[archetype]
        if pos is None:
            choices = sorted(self.engine.walkable - {a.pos for a in self.fighters.values() if a.hp > 0})
            choices = [p for p in choices if distance(p, self.fighters["captain"].pos) >= 5]
            if not choices:
                return False
            captain_pos = self.fighters["captain"].pos
            pos = min(choices, key=lambda tile: (distance(tile, captain_pos), tile))
        aid = f"enemy_{len(self.fighters):03d}"
        profile = self.npc_overrides
        def configured(key, default):
            value = profile.get(key)
            return default if value is None else value
        actor = Actor(
            aid, data["name"], *pos,
            weapon=configured("weapon", self.weapon_override or data["weapon"]),
            secondary_weapon=configured("secondary_weapon", data.get("secondary_weapon")),
            personality=configured("personality", self.personality or data["personality"]),
            tactics=configured("tactics", data.get("tactics", "standard")),
            glyph=configured("glyph", data["glyph"]), hp=data["hp"], max_hp=data["hp"],
            speed=configured("speed", self.speed or data["speed"]),
            movement_skill=configured("movement_skill", 0.0),
            stamina=configured("stamina", 100), max_stamina=configured("max_stamina", 100),
            balance=configured("balance", 100.0), fatigue=configured("fatigue", 0.0),
            carried_weight_kg=configured("carried_weight_kg", 0.0),
            carry_capacity_kg=configured("carry_capacity_kg", 24.0),
            perception=configured("perception", 10), strength=configured("strength", self.strength),
            armor=configured("armor", data["armor"]), shield=configured("shield", data.get("shield", False)),
            morale=configured("morale", 100.0), target_part=configured("target_part", "torso"),
            ammo=configured("ammo", 1), spare_shots=configured("spare_shots", 6),
            smoke_charges=configured("smoke_charges", 0))
        condition_tiers = {"wounded": (30.0, 70.0, 0),
                           "maimed": (60.0, 40.0, 1),
                           "critical": (80.0, 20.0, 2)}
        for part in ("head", "torso", "left_arm", "right_arm", "left_leg", "right_leg"):
            condition = profile.get(f"condition_{part}", "healthy")
            state = actor.body_parts[part]
            if condition in condition_tiers:
                damage, structure, tier = condition_tiers[condition]
                state.update(damage=damage, structure=structure)
                actor.injuries[part] = tier
            elif condition == "severed" and part.endswith("_arm"):
                state.update(damage=100.0, structure=0.0)
                actor.severed_parts.append(part)
                actor.statuses["crippled_arm"] = 1_000_000
            elif condition == "severed" and part.endswith("_leg"):
                state.update(damage=100.0, structure=0.0)
                actor.severed_parts.append(part)
                actor.statuses["crippled_leg"] = 1_000_000
        self.engine.add(actor)
        return True

    def _player_actor(self, world, pos, *, facing=(1, 0), smoke_charges=0):
        """Project persistent captain skill/load into temporary arena movement state."""
        skills = self.progression["skills"].values()
        movement_skill = sum(skills) / max(1, len(self.progression["skills"]))
        actor = Actor("captain", "You", *pos, faction="player", glyph="@", facing=facing,
                      smoke_charges=smoke_charges, movement_skill=movement_skill,
                      carried_weight_kg=world.inventory.carried_weight_g() / 1000.0,
                      carry_capacity_kg=24.0)
        return actor

    def exit(self, world):
        if not self.active:
            return False
        world.game_map, world.active_deck, pos = self._backup
        world.player.x, world.player.y, world.player.z = pos
        self._backup = None
        self.active = False
        victory = False
        captain = self.fighters.get("captain") if self.engine else None
        defeat_message = None
        if captain and captain.unconscious:
            if world.inventory.items:
                lost = world.inventory.pop(len(world.inventory.items) - 1)
                lost.pop("pocket", None)
                ground = ((world.player.x, world.player.y) if world.player.z == 0 else
                          (world.player.x, world.player.y, world.player.z))
                world.ground_items.setdefault(ground, []).append(lost)
                defeat_message = f"Knocked unconscious; raiders robbed you of {lost['name']} and left you for dead."
            else:
                defeat_message = "Knocked unconscious; raiders left you for dead."
        if self._boarding:
            victory = captain is not None and captain.active and not any(e.active for e in self.enemies)
            if victory:
                eligible = [enemy for enemy in self.enemies
                            if enemy.surrendered or enemy.hp <= 0 or enemy.unconscious]
                captured = world.ship.capture_boarders(eligible, world)
                if captured:
                    world.message(f"{len(captured)} surrendered boarder(s) secured in the brig.")
            if victory and world.voyages.stop_after_encounter():
                world.voyages.continue_after_sighting()
                world.message("Boarders repelled. The nearby-ship sighting is resolved.")
            elif captain is not None and not captain.active and world.voyages.stop_after_encounter():
                world.voyages.turn_back(world)
                world.message("Overwhelmed by boarders; the navigator orders a retreat.")
            elif world.voyages.stop_after_encounter():
                world.voyages.turn_back(world)
                world.message("You withdraw from the unfinished boarding action; the navigator turns back.")
        self._boarding = False
        if self._boarding_previous:
            self.scenario, self.archetype, self.count = self._boarding_previous
        self._boarding_previous = None
        self._duel_state = None
        self._duel_actor_ids = ("captain", None)
        self._duel_initial_vitality = {}
        self._duel_initial_blood = {}
        self._duel_actor_ids = ("captain", None)
        self._duel_initial_vitality = {}
        self._duel_initial_blood = {}
        world.refresh()
        if defeat_message:
            world.message(defeat_message)
        world.message("Boarding action ended; ship geometry and captain position restored." if victory
                      else "Arena left; original ship and clock unchanged.")
        return True

    def reset(self, world):
        seed = self.engine.seed
        self.exit(world)
        return self.start(world, seed)

    def visible_entities(self):
        if not self.active:
            return []
        visible = []
        for actor in self.enemies:
            if not actor.active:
                continue
            # Shape, not just color, distinguishes states in monochrome mode.
            icon = ("!" if actor.intent else "?" if actor.aim_tiles else
                    "o" if actor.state in {"recovery", "staggered"} else actor.glyph.lower())
            visible.append(replace(actor, glyph=icon))
        return visible

    def select_at(self, world, x, y):
        target = self.engine.at((x, y))
        if target and target.id != "captain":
            self.selected_target = target.id
            self.engine.say(f"TARGET: {target.name}. Space/right-click to attack.")
            return True
        return False

    def target(self):
        enemy = self.fighters.get(self.selected_target)
        if enemy and enemy.active:
            return enemy
        return next((a for a in self.enemies if a.active), None)

    @property
    def guided_duel_available(self):
        """Whether the focused melee command set is in actionable range."""
        player, enemy = self.fighters.get("captain"), self.target()
        if not self.active or not player or not enemy:
            return False
        if len([actor for actor in self.enemies if actor.active]) != 1:
            return False
        try:
            reach = max(duel_weapon_for_actor(player).reach_tiles,
                        duel_weapon_for_actor(enemy).reach_tiles)
        except ValueError:
            return False
        in_range = tile_distance(player.pos, enemy.pos) <= max(3, reach + 2)
        if (self._duel_state is not None
                and self._duel_actor_ids == (player.id, enemy.id)):
            return (in_range and player.next_ready_tick <= self.engine.tick
                    and self._duel_state.to_act == 0)
        return (in_range
                and not player.intent and not enemy.intent
                and not player.movement_path and not enemy.movement_path
                and player.next_ready_tick <= self.engine.tick
                and enemy.next_ready_tick <= self.engine.tick)

    @property
    def guided_duel_engaged(self):
        """Whether the current arena is still inside a started guided exchange."""
        return (self.active and self._duel_state is not None
                and len(self._duel_actor_ids) == 2
                and self._duel_actor_ids[0] == "captain")

    def act(self, world, action, target_id=None):
        if not self.active:
            return False
        if self.finished:
            self.engine.say(" · ".join(self.player_status()))
            return False
        player = self.fighters["captain"]
        duel_action_names = {
            "duel_light": ActionKind.LIGHT, "duel_heavy": ActionKind.HEAVY,
            "duel_parry": ActionKind.PARRY, "duel_dash": ActionKind.DASH,
            "duel_dash_strike": ActionKind.DASH_STRIKE,
        }
        if action in duel_action_names:
            return self._act_guided_duel(world, duel_action_names[action], target_id)
        if self._duel_state is not None:
            self.engine.say("Walking is unavailable during this duel. You are alive: press D then a direction to Dash; Space attacks, G parries.")
            return False
        if action == "body":
            parts = ("torso", "head", "arm", "leg")
            selected = parts[(parts.index(player.target_part) + 1) % len(parts)]
            self.engine.command("target_part", selected)
            world.message(f"Targeting {selected}; next attack records this body part.")
            return True
        if action == "part":
            if target_id not in {"head", "torso", "arm", "hand", "leg"}:
                return False
            self.engine.command("target_part", target_id)
            world.message(f"Targeting {target_id}; next attack records this body part.")
            return True
        if player.next_ready_tick > self.engine.tick:
            self.engine.say("Paused mid-action: N steps the scheduler.")
            return False
        enemy = self.target()
        dest = enemy.pos if enemy else None
        patterns = WEAPONS[player.weapon]["patterns"]
        if action in {"move", "run", "dash"}:
            if action == "dash" and target_id is None:
                target_id = player.facing
            dx, dy = target_id[:2]
            stride = (int(target_id[2]) if action == "dash" and len(target_id) > 2
                      else {"move": 1, "run": 2, "dash": 3}[action])
            dest = player.x + dx * stride, player.y + dy * stride
        elif action == "style":
            action, dest = "switch", target_id
        elif action == "door":
            doors = [pos for pos in self.engine.doors if distance(player.pos, pos) == 1]
            if not doors:
                self.engine.say("No adjacent arena door.")
                return False
            dest = min(doors)
        elif action == "smoke":
            dest = enemy.pos if enemy else None
        elif action == "attack":
            action = "fire" if WEAPONS[player.weapon]["reload"] else patterns[0]
        elif action == "heavy":
            action = "fire" if WEAPONS[player.weapon]["reload"] else patterns[-1]
        elif action in {"guard", "dodge"}:
            if action == "guard":
                action = "brace"
            else:
                choices = [(player.x + dx, player.y + dy) for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))]
                choices = [p for p in choices if p in self.engine.walkable and not self.engine.at(p)
                           and not any(a.intent and p in a.intent.target_tiles for a in self.enemies)]
                if not choices:
                    self.engine.say("No safe dodge tile; no time spent.")
                    return False
                action, dest = "move", max(choices, key=lambda p: distance(p, enemy.pos) if enemy else 0)
        elif action == "hide":
            self.engine.say("No magic hiding: leave aim lines or break sight behind pillars.")
            return False
        result = self.engine.command(action, dest, single_event=self.debug_step)
        world.player.x, world.player.y = player.pos
        world.refresh()
        if self.finished:
            self.engine.say("DUEL ENDED. Esc leaves; Backspace resets the same seed.")
        return result

    def _guided_duel_state(self, enemy):
        """Create/synchronize the bounded kernel state for one adjacent melee duel."""
        player = self.fighters["captain"]
        try:
            player_weapon = duel_weapon_for_actor(player)
            enemy_weapon = duel_weapon_for_actor(enemy)
        except ValueError:
            return None
        if tile_distance(player.pos, enemy.pos) > max(player_weapon.reach_tiles,
                                                       enemy_weapon.reach_tiles, 1) + 2:
            return None
        if (player.intent or enemy.intent or player.movement_path or enemy.movement_path
                or player.next_ready_tick > self.engine.tick
                or enemy.next_ready_tick > self.engine.tick):
            return None
        if (self._duel_state is not None
                and self._duel_actor_ids == (player.id, enemy.id)):
            return self._duel_state
        # Cancel only stale scheduler entries for these two actors. Their token
        # invalidates queued callbacks without touching unrelated encounter work.
        for actor in (player, enemy):
            actor.token += 1
            actor.next_ready_tick = self.engine.tick
            actor.state = "ready"
        vitality = lambda actor: max(1, min(100, int(
            float(actor.blood or 0) * 100 / max(1, actor.max_hp))))
        left = Duelant(duel_stats_for_actor(player), player_weapon,
                       player.x, player.y, player.facing, vitality=vitality(player),
                       resource_tick=self.engine.tick)
        right = Duelant(duel_stats_for_actor(enemy), enemy_weapon,
                        enemy.x, enemy.y, enemy.facing, vitality=vitality(enemy),
                        resource_tick=self.engine.tick)
        self._duel_state = DuelDecisionState(self.engine.tick, left, right)
        self.engine.guided_duel_state = asdict(self._duel_state)
        self._duel_actor_ids = (player.id, enemy.id)
        self._duel_initial_vitality = {player.id: left.vitality, enemy.id: right.vitality}
        self._duel_initial_blood = {player.id: float(player.blood or 0),
                                    enemy.id: float(enemy.blood or 0)}
        return self._duel_state

    def _guided_npc_action(self, state):
        npc, opponent = state.right, state.left
        commitment = opponent.intent
        visible = (VisibleCommitment(
            commitment.action.kind, commitment.target_pos, commitment.impact_tick,
            None, bool(commitment.path and commitment.next_path_step == len(commitment.path)),
        ) if commitment else None)
        belief = CombatBelief().observe(PublicObservation(
            event_id="runtime-visible-weapon", kind="weapon_seen", tick=state.tick,
            weapon=opponent.weapon,
        ))
        def map_legal(action):
            path = tuple((npc.x + action.dx * step, npc.y + action.dy * step)
                         for step in range(1, action.steps + 1))
            if action.kind in {ActionKind.DASH, ActionKind.DASH_STRIKE} and any(
                    tile not in self.engine.walkable for tile in path):
                return False
            if action.kind in {ActionKind.LIGHT, ActionKind.HEAVY}:
                return all(self.engine.open_tile(tile)
                           for tile in ray(npc.pos, opponent.pos)[:-1])
            if action.kind == ActionKind.DASH_STRIKE:
                endpoint = path[-1]
                return all(self.engine.open_tile(tile)
                           for tile in ray(endpoint, opponent.pos)[:-1])
            return True

        legal = tuple(action for action in legal_actions(state) if map_legal(action))
        observation = NPCObservation(
            tick=state.tick, own_pos=npc.pos, own_facing=npc.facing,
            own_stats=npc.stats, own_weapon=npc.weapon,
            opponent_pos=opponent.pos, opponent_facing=opponent.facing,
            opponent_weapon=opponent.weapon, visible_commitment=visible,
            belief=belief, legal_actions=legal,
            counter_window=npc.counter_until > state.tick,
        )
        behavior = {
            "aggressive": BehaviorProfile.FIRST_MOVER,
            "dash_slash": BehaviorProfile.DASH_AND_SLASH,
            "patient_counter": BehaviorProfile.PATIENT_COUNTER,
        }.get(self.fighters[self._duel_actor_ids[1]].tactics, BehaviorProfile.GENERAL)
        return ObservationLimitedPolicy(IntelligenceTier.INFORMED, behavior).choose_action(observation)

    def _act_guided_duel(self, world, kind, target_id):
        player = self.fighters["captain"]
        enemy = self.target()
        if not enemy or len([actor for actor in self.enemies if actor.active]) != 1:
            self.engine.say("Guided duel actions require one active melee opponent.")
            return False
        state = self._guided_duel_state(enemy)
        if state is None:
            self.engine.say("Guided duel actions are available at clear melee contact when both fighters are ready.")
            return False
        if state.to_act != 0:
            self.engine.say("The opponent has the next decision; no player action was committed.")
            return False
        directions = target_id if isinstance(target_id, (tuple, list)) else None
        if kind in {ActionKind.DASH, ActionKind.DASH_STRIKE}:
            if directions is None and kind == ActionKind.DASH_STRIKE:
                dx = (enemy.x > player.x) - (enemy.x < player.x)
                dy = (enemy.y > player.y) - (enemy.y < player.y)
                steps = min(2, max(1, tile_distance(player.pos, enemy.pos)
                                   - duel_weapon_for_actor(player).reach_tiles))
                directions = (dx, dy, steps)
            if directions is None or len(directions) not in (2, 3):
                self.engine.say("Choose a direction and distance for the dash.")
                return False
            dx, dy = int(directions[0]), int(directions[1])
            steps = int(directions[2]) if len(directions) == 3 else 1
            action = DuelAction(kind, dx, dy, steps)
        else:
            action = DuelAction(kind)
        if action not in legal_actions(state):
            self.engine.say("That duel action is not legal from this position or stamina state; no time spent.")
            return False
        path = tuple((state.left.x + action.dx * step, state.left.y + action.dy * step)
                     for step in range(1, action.steps + 1))
        if any(tile not in self.engine.walkable or
               (self.engine.at(tile) and tile != enemy.pos) for tile in path):
            self.engine.say("The committed dash path is blocked; no time spent.")
            return False
        if kind in {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_STRIKE}:
            # Live map blockers are stricter than the bounded open-lane oracle.
            origin = (state.left.x + action.dx * action.steps,
                      state.left.y + action.dy * action.steps) \
                if kind == ActionKind.DASH_STRIKE else state.left.pos
            if any(not self.engine.open_tile(tile)
                   for tile in ray(origin, state.right.pos)[:-1]):
                self.engine.say("A wall or closed door blocks that duel lane; no time spent.")
                return False
        origin = state.left.pos
        target_pos = state.right.pos
        transition = apply_action(state, action)
        events = list(transition.events)
        state = transition.state
        guard = 0
        while state.to_act == 1 and state.left.vitality > 0 and state.right.vitality > 0:
            npc_action = self._guided_npc_action(state)
            transition = apply_action(state, npc_action)
            events.extend(transition.events)
            state = transition.state
            guard += 1
            if guard > 12:
                break
        self._sync_guided_duel(state, events)
        self._duel_state = state
        self.engine.guided_duel_state = asdict(state)
        player_duelist = state.left
        low_stamina_penalty = (20 if player_duelist.stats.stamina * 10
                               < player_duelist.stats.max_stamina * 3 else 0)
        fatigue_control_penalty = player_duelist.stats.fatigue // 4
        self.engine.show_feedback(
            player, f"{kind.value.replace('_', ' ').title()} committed · "
            f"cost {ACTION_COSTS[kind]} stamina, fatigue +{ACTION_FATIGUE[kind]} "
            f"· now {player_duelist.stats.stamina} stamina / "
            f"{player_duelist.stats.fatigue} fatigue · attack control "
            f"−{fatigue_control_penalty} fatigue, −{low_stamina_penalty} low-stamina",
            "resource", outcome="action_cost",
            consequences={"stamina_cost": ACTION_COSTS[kind],
                          "fatigue_added": ACTION_FATIGUE[kind],
                          "stamina_after": player_duelist.stats.stamina,
                          "fatigue_after": player_duelist.stats.fatigue,
                          "fatigue_control_penalty": fatigue_control_penalty,
                          "low_stamina_control_penalty": low_stamina_penalty},
        )
        self.engine.replay.append({"tick": self.engine.tick, "action": action.kind.value,
                                   "target": list(target_pos), "origin": list(origin),
                                   "duel_action": asdict(action),
                                   "duel_state": asdict(state),
                                   "ruleset": "guided-duel-v1"})
        world.player.x, world.player.y = self.fighters["captain"].pos
        world.refresh()
        return True

    def _sync_guided_duel(self, state, events):
        """Project deterministic kernel time, positions, resources and outcomes."""
        player_id, enemy_id = self._duel_actor_ids
        for actor_id, fighter in zip((player_id, enemy_id), (state.left, state.right)):
            actor = self.fighters[actor_id]
            actor.x, actor.y = fighter.pos
            actor.facing = fighter.facing
            actor.stamina = fighter.stats.stamina * actor.max_stamina // fighter.stats.max_stamina
            actor.fatigue = float(fighter.stats.fatigue)
            actor.balance = float(fighter.stats.balance)
            actor.next_ready_tick = fighter.ready_at
            actor.state = ("windup" if fighter.intent and fighter.intent.action.kind in
                           {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_STRIKE}
                           else "parrying" if fighter.intent and fighter.intent.action.kind == ActionKind.PARRY
                           else "dashing" if fighter.intent and fighter.intent.action.kind == ActionKind.DASH
                           else "recovery" if fighter.ready_at > state.tick else "ready")
            initial_vitality = max(1, self._duel_initial_vitality.get(actor_id, 100))
            initial_blood = self._duel_initial_blood.get(actor_id, float(actor.max_hp))
            actor.blood = initial_blood * fighter.vitality / initial_vitality
            actor.sync_legacy_hp()
            if fighter.vitality <= 0:
                actor.unconscious = True
                actor.unconscious_tick = state.tick
                actor.state = "unconscious"
        if state.tick > self.engine.tick:
            self.engine.advance_time(state.tick)
        for event in events:
            actor_id = (player_id, enemy_id)[event.actor]
            actor = self.fighters[actor_id]
            if event.kind not in {"light", "heavy", "dash_strike"}:
                continue
            recipient = (self.fighters[(player_id, enemy_id)[event.target]]
                         if event.target is not None else None)
            label_actor = recipient if event.outcome == "parried" and recipient else actor
            outcome_text = {
                "hit": f"HIT · {event.reason}",
                "wrong-facing": f"HIT · {event.reason}",
                "parried": f"PARRIED · {event.reason}",
                "overpowered": f"PARRY OVERPOWERED · {event.reason}",
                "precision-miss": f"MISS · {event.reason}",
                "committed-lane-miss": f"MISS · {event.reason}",
            }.get(event.outcome, f"{event.outcome.upper()} · {event.reason}")
            attacker_state = state.left if event.actor == 0 else state.right
            recovery_left = max(0, attacker_state.ready_at - event.tick)
            if event.kind == "dash_strike" and event.outcome in {
                    "parried", "overpowered", "precision-miss", "committed-lane-miss"}:
                outcome_text += f" · exposed recovery {format_combat_time(recovery_left)}"
            self.engine.show_feedback(
                label_actor, outcome_text, event.outcome, source=actor,
                recipient=recipient, outcome=event.outcome,
                consequences={"duel_damage": event.damage, "reason": event.reason,
                              "force": event.force, "parry_capacity": event.parry_capacity,
                              "control": event.control, "evasion": event.evasion,
                              "recovery_ticks": recovery_left},
            )
            if event.outcome == "parried" and recipient and recipient.id == player_id:
                defender = state.left if event.target == 0 else state.right
                attacker = state.right if event.actor == 0 else state.left
                speed, foe_speed = (effective_swing_speed(defender.stats, defender.weapon),
                                    effective_swing_speed(attacker.stats, attacker.weapon))
                remaining = max(0, defender.counter_until - event.tick)
                counter = (f"COUNTER WINDOW · Light [Space] within {format_combat_time(remaining)}"
                           if speed >= foe_speed else
                           f"PARRY STOPPED · quick counter speed {speed} < {foe_speed}")
                self.engine.show_feedback(
                    recipient, counter, "counter_window", source=recipient, recipient=actor,
                    outcome="counter_window" if speed >= foe_speed else "counter_too_slow",
                    consequences={"counter_ticks": remaining, "player_speed": speed,
                                  "opponent_speed": foe_speed},
                )

    def request(self, world, action, target_id=None):
        """Execute a requested combat action immediately; previews are a separate read-only API."""
        return self.act(world, action, target_id)

    def preview(self, action, target_id=None):
        """Expose the core's read-only movement/attack estimate to UI hover handlers."""
        if not self.active or not self.engine:
            return {"valid": False, "error": "Arena not running"}
        player = self.fighters["captain"]
        enemy = self.target()
        target = enemy.pos if enemy else None
        if action == "attack":
            action = "fire" if WEAPONS[player.weapon]["reload"] else WEAPONS[player.weapon]["patterns"][0]
        elif action == "heavy":
            action = "fire" if WEAPONS[player.weapon]["reload"] else WEAPONS[player.weapon]["patterns"][-1]
        elif action == "guard":
            action = "brace"
        elif action == "style":
            action, target = "switch", target_id
        elif action == "door":
            doors = [pos for pos in self.engine.doors if distance(player.pos, pos) == 1]
            target = min(doors) if doors else None
        elif action == "smoke":
            target = enemy.pos if enemy else None
        elif action == "throw":
            action = "throw"
        if action in {"move", "run", "dash"}:
            direction = target_id if target_id is not None else player.facing
            if not isinstance(direction, (tuple, list)) or len(direction) != 2:
                if not isinstance(direction, (tuple, list)) or len(direction) != 3 or action != "dash":
                    return {"valid": False, "error": "Movement needs a straight 8-way direction"}
            dx, dy = direction[:2]
            stride = (int(direction[2]) if action == "dash" and len(direction) > 2
                      else {"move": 1, "run": 2, "dash": 3}[action])
            target = player.x + dx * stride, player.y + dy * stride
        elif action in PATTERNS:
            target = (self.fighters.get(target_id).pos
                      if isinstance(target_id, str) and target_id in self.fighters
                      else target_id if isinstance(target_id, tuple) else target)
        return self.engine.preview(action, target, "captain")

    def look(self, aid):
        actor = self.fighters[aid]
        return (f"{actor.name}: {self._apparent_action(actor)}. "
                f"Weapon: {WEAPONS[actor.weapon]['name']}; personality: {actor.personality}. "
                f"Speed: {speed_band(actor, self.engine.tick)} "
                f"({movement_speed(actor, self.engine.tick)} effective / {actor.speed} base). "
                f"Technique: {technique_summary(actor)}. "
                f"Armor {actor.armor}; shield {'yes' if actor.shield else 'no'}; "
                f"targeting {actor.target_part.replace('_', ' ')}.")

    def floating_labels(self):
        """Actor-local target, intent and urgent movement/status cues."""
        if not self.engine:
            return []
        labels = []
        for actor in self.enemies:
            if actor.hp <= 0 or actor.fled:
                continue
            if actor.id == self.selected_target:
                labels.append((actor.id, actor.pos, f"TARGET · {actor.name}",
                               (255, 220, 105)))
            guided_intent = False
            if self._duel_state is not None and actor.id in self._duel_actor_ids:
                guided_side = self._duel_actor_ids.index(actor.id)
                guided_fighter = (self._duel_state.left if guided_side == 0
                                  else self._duel_state.right)
                guided_intent = guided_fighter.intent is not None
            if actor.intent or guided_intent:
                text = self._apparent_action(actor)
                color = (255, 165, 125)
            elif actor.aim_tiles:
                text = self._apparent_action(actor)
                color = (255, 225, 125)
            else:
                text = self._apparent_action(actor)
                color = (200, 210, 220)
            labels.append((actor.id, actor.pos, text, color))
        player = self.fighters.get("captain")
        if player:
            enemy = self.target()
            if (self.guided_duel_available and enemy
                    and tile_distance(player.pos, enemy.pos) <= 1 and player.active):
                guide = self._guided_player_guide(player, enemy)
                labels.append((player.id, player.pos, guide, (150, 225, 255)))
            warning = next((label for active, label in (
                (player.statuses.get("bleeding", 0) > self.engine.tick, "BLEEDING"),
                (player.statuses.get("crippled", 0) > self.engine.tick
                 or player.statuses.get("crippled_leg", 0) > self.engine.tick, "LEG INJURED"),
                (player.statuses.get("staggered", 0) > self.engine.tick, "STAGGERED"),
            ) if active), None)
            if warning:
                labels.append((player.id, player.pos, warning, (255, 135, 125)))
        return labels

    def _guided_player_guide(self, player, enemy):
        """Describe only verified timing options against a public commitment."""
        state = self._duel_state
        if state is None:
            return "No attack committed · Light [Space] / Heavy [F] · Parry [G] if they strike"
        opponent = state.right
        intent = opponent.intent
        if (not intent or intent.action.kind not in
                {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_STRIKE}
                or intent.target_pos != player.pos):
            if opponent.ready_at > state.tick:
                return "Enemy recovering · Light [Space] can punish"
            return "Observing · no committed attack; choose Light [Space] / Heavy [F]"

        deadline = intent.impact_tick - state.tick
        responses = []
        legal = legal_actions(state) if state.to_act == 0 else ()
        parry = DuelAction(ActionKind.PARRY)
        if parry in legal:
            projected = commit_action(state, parry).left.intent
            if projected.parry_start <= intent.impact_tick < projected.parry_end:
                responses.append("Parry [G] fits window; force uncertain")
            else:
                responses.append("Parry [G] starts too late")
        elif state.left.stats.stamina < 20:
            responses.append("Parry unavailable (need 20 stamina)")

        escape = None
        for action in legal:
            if action.kind != ActionKind.DASH:
                continue
            projected = commit_action(state, action).left.intent
            if (projected.path_ticks and projected.path_ticks[0] < intent.impact_tick
                    and all(tile in self.engine.walkable for tile in projected.path)):
                escape = action
                break
        if escape:
            responses.append("Dash [D+dir] clears before contact")
        elif deadline > 0:
            responses.append("Dash cannot clear before contact")
        return " · ".join(responses) if responses else "Committed threat · no verified response"

    def feedback_labels(self):
        """Expose structured outcome facts; renderer owns wall-clock age and motion."""
        if not self.engine:
            return []
        labels = []
        colors = {"feint": (255, 225, 125), "miss": (205, 205, 205),
                  "cover_block": (205, 205, 205), "parry": (130, 225, 255),
                  "stagger": (255, 165, 125), "block": (130, 225, 255),
                  "blocked": (255, 190, 130), "kill": (255, 225, 125),
                  "down": (255, 125, 125), "hit": (255, 225, 125),
                  "wound": (255, 135, 125), "sever": (255, 100, 100)}
        for item in self.engine.feedback:
            actor = self.fighters.get(item["actor_id"])
            if actor:
                labels.append((actor.id, tuple(item["position"]), item["text"],
                               colors.get(item["type"], (235, 235, 235)), item["event_id"]))
        return labels

    def _part_state(self, actor, part):
        if part in actor.severed_parts:
            return "GONE"
        if part == "arm" and actor.statuses.get("crippled_arm", 0) > self.engine.tick:
            return "DIS"
        if part == "leg" and any(actor.statuses.get(k, 0) > self.engine.tick
                                  for k in ("crippled", "crippled_leg")):
            return "DIS"
        if part == "torso" and actor.statuses.get("bleeding", 0) > self.engine.tick:
            return "BLEED"
        body = actor.body_parts.get(part, {})
        damage = body.get("damage", 0)
        if damage >= 60:
            return "CRIT"
        if damage >= 40:
            return "MAIM"
        if damage > 0:
            return "WOUND"
        severity = actor.injuries.get(part)
        return "OK" if severity is None else ("WOUND", "MAIM", "CRIT")[max(0, min(2, severity))]

    def combat_overlay_lines(self):
        """Four-layer status/timeline/log strip; display only, never mutates combat."""
        if not self.active or not self.engine:
            return []
        return self.hud_lines(None)

    def _condition_summary(self, actor):
        active = sorted(name.replace("_", " ") for name, expiry in actor.statuses.items()
                        if expiry > self.engine.tick)
        if not active:
            return "none"
        return ", ".join(active)

    def _apparent_action(self, actor):
        if (self._duel_state is None and actor.id != "captain"
                and actor.id == (self.target().id if self.target() else None)
                and self.guided_duel_available):
            return "Observing"
        if self._duel_state is not None and actor.id in self._duel_actor_ids:
            side = self._duel_actor_ids.index(actor.id)
            fighter = self._duel_state.left if side == 0 else self._duel_state.right
            commitment = fighter.intent
            if commitment:
                kind = commitment.action.kind
                if kind == ActionKind.WAIT:
                    return f"Waiting · {format_combat_time(commitment.impact_tick - self._duel_state.tick)}"
                if kind == ActionKind.PARRY:
                    return "Parrying"
                label = {ActionKind.LIGHT: "Preparing Light",
                         ActionKind.HEAVY: "Preparing Heavy",
                         ActionKind.DASH: "Dashing",
                         ActionKind.DASH_STRIKE: "Preparing Dash-strike"}.get(kind, "Observing")
                if kind in {ActionKind.LIGHT, ActionKind.HEAVY, ActionKind.DASH_STRIKE}:
                    label += f" · {format_combat_time(commitment.impact_tick - self._duel_state.tick)}"
                return label
            if fighter.vitality <= 0:
                return "Down"
            if fighter.ready_at > self._duel_state.tick:
                return "Recovering"
            return "Observing"
        if actor.intent:
            pattern = PATTERNS[actor.intent.kind]["name"].upper()
            player = self.fighters.get("captain")
            if player and player.pos in actor.intent.target_tiles:
                return f"{pattern} INCOMING"
            return f"PREPARING {pattern}"
        if actor.aim_tiles:
            return f"AIMING {WEAPONS[actor.weapon]['name'].upper()}"
        if actor.movement_path:
            return {"dash": "Dashing", "run": "Sprinting", "walk": "Moving"}.get(
                actor.movement_mode, "Moving")
        if actor.state in {"recovery", "staggered", "reloading"}:
            action = {"reloading": "Reloading", "staggered": "Staggered", "recovery": "Recovering"}[actor.state]
            return action.upper()
        if actor.movement_mode == "dash":
            return "Recovering from dash"
        if actor.movement_mode == "run":
            return "Sprinting"
        if actor.behavior == "fleeing":
            return "retreating"
        if actor.behavior == "searching":
            return "Searching"
        if actor.behavior == "engaging":
            return "Closing distance" if actor.state == "moving" else "Watching for an opening"
        return "Watching"

    def player_status(self):
        """Persistent player-facing truth, independent of transient combat logs."""
        player = self.fighters["captain"]
        if player.hp <= 0:
            return "YOU ARE DEAD — DEFEAT", "Fight over. Backspace: retry | Esc: leave"
        if player.unconscious:
            return "YOU ARE UNCONSCIOUS — DEFEAT", "You are alive but cannot act. Backspace: retry | Esc: leave"
        if not player.active:
            return "FIGHT OVER — YOU CANNOT ACT", "Backspace: retry | Esc: leave"
        if self.finished:
            return "VICTORY — YOU ARE ALIVE", "All opponents defeated or withdrawn. Backspace: retry | Esc: leave"
        if player.next_ready_tick > self.engine.tick:
            return ("YOU ARE ALIVE — RECOVERING",
                    f"Ready in {format_combat_time(player.next_ready_tick - self.engine.tick)}. "
                    + ("Debug pause: N advances time." if self.debug_step else "Action is still resolving."))
        if self.guided_duel_engaged:
            return ("YOU ARE ALIVE — CHOOSE AN ACTION",
                    "Walking is locked in this duel. D + direction: Dash | Space: attack | G: parry")
        return "YOU ARE ALIVE — YOUR TURN", "Time waits for you. Arrows: move | Space: attack | Enter: wait"

    def hud_lines(self, world):
        player = self.fighters["captain"]
        enemy = self.target()
        phase, guidance = self.player_status()
        wound_names = {"head": "H", "torso": "T", "left_arm": "A", "right_arm": "A",
                       "left_leg": "L", "right_leg": "L"}
        damaged_parts = {part for part, state in player.body_parts.items() if state["damage"] > 0}
        damaged_parts.update(part for part, tier in player.injuries.items() if tier >= 0)
        damaged = "".join(dict.fromkeys(wound_names[part] for part in damaged_parts
                                              if part in wound_names)) or "—"
        lines = [phase,
                 f"YOU · Sprint {'ON' if player.move_mode == 'run' else 'OFF'} · "
                 f"Stamina {player.stamina}/{player.max_stamina} · Fatigue {player.fatigue:.0f} · "
                 f"Balance {player.balance:.0f} · "
                 f"Shock {player.shock:.0f}",
                 f"Health {player.hp}/{player.max_hp} · Blood {player.blood:.0f} · Wounds {damaged} · Aim: {player.target_part.replace('_', ' ')}",
                 f"KIT {WEAPONS[player.weapon]['name']} · ammo {player.ammo} · spare shots {player.spare_shots}"]
        if enemy:
            foe_action = self._apparent_action(enemy)
            lines.append(f"FOE {enemy.name} · {foe_action}")
            lines.append(f"Speed: {speed_band(enemy, self.engine.tick)} · "
                         f"Technique: {technique_summary(enemy)}")
            lines.extend(self.enemy_body_diagram(enemy, player.target_part).splitlines())
        events = self.timeline_events()
        lines.append("Next: " + (" | ".join(events[:2]) if events else "no committed event"))
        return lines

    def enemy_body_diagram(self, actor, aimed_part="torso"):
        """Compact ASCII condition map; '*' marks the currently selected aim region."""
        def mark(part, label):
            state = self._part_state(actor, part)
            condition = {"OK": "intact", "WOUND": "wounded", "MAIM": "maimed",
                         "CRIT": "critical", "DIS": "disabled", "BLEED": "bleeding",
                         "GONE": "missing"}.get(state, state.lower())
            aliases = {part, "arm" if "arm" in part else "leg" if "leg" in part else part}
            if part == "left_arm":
                aliases.add("hand")
            selected = aimed_part in aliases
            text = f"{label}:{condition}"
            return f"[{text}]" if selected else text
        return ("Enemy body · front view; L/R are the enemy's\n"
                f"                 {mark('head', 'HEAD')}\n"
                f" {mark('left_arm', 'L ARM')}  /   |   \\  {mark('right_arm', 'R ARM')}\n"
                f"                    {mark('torso', 'TORSO')}\n"
                f" {mark('left_leg', 'L LEG')}  /     \\  {mark('right_leg', 'R LEG')}")

    def timeline_events(self):
        """Read-only ordered summary of committed intents, movement and readiness."""
        events = []
        for actor in self.fighters.values():
            if not actor.active:
                continue
            if actor.intent:
                tick = actor.intent.resolves_at_tick
                label = f"{actor.name}: {PATTERNS[actor.intent.kind]['name']} in {format_combat_time(max(0, tick-self.engine.tick))}"
                if actor.id != "captain" and self.fighters["captain"].pos in actor.intent.target_tiles:
                    label += " · THREAT"
            elif actor.movement_path:
                tick = actor.next_ready_tick - actor.movement_recovery_ticks
                label = f"{actor.name}: arrives in {format_combat_time(max(0, tick-self.engine.tick))}"
            else:
                tick = max(self.engine.tick, actor.next_ready_tick)
                label = f"{actor.name}: ready in {format_combat_time(max(0, tick-self.engine.tick))}"
            events.append((tick, actor.id, label))
        return [row[2] for row in sorted(events)[:4]]

    def overlay(self, tile):
        if not self.show_intents:
            return None
        threats = [a.intent for a in self.fighters.values() if a.intent and tile in a.intent.target_tiles]
        if not threats:
            if any(tile in a.aim_tiles for a in self.fighters.values() if a.aim_tiles):
                return "·", (220, 220, 120)
            return None
        intent = min(threats, key=lambda i: i.resolves_at_tick)
        return ("!", (230, 75, 70)) if intent.actor_id != "captain" else ("x", (80, 180, 220))

    def debug_rows(self):
        return [json.dumps(self.engine.report(), indent=2), *[self.look(a.id) + "\n" + json.dumps(a.utilities, sort_keys=True)
                for a in self.fighters.values()]]

    def dump(self, path):
        Path(path).write_text(json.dumps({"combat_ruleset_version": COMBAT_RULESET_VERSION,
                                         "state": self.engine.snapshot(), "report": self.engine.report(),
                                         "guided_duel": asdict(self._duel_state)
                                         if self._duel_state else None,
                                         "guided_duel_state_version": 1 if self._duel_state else None,
                                         "guided_duel_actors": list(self._duel_actor_ids)
                                         if self._duel_state else None,
                                         "replay": self.engine.replay,
                                         "initial": self.engine.starting_state,
                                         "walkable": sorted(self.engine.walkable),
                                         "environment": {"doors": [[list(p), opened] for p, opened in sorted(self.engine.doors.items())],
                                          "hazards": [[list(p), kind] for p, kind in sorted(self.engine.hazards.items())]}},
                                         indent=2), encoding="utf-8")

    def restore_guided_duel_snapshot(self, payload):
        """Restore a validated duel-kernel state onto a matching active arena.

        This restores only the deterministic two-actor slice, not campaign or
        legacy multi-actor combat state.
        """
        if (not isinstance(payload, dict)
                or payload.get("combat_ruleset_version") != COMBAT_RULESET_VERSION
                or payload.get("guided_duel_state_version") != 1
                or payload.get("guided_duel") is None):
            raise ValueError("unsupported or missing guided duel save data")
        state = duel_state_from_data(payload["guided_duel"])
        actor_ids = payload.get("guided_duel_actors")
        if (not isinstance(actor_ids, list) or len(actor_ids) != 2
                or actor_ids[0] != "captain" or actor_ids[1] not in self.fighters
                or actor_ids[0] == actor_ids[1]):
            raise ValueError("guided save must identify captain and one active opponent")
        player_id, enemy_id = actor_ids
        enemy = self.fighters[enemy_id]
        if enemy not in self.enemies or not enemy.active:
            raise ValueError("guided save opponent is not active in this arena")
        for pos in (state.left.pos, state.right.pos):
            if pos not in self.engine.walkable:
                raise ValueError("guided save places a duelist outside walkable arena tiles")
            occupant = self.engine.at(pos)
            if occupant and occupant.id not in actor_ids:
                raise ValueError("guided save overlaps another active combatant")
        replay = payload.get("replay", [])
        if not isinstance(replay, list):
            raise ValueError("guided save replay must be a list")
        for row in replay:
            if (not isinstance(row, dict) or row.get("ruleset") != "guided-duel-v1"
                    or row.get("duel_state") is None):
                raise ValueError("guided save contains an incompatible replay entry")
            duel_state_from_data(row["duel_state"])
        if replay and duel_state_from_data(replay[-1]["duel_state"]) != state:
            raise ValueError("guided save state does not match its replay tail")
        if state.tick < self.engine.tick:
            raise ValueError("guided save predates the active arena clock")

        self._duel_actor_ids = (player_id, enemy_id)
        self._duel_state = state
        for actor_id in actor_ids:
            self.fighters[actor_id].token += 1
        self._duel_initial_vitality = {
            player_id: max(1, state.left.vitality), enemy_id: max(1, state.right.vitality)}
        self._duel_initial_blood = {
            player_id: float(self.fighters[player_id].blood or 0),
            enemy_id: float(enemy.blood or 0)}
        self.engine.replay = deepcopy(replay)
        self.engine.guided_duel_state = asdict(state)
        self._sync_guided_duel(state, ())
        return state

    def progression_snapshot(self):
        return deepcopy(self.progression)

    def restore_progression(self, data):
        if not isinstance(data, dict) or set(data) not in ({"skills", "xp"}, {"skills", "xp", "traits"}):
            raise ValueError("Invalid legacy combat progression")
        for group in ("skills", "xp"):
            if not isinstance(data[group], dict) or set(data[group]) != {"sword", "brawl", "gun"}:
                raise ValueError("Invalid legacy combat skills")
            for value in data[group].values():
                if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid legacy combat value")
                if group == "skills" and not 1 <= value <= 99:
                    raise ValueError("Invalid legacy combat skill")
        traits = data.get("traits", ["quick_learner"])
        if not isinstance(traits, list) or any(t not in {"quick_learner", "steady_hand", "patient", "hard_hitter"} for t in traits):
            raise ValueError("Invalid legacy combat traits")
        self.progression = deepcopy(data)
        self.progression["traits"] = list(traits)
