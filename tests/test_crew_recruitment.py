import json

import pytest

from pirates.crew_recruitment import CANDIDATES, RecruitmentPool
from pirates.game import World
from pirates.management import profile_rows
from pirates.management import crew_menu
from pirates.trading_ui import port_menu


def test_port_candidate_roster_is_finite_and_does_not_use_captive_ledger():
    pool = RecruitmentPool()
    verdant = pool.at_port("verdant")
    cinder = pool.at_port("cinder")
    assert {row["id"] for row in verdant} == {"pippin_ash", "elira_voss"}
    assert {row["id"] for row in cinder} == {"brannoc_reed"}
    assert all(row["asking_price"] > 0 for row in verdant + cinder)
    # The recruitment source is its own fixed roster, not the NPC captive market.
    world = World()
    captive_count = world.npcs.market["enslaved_people"]
    pool.mark_recruited("pippin_ash", "verdant")
    assert world.npcs.market["enslaved_people"] == captive_count
    assert pool.at_port("verdant")[0]["id"] == "elira_voss"


def test_recruitment_pool_rejects_wrong_port_repeated_and_unknown_transfers():
    pool = RecruitmentPool()
    with pytest.raises(ValueError, match="no longer available"):
        pool.mark_recruited("brannoc_reed", "verdant")
    pool.mark_recruited("brannoc_reed", "cinder")
    with pytest.raises(ValueError, match="no longer available"):
        pool.mark_recruited("brannoc_reed", "cinder")
    with pytest.raises(ValueError, match="no longer available"):
        pool.mark_recruited("missing", "verdant")


def test_recruitment_pool_save_roundtrip_and_old_snapshot_migration(tmp_path):
    world = World()
    world.economy.recruitment.decline("pippin_ash", "verdant")
    path = tmp_path / "crew-pool.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.economy.recruitment.snapshot() == world.economy.recruitment.snapshot()

    data = json.loads(path.read_text(encoding="utf-8"))
    del data["economy"]["recruitment"]
    legacy_path = tmp_path / "old-crew-pool.json"
    legacy_path.write_text(json.dumps(data), encoding="utf-8")
    migrated = World.load(legacy_path)
    assert {row["id"] for row in migrated.economy.recruitment.at_port("verdant")} == {
        "pippin_ash", "elira_voss"}


def test_hire_transfers_a_named_candidate_to_ship_with_a_persisted_hammock(tmp_path):
    world = World()
    world.player.x, world.player.y, world.player.z = 32, 22, 0
    starting_money = world.economy.money
    sailor = world.economy.recruit_candidate(world, "pippin_ash")
    assert sailor.id == "hire:pippin_ash"
    assert sailor.age == 16 and sailor.learning_potential == 92
    assert len(world.ship.crew) == 7
    assert len(world.fixtures.bunks) == 7
    assert world.fixtures.get("bunk:6") is not None
    assert world.economy.money == starting_money - 35
    assert world.economy.recruitment.candidate("pippin_ash") is None
    assert world.npcs.market["enslaved_people"] == 5
    assert any("Age 16" in row and "exceptional" in row for row in profile_rows(sailor))
    assert sailor.mental == CANDIDATES["pippin_ash"]["mental"]
    assert any("Intelligence 70" in row and "Discipline 90" in row for row in profile_rows(sailor))

    path = tmp_path / "recruited.json"
    world.save(path)
    loaded = World.load(path)
    recruit = loaded.ship.sailor("hire:pippin_ash")
    assert recruit.name == "Pip Ash" and recruit.bunk == 6
    assert recruit.mental == CANDIDATES["pippin_ash"]["mental"]
    assert len(loaded.fixtures.bunks) == 7
    assert loaded.economy.recruitment.statuses["pippin_ash"] == "recruited"


def test_legacy_sailor_rows_migrate_mental_attributes_from_canonical_identity(tmp_path):
    world = World()
    world.player.x, world.player.y, world.player.z = 32, 22, 0
    world.economy.recruit_candidate(world, "pippin_ash")
    path = tmp_path / "legacy-no-mental.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = {row["id"]: row["mental"] for row in data["ship"]["crew"]}
    for row in data["ship"]["crew"]:
        row.pop("mental")
    path.write_text(json.dumps(data), encoding="utf-8")

    migrated = World.load(path)
    assert {s.id: s.mental for s in migrated.ship.crew} == expected


def test_recruitment_failure_does_not_charge_or_consume_candidate():
    world = World()
    starting_money = world.economy.money
    with pytest.raises(ValueError, match="while ashore"):
        world.economy.recruit_candidate(world, "pippin_ash")
    assert world.economy.money == starting_money
    assert world.economy.recruitment.candidate("pippin_ash") is not None


def test_port_menu_can_review_and_accept_a_finite_crew_prospect():
    class Scripted:
        def __init__(self):
            self.answers = iter((2, 0, 0))
            self.titles = []

        def render_list(self, _world, title, *_args, **_kwargs):
            self.titles.append(title)

        def choose(self, _count):
            return next(self.answers)

        def wait_any(self):
            pass

    world = World()
    world.player.x, world.player.y, world.player.z = 32, 22, 0
    renderer = Scripted()
    port_menu(renderer, world)
    assert world.ship.sailor("hire:pippin_ash") is not None
    assert any("Crew prospect" in title for title in renderer.titles)


def test_releasing_crew_records_former_status_and_preserves_save_identity(tmp_path):
    world = World()
    world.player.x, world.player.y, world.player.z = 32, 22, 0
    sailor = world.economy.recruit_candidate(world, "pippin_ash")
    record = world.ship.release_crew_member(world, sailor.id)
    assert record["status"] == "released"
    assert record["id"] not in {member.id for member in world.ship.crew}
    assert world.ship.sailor("hire:pippin_ash") is None
    assert all("hire:pippin_ash" not in member.relationships for member in world.ship.crew)
    assert all(assigned != "hire:pippin_ash" for assigned in world.voyages.roles.values())

    path = tmp_path / "released-recruit.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.ship.crew_history == [record]
    assert loaded.economy.recruitment.statuses["pippin_ash"] == "recruited"


def test_zero_active_crew_is_a_valid_persisted_roster_and_vacant_voyage(tmp_path):
    world = World()
    world.player.x, world.player.y, world.player.z = 32, 22, 0
    for sailor in list(world.ship.crew):
        # This fixture has no active job claims; release is an explicit port action.
        world.ship.release_crew_member(world, sailor.id)
    assert not world.ship.crew
    assert len(world.ship.crew_history) == 6
    plan = world.voyages.plan(world, "shoals")
    assert plan["navigator"] == plan["quartermaster"] == plan["purser"] == "Unassigned"
    path = tmp_path / "crewless.json"
    world.save(path)
    loaded = World.load(path)
    assert not loaded.ship.crew and len(loaded.ship.crew_history) == 6
    loaded.voyages.plan(loaded, "shoals")


def test_former_crew_ledger_rejects_unknown_or_duplicate_people(tmp_path):
    world = World()
    world.player.x, world.player.y, world.player.z = 32, 22, 0
    world.ship.release_crew_member(world, "mara")
    path = tmp_path / "former-crew.json"
    world.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["ship"]["crew_history"][0]["id"] = "intruder"
    invalid = tmp_path / "invalid-former-crew.json"
    invalid.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="former roster"):
        World.load(invalid)


def test_release_is_port_only_and_requires_work_supplies_returned():
    world = World()
    sailor = world.ship.crew[0]
    with pytest.raises(ValueError, match="in port"):
        world.ship.release_crew_member(world, sailor.id)
    world.player.x, world.player.y = 32, 22
    sailor.cargo["water"] = 1
    with pytest.raises(ValueError, match="carried supplies"):
        world.ship.release_crew_member(world, sailor.id)
    assert world.ship.sailor(sailor.id) is sailor
    assert not world.ship.crew_history


def test_former_crew_history_is_visible_in_crew_menu():
    class Scripted:
        def __init__(self):
            self.answers = iter((5, None))
            self.titles = []

        def render_list(self, _world, title, *_args, **_kwargs):
            self.titles.append(title)

        def choose(self, _count):
            return next(self.answers)

        def wait_any(self):
            pass

    world = World()
    world.player.x, world.player.y = 32, 22
    world.ship.release_crew_member(world, "mara")
    crew_menu(Scripted(), world)


def test_crew_profile_menu_can_confirm_release_at_port():
    class Scripted:
        def __init__(self):
            self.answers = iter((0, 7, 0, None))

        def render_list(self, *_args, **_kwargs):
            pass

        def choose(self, _count):
            return next(self.answers)

        def wait_any(self):
            pass

    world = World()
    world.player.x, world.player.y = 32, 22
    crew_menu(Scripted(), world)
    assert world.ship.sailor("mara") is None
    assert world.ship.crew_history[0]["status"] == "released"


@pytest.mark.parametrize("data", [
    {},
    {"version": True, "statuses": {key: "available" for key in CANDIDATES}},
    {"version": 1, "statuses": {**{key: "available" for key in CANDIDATES}, "intruder": "available"}},
    {"version": 1, "statuses": {**{key: "available" for key in CANDIDATES}, "pippin_ash": "dead"}},
])
def test_invalid_recruitment_pool_snapshot_is_rejected(data):
    with pytest.raises(ValueError, match="Invalid recruitment pool"):
        RecruitmentPool.restore(data)
