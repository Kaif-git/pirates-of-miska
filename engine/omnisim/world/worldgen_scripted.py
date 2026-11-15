"""Phase 1 worldgen: SCRIPTED village of Thornhollow.

Reads data/world_scripted/starter_town.yaml and stamps: grass, forest, pond,
palisade wall + gates, roads, market + stalls + well, farm, lantern posts,
buildings (walls + floor + door), player start, standing-still NPCs.

Random gen lives in worldgen_random_stub.py — untouched until model is proven.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import random

import numpy as np
import yaml

from omnisim.entities.entity import Entity
from omnisim.world import tile as T

MARKET_RESERVE = 1500  # the Crown's purse via Bram: deep, finite, visible
# (500 trade stabilizer + 1000 crown land fund — deeds flow through it)
from omnisim.entities.items import defs as item_defs
from omnisim.systems.schedule import parse_schedule
from omnisim.systems.skills import ensure_skills
from omnisim.world.places import build_places
from omnisim.world.map import GameMap


@dataclass
class Village:
    name: str
    game_map: GameMap
    player: Entity
    npcs: list[Entity] = field(default_factory=list)
    turn: int = 0
    log: list[str] = field(default_factory=list)
    clock_pts: int = 36000  # Day 1, 06:00 — the village wakes as you arrive
    felled: list = field(default_factory=list)  # (x, y) stumps regrown at dawn
    foraged: set = field(default_factory=set)  # picked-clean wild tiles, fruited at dawn
    work_cd: dict = field(default_factory=dict)  # site key -> clock_pts ready again
    rng: object = field(default=None)  # deterministic wander; set at gen
    watch_id: object = field(default=None)  # entity id Tab is following
    ground: dict = field(default_factory=dict)
    ground_lore: dict = field(default_factory=dict)  # (x,y) -> id -> {by, origin} (M3)
    stalls: list = field(default_factory=list)
    torch_xy: list[tuple[int, int]] = field(default_factory=list)
    places: list = field(default_factory=list)
    wall_rect: tuple[int, int, int, int] | None = None
    light = None  # float (h, w) brightness 0..1, refreshed by systems
    resale_day: int = -1  # Smithing L6 repair-and-resell: last day the +2c fired
    campfires: dict = field(default_factory=dict)  # (x,y) -> charges left (M8)
    chests: dict = field(default_factory=dict)  # (x,y) -> {"pack": {}, "lore": {}} (M8)
    work_posts: dict = field(default_factory=dict)  # good -> {"at": (x,y), "skill": s} (CHAIN)
    made_today: dict = field(default_factory=dict)  # item -> {"qty": n, "by": [names]} (CHAIN)
    sold_today: dict = field(default_factory=dict)  # item -> qty moved through stalls (CHAIN)
    trades: list = field(default_factory=list)  # ECON: every deal {day,time,seller,buyer,item,qty,price}
    plots: dict = field(default_factory=dict)  # ECON: good -> owner name|None (commons)
    land_parcels: list = field(default_factory=list)  # LAND: live deeds (data/land.yaml at gen)
    workspaces: dict = field(default_factory=dict)  # V1: station key -> {"condition": 0..100} (GDD 5.2)
    scar_mem: dict = field(default_factory=dict)  # P2: good -> last-3 dawn scarcities (flex signal smoothing)
    well_xy: tuple | None = None  # the town well: drink/wash anchor (spec well)
    fish_stock: float = 80.0  # Stillpond's living stock: catches draw it down, dawns recruit
    fish_K: float = 120.0  # pond capacity (data: pond.K)
    fish_r: float = 0.15  # intrinsic recruitment (data: pond.r; MSY = rK/4)
    chill_days: int = 0  # WEATHER: dawns of cold snap remaining (hearths eat wood)
    season: str = "autumn"  # STEP 3: calendar season (ambient warmth); soaks/tests override

    def entities(self) -> list[Entity]:
        return [self.player, *self.npcs]

    def entity_at(self, x: int, y: int) -> Entity | None:
        for e in self.entities():
            if e.x == x and e.y == y:
                return e
        return None

    def say(self, msg: str) -> None:
        self.log.append(msg)
        del self.log[:-2000]  # a real journal: panels show the tail, the day stays


def _stamp_rect(tiles: np.ndarray, x1: int, y1: int, x2: int, y2: int, t: int) -> None:
    tiles[y1:y2 + 1, x1:x2 + 1] = t


def _stamp_building(tiles: np.ndarray, spec: dict) -> None:
    x, y, w, h = spec["x"], spec["y"], spec["w"], spec["h"]
    wall = T.WALL_STONE if spec.get("wall") == "stone" else T.WALL_WOOD
    _stamp_rect(tiles, x, y, x + w - 1, y + h - 1, T.FLOOR)          # interior
    tiles[y, x:x + w] = wall                                          # N wall
    tiles[y + h - 1, x:x + w] = wall                                  # S wall
    tiles[y:y + h, x] = wall                                          # W wall
    tiles[y:y + h, x + w - 1] = wall                                  # E wall
    tiles[spec["door_y"], spec["door_x"]] = T.DOOR


def generate_scripted(data_dir: Path, spec_file: str = "starter_town.yaml") -> Village:
    spec = yaml.safe_load((data_dir / spec_file).read_text(encoding="utf-8"))
    try:  # STEP 10: content drops merge data rows at boot (data only)
        from omnisim.systems.content_drops import apply as _drops_apply
        _drops_apply()
    except Exception:
        pass
    _drops_village = None
    w, h = spec["map"]["width"], spec["map"]["height"]
    tiles = np.full((h, w), T.GRASS, dtype=np.int8)

    # --- forest (outside the walls only) ---
    wall = spec["wall"]
    frng = random.Random(spec["forest"]["seed"])
    placed = 0
    for _ in range(spec["forest"]["count"] * 4):
        if placed >= spec["forest"]["count"]:
            break
        x, y = frng.randrange(w), frng.randrange(h)
        if wall["x1"] - 1 <= x <= wall["x2"] + 1 and wall["y1"] - 1 <= y <= wall["y2"] + 1:
            continue
        if tiles[y, x] == T.GRASS:
            tiles[y, x] = T.TREE
            placed += 1

    # --- pond ellipse ---
    pond = spec["pond"]
    for y in range(h):
        for x in range(w):
            nx = (x - pond["cx"]) / pond["rx"]
            ny = (y - pond["cy"]) / pond["ry"]
            if nx * nx + ny * ny <= 1.0 and tiles[y, x] == T.GRASS:
                tiles[y, x] = T.WATER

    # --- quarry teeth (iron for the forge; unwalkable grey stone) ---
    _q = spec.get("quarry") or {}
    if _q:
        for y in range(int(_q.get("y1", 5)), int(_q.get("y2", 8)) + 1):
            for x in range(int(_q.get("x1", 106)), int(_q.get("x2", 109)) + 1):
                if tiles[y, x] in (T.GRASS, T.TREE):
                    tiles[y, x] = T.WALL_STONE

    # --- palisade wall (gates punched afterwards) ---
    _stamp_rect(tiles, wall["x1"], wall["y1"], wall["x2"], wall["y1"], T.WALL_PALISADE)
    _stamp_rect(tiles, wall["x1"], wall["y2"], wall["x2"], wall["y2"], T.WALL_PALISADE)
    _stamp_rect(tiles, wall["x1"], wall["y1"], wall["x1"], wall["y2"], T.WALL_PALISADE)
    _stamp_rect(tiles, wall["x2"], wall["y1"], wall["x2"], wall["y2"], T.WALL_PALISADE)
    for g in spec["gates"]:
        tiles[g["y"], g["x"]] = T.DOOR

    # --- roads ---
    for r in spec["roads"]:
        if r["axis"] == "h":
            for x in range(r["x1"], r["x2"] + 1):
                if tiles[r["y"], x] in (T.GRASS,):
                    tiles[r["y"], x] = T.ROAD
        else:
            for y in range(r["y1"], r["y2"] + 1):
                if tiles[y, r["x"]] in (T.GRASS,):
                    tiles[y, r["x"]] = T.ROAD

    # --- market square + stalls + well ---
    m = spec["market"]
    _stamp_rect(tiles, m["x1"], m["y1"], m["x2"], m["y2"], T.FLOOR)
    for s in spec["stalls"]:
        tiles[s["y"], s["x"]] = T.STALL
    tiles[spec["well"]["y"], spec["well"]["x"]] = T.WELL

    # --- footpaths (roads on grass only, so walls/trees are never overwritten) ---
    for p in spec.get("paths", []):
        for y in range(p["y1"], p["y2"] + 1):
            for x in range(p["x1"], p["x2"] + 1):
                if tiles[y, x] == T.GRASS:
                    tiles[y, x] = T.ROAD

    # --- farm (a second field east when the village grows) ---
    f = spec["farm"]
    _stamp_rect(tiles, f["x1"], f["y1"], f["x2"], f["y2"], T.FARM)
    if spec.get("farm2"):
        f2 = spec["farm2"]
        _stamp_rect(tiles, f2["x1"], f2["y1"], f2["x2"], f2["y2"], T.FARM)

    # --- buildings ---
    for b in spec["buildings"]:
        _stamp_building(tiles, b)

    # --- lantern posts (decor, walkable so they never trap the player) ---
    for tx, ty in spec["torches"]:
        if tiles[ty, tx] in (T.GRASS, T.ROAD, T.FLOOR):
            tiles[ty, tx] = T.TORCH

    # --- furniture (explicit per-building dressing; beds are walkable) ---
    _furn_ids = {"bed": T.BED, "table": T.TABLE, "chair": T.CHAIR,
                 "chest": T.CHEST, "hearth": T.HEARTH, "shelf": T.SHELF,
                 "anvil": T.ANVIL, "workbench": T.WORKBENCH}
    for f in spec.get("furniture", []):
        tiles[f["y"], f["x"]] = _furn_ids[f["t"]]

    game_map = GameMap(w, h, tiles)

    ps = spec["player_start"]
    player = Entity(id=1, name="You", x=ps["x"], y=ps["y"], glyph="@",
                    color=(255, 255, 255),
                    comps={"role": "player", "hp": 20, "max_hp": 20,
                           "atk": 2, "speed": 100, "hunger": 0.0, "skills": {}, "skillxp": {},
                           "starve_acc": 0.0, "pack": {"bread": 3, "coin": 10}})

    import random as _random
    import yaml as _yaml2
    _arch_path = data_dir.parent / "archetypes.yaml"
    _arch = {}
    if _arch_path.is_file():
        _arch = (_yaml2.safe_load(_arch_path.read_text(encoding="utf-8")) or {})
    roles = _arch.get("roles", {})
    speed_table = _arch.get("speed", {}) if isinstance(_arch.get("speed"), dict) else {}
    npcs: list[Entity] = []
    for i, s in enumerate(spec["spawns"], start=2):
        role = s.get("role", "villager")
        pocket = {"apple": 1, "coin": 2}  # ECON: the world starts on raw —
        if s["name"] == "Mira":  # yesterday's flour bin: the chain can start
            pocket = {"apple": 1, "coin": 4, "wheat": 6}
        if s["name"] in ("Elin", "Ash"):  # seed pinches in the apron: hands that
            pocket = {"apple": 1, "coin": 2, "wheat": 2,  # work the field start sown
                      "wheat_seed": 4, "barley_seed": 3, "turnip_seed": 2}
        if s["name"] == "Sella":  # the smith's fuel pile: the chain can start
            pocket = {"apple": 1, "coin": 4, "firewood": 8}
        if s["name"] == "Dunna":  # the miller's first grist: stones turn day one
            pocket = {"apple": 1, "coin": 2, "wheat": 4}
        if s["name"] == "Cobb":  # billets for the saw: beams by midday
            pocket = {"apple": 1, "coin": 2, "firewood": 6}
        if s["name"] == "Hob":  # seed pinches in the apron, east field
            pocket = {"apple": 1, "coin": 2, "wheat": 2,
                      "wheat_seed": 4, "barley_seed": 3, "turnip_seed": 2}
        rdef = roles.get(role, {}) if isinstance(roles, dict) else {}
        ident = _random.Random(s["name"])  # WEB: stable faces — same name, same age
        gender = ident.choice(["woman", "man"])
        age = ident.randint(8, 14) if role == "kid" else ident.randint(17, 70)
        npc = Entity(
            id=i, name=s["name"], x=s["x"], y=s["y"],
            glyph=s.get("glyph", "@"), color=tuple(s.get("color", [200, 200, 200])),
            comps={"role": role, "standing": True, "pocket": pocket,
                   "hp": 10, "max_hp": 10,  # M6: hunger 100+ drains these; 0 = dead
                   "age": age, "gender": gender,
                   "hunger": 0.0, "activity": "idle", "path": [],
                   "dest": None, "move_budget": 0.0, "block_idx": -1,
                   "work_acc": 0.0, "starve_acc": 0.0, "fatigue": 0.0, "bought": False, "wp": 0,
                    "work_skill": s.get("work_skill", rdef.get("work_skill")),
                    "invincible": bool(s.get("invincible", False)),  # MERCHANT:
                    # infrastructure with a name — needs frozen, death refused
                    "tools": {**(rdef.get("tools", {}) or {}), **(s.get("tools", {}) or {})},
                    "speed": speed_table.get(role, speed_table.get("default", 0.85))},
        )
        if not npc.comps.get("invincible"):  # STEP 4: nine fixed numbers —
            try:  # same name, same soul (ident RNG); infrastructure stays traitless
                from omnisim.systems.mood import assign_traits as _at
                _at(npc, ident)
            except Exception:
                pass
        if s.get("schedule"):
            npc.comps["sched"] = parse_schedule(s)
        # P2 Utility AI: anchors for the day-planner; YAML stays as fallback.
        try:
            _home = tuple(s["home"]) if s.get("home") else (s["x"], s["y"])
        except Exception:
            _home = (s["x"], s["y"])
        try:
            _work = tuple(s["work"]) if s.get("work") else None
        except Exception:
            _work = None
        try:
            _patrol = [tuple(p) for p in s["patrol"]] if s.get("patrol") else None
        except Exception:
            _patrol = None
        npc.comps["anchors"] = {"home": _home, "work": _work, "patrol": _patrol}
        npc.comps["work_at"] = _work or _home
        npcs.append(npc)

    import copy
    assort = item_defs().get("stalls", {})

    stalls = []
    for s in spec.get("stalls", []):
        par = copy.deepcopy(assort.get(s.get("goods", "market"), {}))
        par = {k: list(v) for k, v in par.items()}  # [qty, price] pairs
        name = "the Market"  # one counter for the whole village
        opening = {k: list(v) for k, v in par.items()}  # FOUNDERS' STORES:
        # the shelves open FULL (grain, bread, and tackle the founders hauled
        # in) — finite, declared, eaten down. Thereafter the village refills
        # them or goes without: no cart, no wholesale, no outside.
        stalls.append({"x": s["x"], "y": s["y"], "name": name, "stock": opening,
                       "restock": par, "till": MARKET_RESERVE, "float": MARKET_RESERVE})
        # MERCHANT: Bram capitalizes the Market once, deep, from his house
        # reserve — a stabilizer fund, finite and visible. No wages, no
        # wholesale, no cart: producers stock these shelves, makers empty them.
    ground: dict[tuple[int, int], dict] = {}
    ground_lore: dict[tuple[int, int], dict] = {}
    for g in spec.get("ground", []):
        key = (g["x"], g["y"])
        ground.setdefault(key, {})
        ground[key][g["id"]] = ground[key].get(g["id"], 0) + g.get("qty", 1)
        if g.get("by") or g.get("origin"):
            ground_lore.setdefault(key, {})[g["id"]] = {"by": g.get("by", "unknown"),
                                                        "origin": g.get("origin", "the wilds")}
    torch_xy = [(x, y) for y in range(game_map.height) for x in range(game_map.width)
                if game_map.tile_id(x, y) == T.TORCH]
    import yaml as _yaml
    arch_path = data_dir.parent / "archetypes.yaml"
    roles = {}
    if arch_path.is_file():
        roles = (_yaml.safe_load(arch_path.read_text(encoding="utf-8")) or {}).get("roles", {})
    for n in npcs:
        ensure_skills(n, (roles.get(n.get("role", ""), {}) or {}).get("skills"))
    ensure_skills(player)
    wall = spec["wall"]
    village = Village(name=spec.get("name", "Thornhollow"), game_map=game_map,
                      player=player, npcs=npcs, ground=ground, ground_lore=ground_lore,
                      stalls=stalls,
                      rng=_random.Random(7),
                      torch_xy=torch_xy,
                      places=build_places(spec),
                      wall_rect=(wall["x1"], wall["y1"], wall["x2"], wall["y2"]))
    village.work_posts = _work_posts(game_map, (ps["x"], ps["y"]))
    meadow = (village.work_posts.get("herbs") or {}).get("at")  # HERBS: Wren works
    if meadow:  # where the wild things are — not where the yaml guessed
        for n in npcs:
            if n.name == "Wren":
                n.comps["work_at"] = (int(meadow[0]), int(meadow[1]))
                for b in n.comps.get("sched", []):
                    if b.get("act") == "work":
                        b["at"] = (int(meadow[0]), int(meadow[1]))
    # P2 Utility AI: Day 1 is planned from needs, not read from YAML.
    # The YAML parse stays in comps['fallback_sched'] if planning ever fails.
    try:
        from omnisim.systems.ai_agenda import plan_day as _plan
        _fell_back = []
        for n in npcs:
            n.comps["fallback_sched"] = n.comps.get("sched", [])
            try:
                planned = _plan(n, village)
                if planned:
                    n.comps["sched"] = planned
                else:
                    _fell_back.append(n.name)
            except Exception:
                _fell_back.append(n.name)
        if _fell_back:
            village.say(f"The old routine holds for: {', '.join(_fell_back)}.")
    except Exception:
        pass
    import copy as _copy2
    from omnisim.systems.ownership import land_spec, sync_plots
    try:  # STEP 10: drop goods reach live shelves (founders' stores)
        from omnisim.systems.content_drops import apply as _drops_apply2
        _drops_apply2(village)
    except Exception:
        pass
    try:  # SEEDED RUNS: a drop trade naming seed: <name> starts that
        # founder in the trade from day one (kit + skill, data-driven).
        from omnisim.systems.production import trades as _seed_trs
        for _t in _seed_trs().values():
            _sn = (_t.get("seed") or "")
            if not _sn:
                continue
            for _n in npcs:
                if _n.name == _sn:
                    _n.comps["work_skill"] = _t.get("skill", _n.get("work_skill"))
                    _tools = _n.get("tools", {}) or {}
                    for _k, _q in ((_t.get("starting_kit", {}) or {}).items()):
                        _tools[_k] = int(_tools.get(_k, 0)) + int(_q or 0)
                    _n.comps["tools"] = _tools
                    break
    except Exception:
        pass
    village.land_parcels = _copy2.deepcopy(land_spec().get("parcels", []))
    sync_plots(village)  # LAND: the deeds say whose ground is whose
    # NOTE: founders' shelves open with NO books — endowed goods cost the
    # walk (guess floors) until replacement lots (actually paid) take over.
    # Booking par as cost once froze gifts at par; gifts trade cheap.
    _pond = spec.get("pond", {})  # FISHERY: per-pond biology (K, r, stock)
    village.fish_K = float(_pond.get("K", 120))
    village.fish_r = float(_pond.get("r", 0.15))
    village.fish_stock = float(_pond.get("stock", 80))
    try:  # the well: every thirsty soul walks here
        _well = spec.get("well") or {}
        village.well_xy = (int(_well.get("x", 42)), int(_well.get("y", 22)))
    except Exception:
        village.well_xy = None
    village.say(f"Welcome to {village.name}. WASD/arrows to walk, . to wait, q to quit.")
    return village


def _work_posts(game_map, spawn) -> dict:  # type: ignore[no-untyped-def]
    """CHAIN: one honest post per producible good — where the work happens."""
    from omnisim.world import tile as T
    from omnisim.world.tile import BY_ID
    w, h = game_map.width, game_map.height

    def walk(x, y):
        return (game_map.in_bounds(x, y)
                and BY_ID[int(game_map.tiles[y, x])].walkable)

    # flood the spawn's walkable region: posts must be genuinely reachable
    region: set = set()
    stack = [tuple(spawn)]
    while stack:
        cx, cy = stack.pop()
        if (cx, cy) in region or not walk(cx, cy):
            continue
        region.add((cx, cy))
        stack.extend([(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)])

    def beside(tid, skip=()):
        for y in range(h):
            for x in range(w):
                if int(game_map.tiles[y, x]) != tid:
                    continue
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if walk(x + dx, y + dy) and (not region or (x + dx, y + dy) in region) \
                                and (x + dx, y + dy) not in skip:
                            return (x + dx, y + dy)
        return None

    def first_of(tid):
        for y in range(h):
            for x in range(w):
                if int(game_map.tiles[y, x]) == tid:
                    return (x, y)
        return None

    posts = {}
    farm = first_of(T.FARM)
    if farm:
        posts["wheat"] = {"at": farm, "skill": "Farming"}
    shore = beside(T.WATER)
    if shore:
        posts["fish"] = {"at": shore, "skill": "Fishing"}
    clearing = beside(T.TREE)
    if clearing:
        posts["firewood"] = {"at": clearing, "skill": "Woodcutting"}
    oven = beside(T.HEARTH)
    if oven:
        posts["bread"] = {"at": oven, "skill": "Cooking"}
    meadow = beside(T.GRASS)
    if meadow:
        posts["herbs"] = {"at": meadow, "skill": "Herbalism"}
    bank = beside(T.WATER, skip={shore} if shore else ())
    if bank:
        posts["clay"] = {"at": bank, "skill": "Pottery"}
        posts["pot"] = {"at": bank, "skill": "Pottery"}
    teeth = beside(T.WALL_STONE)
    if teeth:
        posts["iron_ore"] = {"at": teeth, "skill": "Mining"}
    try:  # STEP 10: drop trades with buildings staff posts (data rows only:
        # trade skill + its station's building footprint anchor the post)
        from omnisim.systems.production import trades as _ptrs
        try:
            import omnisim.systems.buildings as _bld10
            _bps10 = _bld10.blueprints()
        except Exception:
            _bps10 = {}
        for _t in _ptrs().values():
            _sk10 = str(_t.get("skill", "") or "")
            if not _sk10 or any((p or {}).get("skill") == _sk10 for p in posts.values()):
                continue
            _st10 = None
            for _rid in (_t.get("recipes", []) or []):
                try:
                    from omnisim.systems.production import recipes as _rr10
                    _st10 = (_rr10().get(_rid, {}) or {}).get("station")
                    if _st10:
                        break
                except Exception:
                    continue
            _at10 = None
            for _bp in _bps10.values():
                if _st10 and _st10 in ((_bp.get("builds", {}) or {}).get("stations", []) or []):
                    _at10 = beside(T.HEARTH)
                    break
            if _at10 is None:
                _at10 = meadow
            if _at10 is not None:
                _rep10 = None
                for _rid in (_t.get("recipes", []) or []):
                    try:
                        _outs10 = ((_rr10().get(_rid, {}) or {}).get("outputs", []) or [])
                        if _outs10 and _outs10[0].get("good"):
                            _rep10 = _outs10[0]["good"]
                            break
                    except Exception:
                        continue
                if _rep10:
                    posts[_rep10] = {"at": _at10, "skill": _sk10}
    except Exception:
        pass
    return posts


def render_ascii(v: Village, fov_only: bool = True) -> list[str]:
    """Headless render. fov_only: hide unseen (explored-memory as dim `'`'`
    for floors/roads, space for never-seen) — mirrors the tcod renderer."""
    rows: list[str] = []
    for y in range(v.game_map.height):
        line: list[str] = []
        for x in range(v.game_map.width):
            gm = v.game_map
            vis = bool(gm.visible[y, x]) if fov_only else True
            exp = bool(gm.explored[y, x]) if fov_only else True
            e = v.entity_at(x, y)
            if e is not None and (vis or e is v.player):
                line.append(e.glyph)
            elif vis:
                line.append(T.glyph_at(gm, x, y)[0])
            elif exp:
                line.append("`" if gm.tile_id(x, y) in (T.FLOOR, T.ROAD, T.FARM) else " ")
            else:
                line.append(" ")
        rows.append("".join(line))
    return rows












