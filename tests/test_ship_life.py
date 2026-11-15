import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

from pirates.game import World
from pirates.ship import LABORS, STATIONS, BUNKS, ShipLife
from omnisim.world import tile as T
from pirates.management import crew_menu, chores_menu, time_menu, profile_rows


def quiet_world():
    world = World()
    for c in world.ship.crew:
        c.hunger = c.thirst = c.fatigue = c.loneliness = c.boredom = 0
    return world


def disable_all(ship):
    for c in ship.crew:
        c.labors = {k: 0 for k in LABORS}


def supply_total(ship, resource):
    return ship.stores[resource] + sum(j.reserved.get(resource, 0) for j in ship.jobs) + sum(c.cargo.get(resource, 0) for c in ship.crew)


def test_ship_stations_and_bunks_reachable_through_doors():
    world = quiet_world()
    c = world.ship.crew[0]
    for key, target in STATIONS.items():
        assert world.ship.path(world, c, target) is not None, key
    for bunk in BUNKS:
        assert world.ship.path(world, c, bunk, exact=True) is not None
    assert len({c.bunk for c in world.ship.crew}) == 6
    assert not world.game_map.is_walkable(92, 25)


def test_simulation_and_visible_entities_are_not_decorative():
    world = World()
    starts = [(c.x, c.y) for c in world.ship.crew]
    assert len([e for e in world.entities() if e.get("role") in ("player", "crew")]) == 7
    assert len(world.economy.merchants()) == 3
    world.advance(60000)
    assert world.ship.hull == 100
    assert world.ship.bilge < 3
    assert world.ship.meals >= 6
    assert all(world.ship.ammo.values())
    assert all(v < 20 for v in world.ship.dirt.values())
    assert world.ship.rigging >= 90
    assert all((c.x, c.y) != at for c, at in zip(world.ship.crew, starts))
    assert sum(c.completed for c in world.ship.crew) >= 9
    assert world.ship.crew[1].hunger < 10
    assert world.ship.crew[2].thirst < 10
    assert world.ship.crew[4].fatigue < 20
    assert any("story" in thought for c in world.ship.crew for thought in c.thoughts)


def test_no_overlap_no_walking_through_walls_or_captain():
    world = World()
    before = [(c.x, c.y) for c in world.ship.crew]
    for _ in range(300):
        world.advance(100)
        positions = [(c.x, c.y) for c in world.ship.crew]
        assert len(set(positions + [(world.player.x, world.player.y)])) == 7
        assert all(world.game_map.is_walkable(*at) for at in positions)
        assert all(abs(x - bx) + abs(y - by) <= 1 for (x, y), (bx, by) in zip(positions, before))
        before = positions


def test_menu_time_is_frozen_and_cancelled_wait_costs_nothing():
    class Fake:
        def render_list(self, *args, **kwargs):
            pass
        def choose(self, count):
            return None
        def wait_any(self):
            pass
    world = World()
    snapshot = world.ship.snapshot()
    clock = world.clock.total_ticks
    crew_menu(Fake(), world)
    chores_menu(Fake(), world)
    time_menu(Fake(), world)
    assert world.ship.snapshot() == snapshot
    assert world.clock.total_ticks == clock
    assert "Personal want:" in " ".join(profile_rows(world.ship.crew[0]))


def test_fractional_ticks_and_chunking_produce_same_state():
    a, b = World(), World()
    a.advance(37)
    assert a.ship.seconds == 0 and a.ship.remainder == 37
    a.advance(9963)
    for _ in range(100):
        b.advance(100)
    assert a.ship.snapshot() == b.ship.snapshot()
    assert (a.game_map.doors == b.game_map.doors).all()


def test_jobs_exclusive_and_supplies_reserved_once():
    world = quiet_world()
    ship = world.ship
    before = {key: supply_total(ship, key) for key in ship.stores}
    world.advance(100)
    assigned = [c.job_id for c in ship.crew if c.job_id is not None]
    assert len(assigned) == len(set(assigned))
    for key, value in before.items():
        assert supply_total(ship, key) == value
    for c in ship.crew:
        if c.job_id is not None:
            assert ship.job(c.job_id).worker == c.id


def test_labor_off_and_personal_priority():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    world.advance(1000)
    assert all(c.job_id is None for c in ship.crew)
    c = ship.crew[0]
    c.labors["repair"] = 5
    c.labors["bail"] = 1  # equal board priority; personal preference wins
    ship.claim(world, c)
    assert ship.job(c.job_id).kind == "bail"
    ship.set_labor(c.id, "bail", 0)
    assert c.job_id is None
    with pytest.raises(ValueError):
        ship.set_labor(c.id, "repair", 6)


def test_missing_supplies_block_without_conjuring_work():
    world = quiet_world()
    ship = world.ship
    ship.stores["planks"] = 0
    world.advance(18000)
    assert ship.hull == 72
    repair = next(j for j in ship.jobs if j.kind == "repair")
    assert repair.state == "blocked" and "planks" in repair.reason
    ship.stores["planks"] = 2
    world.advance(18000)
    assert ship.hull == 100
    assert supply_total(ship, "planks") == 0


def test_need_preempts_and_resumes_claim_with_progress():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    c = ship.crew[0]
    c.labors["repair"] = 1
    for _ in range(120):
        world.advance(100)
        job = ship.job(c.job_id)
        if job and job.progress > 0:
            break
    assert job.progress > 0 and c.cargo == {"planks": 1}
    progress = job.progress
    c.thirst = 90
    world.advance(100)
    assert job.state == "paused" and c.job_id == job.id
    assert job.progress == progress and c.cargo == {"planks": 1}
    world.advance(9000)
    assert c.thirst < 25
    assert job.state == "done"


def test_cancel_reserved_and_carried_supplies_are_not_lost_or_teleported():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    c = ship.crew[0]
    c.labors["repair"] = 1
    before = supply_total(ship, "planks")
    ship.claim(world, c)
    first = ship.job(c.job_id)
    assert first.reserved == {"planks": 1}
    ship.cancel(first.id)
    assert first.state == "cancelled" and supply_total(ship, "planks") == before
    ship.cooldowns.clear()
    ship.generate_jobs()
    ship.claim(world, c)
    second = ship.job(c.job_id)
    for _ in range(100):
        world.advance(100)
        if c.cargo:
            break
    assert c.cargo == {"planks": 1}
    hold_amount = ship.stores["planks"]
    ship.cancel(second.id)
    assert second.state == "returning"
    assert ship.stores["planks"] == hold_amount  # bundle is still on sailor
    world.advance(3000)
    assert second.state == "cancelled"
    assert supply_total(ship, "planks") == before
    assert not c.cargo


def test_unreachable_site_reports_blockage_then_retries():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    c = ship.crew[0]
    c.labors["repair"] = 1
    x, y = STATIONS["repair"]
    old = int(world.game_map.tiles[y, x - 1])
    world.game_map.tiles[y, x - 1] = T.WALL_WOOD
    world.game_map.tiles[y - 1, x] = T.WALL_WOOD
    world.game_map.tiles[y + 1, x] = T.WALL_WOOD
    world.advance(100)
    repair = next(j for j in ship.jobs if j.kind == "repair")
    assert repair.state == "blocked" and "route" in repair.reason
    assert not repair.reserved
    world.game_map.tiles[y, x - 1] = old
    world.advance(100)
    assert repair.worker == c.id


def test_food_water_and_sleep_are_finite_and_location_bound():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    c = ship.crew[0]
    c.hunger = 90
    before = ship.meals
    world.advance(100)
    assert c.hunger >= 90 and ship.meals == before  # has not reached mess
    world.advance(3000)
    assert c.hunger < 25 and ship.meals == before - 1
    c.thirst = 90
    water = ship.stores["water"]
    world.advance(3000)
    assert c.thirst < 25 and ship.stores["water"] == water - 1
    c.fatigue = 75
    c.sleep_debt = 75  # Physical fatigue alone now calls for a short rest.
    world.advance(18000)
    assert c.fatigue < 20
    assert "Slept in my own bunk." in c.thoughts


def test_wants_and_social_relationships_change_morale():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    a, b = ship.crew[:2]
    a.x, a.y = 72, 28
    b.x, b.y = 72, 27
    a.loneliness = 80
    b.boredom = 0
    before = a.morale
    world.advance(2000)
    assert a.loneliness < 60
    assert a.relationships.get(b.id, 0) >= 1
    assert b.relationships.get(a.id, 0) >= 1
    assert a.morale > before
    a.loneliness = 0
    a.boredom = 80
    a.intent = ""
    world.advance(3000)
    assert a.boredom < 40
    assert any("quiet moment" in t for t in a.thoughts)


def test_starvation_has_health_and_morale_consequences():
    world = quiet_world()
    ship = world.ship
    disable_all(ship)
    ship.meals = ship.stores["water"] = 0
    c = ship.crew[0]
    c.thirst = c.hunger = 99
    world.advance(10000)
    assert c.health < 100 and c.morale < 65
    assert c.completed == 0


@pytest.mark.parametrize("elapsed", [0, 1, 20, 65, 180, 600])
def test_mid_simulation_save_resume_is_deterministic(tmp_path, elapsed):
    a = World()
    a.advance(elapsed * 100 + 37)
    path = tmp_path / "ship.json"
    a.save(path)
    b = World.load(path)
    # JSON converts RGB/coordinates tuples to lists; snapshot serialisation is canonical.
    assert json.dumps(a.ship.snapshot(), sort_keys=True) == json.dumps(b.ship.snapshot(), sort_keys=True)
    a.advance(2000)
    b.advance(2000)
    assert json.dumps(a.ship.snapshot(), sort_keys=True) == json.dumps(b.ship.snapshot(), sort_keys=True)


@pytest.mark.parametrize("corruption", ["overlap", "need", "supply", "orphan", "labor", "target", "duplicate", "nan"])
def test_bad_ship_save_rejected_without_mutating_world(tmp_path, corruption):
    world = quiet_world()
    world.advance(100)
    before = deepcopy(world.ship.snapshot())
    path = tmp_path / "bad.json"
    world.save(path)
    data = json.loads(path.read_text())
    state = data["ship"]
    if corruption == "overlap":
        state["crew"][0]["x"], state["crew"][0]["y"] = data["player"]
    elif corruption == "need":
        state["crew"][0]["hunger"] = -1
    elif corruption == "supply":
        state["stores"]["water"] = -1
    elif corruption == "orphan":
        state["crew"][0]["job_id"] = 9999
    elif corruption == "labor":
        state["crew"][0]["labors"]["repair"] = 99
    elif corruption == "target":
        state["jobs"][0]["target"] = [0, 0]
    elif corruption == "duplicate":
        state["jobs"].append(deepcopy(state["jobs"][0]))
    elif corruption == "nan":
        state["hull"] = float("nan")
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        World.load(path)
    assert world.ship.snapshot() == before


def test_legacy_v1_slot_adds_crew_without_erasing_inventory(tmp_path):
    world = World()
    world.player.x, world.player.y = 30, 23
    world.move(1, 0)
    world.pickup()
    path = tmp_path / "old.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["version"] = 1
    data.pop("ship")
    path.write_text(json.dumps(data))
    restored = World.load(path)
    assert restored.player.x == 31 and len(restored.inventory) == 2
    assert len(restored.ship.crew) == 6


def test_legacy_deck_positions_displaced_by_new_fixtures_migrate(tmp_path):
    world = World()
    path = tmp_path / "old.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["version"] = 1
    data["player"] = [74, 30]  # old bare deck, now a non-walkable water cask
    data.pop("ship")
    data["ground"]["74,30"] = [{"id": "test", "name": "Kept cargo", "weight_g": 1, "bulk_ml": 1, "quantity": 2}]
    path.write_text(json.dumps(data))
    restored = World.load(path)
    assert restored.game_map.is_walkable(restored.player.x, restored.player.y)
    assert any(i["name"] == "Kept cargo" for items in restored.ground_items.values() for i in items)


def test_management_orders_set_labors_and_cancel_without_time_cost():
    class Scripted:
        def __init__(self, answers):
            self.answers = iter(answers)
        def render_list(self, *args, **kwargs):
            pass
        def choose(self, count):
            return next(self.answers)
    world = World()
    before = world.clock.total_ticks
    crew_menu(Scripted([0, 1, 0, None, None]), world)
    assert world.ship.crew[0].labors["repair"] == 0
    job = sorted(world.ship.jobs, key=lambda j: (j.priority, j.id))[0]
    chores_menu(Scripted([0, 1, 4, None]), world)
    assert job.priority == 5
    target = sorted((j for j in world.ship.jobs if j.state not in ("done", "cancelled")), key=lambda j: (j.priority, j.id))[0]
    chores_menu(Scripted([0, 2, None]), world)
    assert target.state == "cancelled"
    assert world.clock.total_ticks == before


def test_observation_escape_stops_and_quit_is_forwarded(monkeypatch):
    import tcod.event
    from collections import deque
    from pirates.management import observe
    from omnisim.ui.ascii_renderer import MovementRepeater
    class Fake:
        _move_repeater = MovementRepeater()
        _pending_events = deque()
        def _finish_motion_cache(self):
            pass
        def render(self, world):
            pass
    world = World()
    renderer = Fake()
    event = tcod.event.KeyDown(scancode=tcod.event.Scancode.ESCAPE, sym=tcod.event.KeySym.ESCAPE, mod=0)
    monkeypatch.setattr(tcod.event, "wait", lambda timeout: [event])
    observe(renderer, world, seconds=60)
    assert world.ship.seconds == 1
    assert not renderer._pending_events
    event = tcod.event.Quit()
    observe(renderer, world, seconds=60)
    assert world.ship.seconds == 2
    assert list(renderer._pending_events) == [event]


def test_returning_cancelled_job_can_be_saved_and_resumed(tmp_path):
    world = quiet_world()
    disable_all(world.ship)
    c = world.ship.crew[0]
    c.labors["repair"] = 1
    for _ in range(100):
        world.advance(100)
        if c.cargo:
            break
    job = world.ship.job(c.job_id)
    before = supply_total(world.ship, "planks")
    world.ship.cancel(job.id)
    path = tmp_path / "returning.json"
    world.save(path)
    restored = World.load(path)
    restored.advance(3000)
    assert restored.ship.job(job.id).state == "cancelled"
    assert supply_total(restored.ship, "planks") == before
