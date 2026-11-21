from copy import deepcopy

import pytest

from pirates import sea_encounter, sea_trade
from pirates.game import World
from pirates.management import crossing_request_rows, voyage_menu
from pirates.ship import LABORS
from pirates import sea_transfer
from pirates.voyage import VoyageSystem


def quiet_world():
    world = World()
    for sailor in world.ship.crew:
        sailor.labors = {kind: 0 for kind in LABORS}
        sailor.hunger = sailor.thirst = sailor.fatigue = 0
        sailor.loneliness = sailor.boredom = 0
        sailor.sleep_debt = 20
    world.npcs.sighted.update(ship["id"] for ship in world.npcs.ships)
    return world


def encounter_world(ship_id="government_1", *, close=False):
    world = quiet_world()
    target = deepcopy(next(ship for ship in world.npcs.ships if ship["id"] == ship_id))
    world.voyages.confirm(world, "deepwater")
    world.voyages.voyage["status"] = "encounter"
    world.voyages.voyage["encounter"] = target
    world.enter_sea()
    if close:
        sea_encounter.contact_of(target)["range"] = "close"
    return world


def synthetic_encounter(encounter_id, threat="merchant"):
    return {"id": encounter_id, "name": f"Vessel {encounter_id}", "faction": "Test Flag",
            "threat": threat, "crew": 5, "condition": 80, "destination": "cinder",
            "mission": "trade", "node": "shoals"}


def find_id(predicate, threat="merchant", limit=2000):
    for index in range(limit):
        encounter = synthetic_encounter(f"probe_{index}", threat)
        if predicate(encounter):
            return encounter
    raise AssertionError("No deterministic encounter id satisfied the probe")


def test_contact_defaults_are_distant_and_read_only():
    world = encounter_world()
    encounter = world.voyages.voyage["encounter"]
    assert "contact" not in encounter
    contact = sea_encounter.contact_of(encounter)
    assert contact["range"] == "distant" and contact["signals"] == []
    assert contact["parley"] is None and contact["crossing"] is None

    before = world.voyages.snapshot()
    rows = sea_encounter.sighting_rows(world, encounter) + sea_encounter.contact_rows(world, encounter)
    assert world.voyages.snapshot() == before
    assert any("Lookout confidence: vessel contact is consistently tracked" in row for row in rows)
    assert any("no verified organization match" in row for row in rows)
    assert any("No captain-to-faction stance" in row for row in rows)


def test_verified_faction_identity_is_read_from_the_shared_registry_only():
    world = encounter_world()
    encounter = world.voyages.voyage["encounter"]
    world.society.organizations["test_org"] = {
        "name": "Test Marine Command", "kind": "naval_command", "reputation": 61,
    }
    encounter["organization_id"] = "test_org"
    before = deepcopy(world.society.organizations)
    rows = sea_encounter.sighting_rows(world, encounter)
    assert any("Registry match: Test Marine Command" in row and "61/100" in row for row in rows)
    assert world.society.organizations == before


def test_signal_costs_one_minute_is_bounded_and_does_not_touch_other_ledgers():
    world = encounter_world()
    encounter = world.voyages.voyage["encounter"]
    ticks = world.clock.total_ticks
    seconds = world.ship.seconds
    money, cargo = world.economy.money, deepcopy(world.economy.cargo)

    record = world.voyages.signal_sighting(world, "identify")
    assert "Government marine patrol" in record["reply"]
    assert world.clock.total_ticks == ticks + 6000
    assert world.ship.seconds == seconds + 60
    assert world.economy.money == money and world.economy.cargo == cargo
    assert world.npcs.player_contacts == {}

    with pytest.raises(ValueError, match="already been exchanged"):
        world.voyages.signal_sighting(world, "identify")
    assert world.clock.total_ticks == ticks + 6000


def test_signal_records_deterministic_no_answer_and_refusal_without_hidden_mission_leak():
    world = encounter_world()
    no_answer = deepcopy(next(ship for ship in world.npcs.ships
                              if sea_encounter._unit(ship, "signal:identify")
                              >= {"merchant": 0.72, "marines": 0.82, "pirates": 0.62}[ship["threat"]]))
    no_answer["mission"] = "secret_target_island"
    world.voyages.voyage["encounter"] = no_answer
    ticks = world.clock.total_ticks
    record = world.voyages.signal_sighting(world, "identify")
    assert record["outcome"] == "no_answer"
    assert "No reply" in record["reply"]
    assert "secret_target_island" not in record["reply"]
    assert world.clock.total_ticks == ticks + 6000
    assert world.npcs.player_contacts == {}

    refused = find_id(lambda encounter:
                      sea_encounter._unit(encounter, "signal:warn_off") < 0.62,
                      threat="pirates")
    refused["contact"] = sea_encounter.new_contact()
    row = sea_encounter.signal(refused, 5, "warn_off")
    assert row["outcome"] == "refused" and "refuses the warning" in row["reply"]
    assert sea_encounter.validate_contact(refused["contact"]) == refused["contact"]

    close = synthetic_encounter("signal_close")
    close["contact"] = sea_encounter.new_contact()
    close["contact"]["range"] = "close"
    with pytest.raises(ValueError, match="require a distant contact"):
        sea_encounter.signal(close, 1, "identify")

    private = synthetic_encounter("mission_privacy")
    private["mission"] = "secret_target_island"
    private["contact"] = sea_encounter.new_contact()
    intention = sea_encounter.signal(private, 1, "intentions")
    assert "secret_target_island" not in intention["reply"]


def test_sighting_source_and_age_are_reported_without_private_mission_or_cargo():
    world = encounter_world()
    encounter = world.voyages.voyage["encounter"]
    encounter["mission"] = "secret_target_island"
    encounter["cargo"] = {"gold": 999}
    world.npcs.sighting_reports[encounter["id"]] = {
        "node": encounter["node"], "observed_hour": 2,
        "crew": encounter["crew"], "condition": encounter["condition"],
    }
    world.clock.total_ticks = 5 * 360000
    rows = sea_encounter.sighting_rows(world, encounter)
    assert any("(3h old)" in row and "current society record" in row for row in rows)
    assert any("consistently tracked" in row for row in rows)
    assert all("secret_target_island" not in row and "999" not in row for row in rows)


def test_approach_closes_range_without_moving_anyone_and_failure_keeps_distance():
    world = encounter_world("raider_1")
    encounter = world.voyages.voyage["encounter"]
    positions = [world.player.x, world.player.y, world.player.z]
    crew = [(sailor.id, sailor.x, sailor.y, sailor.z, sailor.health) for sailor in world.ship.crew]
    ticks = world.clock.total_ticks
    seconds = world.ship.seconds

    result = world.voyages.approach_sighting(world)
    assert result["accepted"] is True
    assert encounter["contact"]["range"] == "close"
    assert world.clock.total_ticks == ticks + 30000
    assert world.ship.seconds == seconds + 300
    assert [world.player.x, world.player.y, world.player.z] == positions
    assert [(s.id, s.x, s.y, s.z, s.health) for s in world.ship.crew] == crew

    refused = find_id(
        lambda encounter: sea_encounter._unit(encounter, "approach:0") < 0.12)
    outcome = sea_encounter.approach(refused, 1)
    assert outcome["accepted"] is False
    assert refused["contact"]["range"] == "distant"
    assert refused["contact"]["approach_attempts"] == 1

    with pytest.raises(ValueError, match="already at close range"):
        sea_encounter.approach(world.voyages.voyage["encounter"], 2)

    exhausted = synthetic_encounter("approach_exhausted")
    exhausted["contact"] = sea_encounter.new_contact()
    exhausted["contact"]["approach_attempts"] = 100
    with pytest.raises(ValueError, match="No further approach attempts"):
        sea_encounter.approach(exhausted, 3)


def test_evade_is_deterministic_and_a_failure_worsens_a_distant_contact():
    world = encounter_world()
    failed = find_id(lambda encounter: sea_encounter._unit(encounter, "evade:0") >= 0.85)
    outcome = sea_encounter.evade(world, failed, 0)
    assert outcome["escaped"] is False
    assert failed["contact"]["range"] == "close"

    escaped = find_id(lambda encounter: sea_encounter._unit(encounter, "evade:0") < 0.15)
    outcome = sea_encounter.evade(world, escaped, 0)
    assert outcome["escaped"] is True
    assert escaped["contact"]["range"] == "distant"

    encounter = world.voyages.voyage["encounter"]
    ticks = world.clock.total_ticks
    seconds = world.ship.seconds
    result = world.voyages.evade_sighting(world)
    assert world.clock.total_ticks == ticks + 30000
    assert world.ship.seconds == seconds + 300
    if result["escaped"]:
        assert world.voyages.voyage["status"] == "underway"
        assert world.voyages.voyage["encounter"] is None
    else:
        assert world.voyages.voyage["status"] == "encounter"

    exhausted = synthetic_encounter("evade_exhausted")
    exhausted["contact"] = sea_encounter.new_contact()
    exhausted["contact"]["evade_attempts"] = 100
    with pytest.raises(ValueError, match="No further evasion attempts"):
        sea_encounter.evade(world, exhausted, 3)


def test_same_encounter_actions_match_single_and_one_second_time_advances():
    bulk = encounter_world()
    stepped = encounter_world()
    bulk_result = bulk.voyages.signal_sighting(bulk, "identify")
    step_result = stepped.voyages.signal_sighting(stepped, "identify")
    assert bulk_result == step_result
    for world, chunks in ((bulk, [30000]), (stepped, [100] * 300)):
        world.voyages._action_advance_depth += 1
        try:
            for ticks in chunks:
                world.advance(ticks)
        finally:
            world.voyages._action_advance_depth -= 1
    assert bulk.clock.total_ticks == stepped.clock.total_ticks
    assert bulk.ship.seconds == stepped.ship.seconds
    assert bulk.voyages.voyage["encounter"]["contact"] == \
        stepped.voyages.voyage["encounter"]["contact"]
    assert bulk.ship.hull == stepped.ship.hull
    assert bulk.ship.rigging == stepped.ship.rigging
    assert bulk.economy.cargo == stepped.economy.cargo


def test_parley_requires_close_range_refuses_pirates_and_expires_with_the_contact():
    distant = find_id(lambda encounter: True)
    with pytest.raises(ValueError, match="requires close range"):
        sea_encounter.parley(distant, "identity")

    pirate = synthetic_encounter("pirate_probe", threat="pirates")
    pirate["contact"] = sea_encounter.new_contact()
    pirate["contact"]["range"] = "close"
    with pytest.raises(ValueError, match="refuses parley"):
        sea_encounter.parley(pirate, "identity")

    granted = find_id(
        lambda encounter: sea_encounter._consent_chance(
            encounter, {"parley": None}, "transfer_permission")
        > sea_encounter._unit(encounter, "parley:transfer_permission"))
    granted["contact"] = sea_encounter.new_contact()
    granted["contact"]["range"] = "close"
    assert sea_encounter.parley(granted, "identity")["agreed"] is True
    assert sea_encounter.parley(granted, "transfer_permission")["agreed"] is True
    assert granted["contact"]["parley"]["transfer_permission"] is True
    with pytest.raises(ValueError, match="already been raised"):
        sea_encounter.parley(granted, "identity")


def test_local_pirate_risk_uses_observed_facts_and_weaker_crew_can_back_down():
    world = encounter_world("raider_1", close=True)
    weak = synthetic_encounter("raider_weak", threat="pirates")
    weak.update({"crew": 2, "condition": 25, "contact_instance_id": "raider_weak:1",
                 "contact": sea_encounter.new_contact()})
    weak["contact"]["range"] = "close"
    strong = synthetic_encounter("raider_strong", threat="pirates")
    strong.update({"crew": 20, "condition": 100,
                   "contact_instance_id": "raider_strong:1",
                   "contact": sea_encounter.new_contact()})
    strong["contact"]["range"] = "close"

    weak_risk = sea_encounter.pirate_risk_assessment(world, weak)
    strong_risk = sea_encounter.pirate_risk_assessment(world, strong)
    assert weak_risk["warn_off_chance"] > strong_risk["warn_off_chance"]
    assert "cargo" not in weak_risk and "mission" not in weak_risk
    assert not sea_encounter.hostile_commitment_authorized(world, weak)

    weak["contact_instance_id"] = next(
        f"raider_weak:{sequence}" for sequence in range(1, 1000)
        if sea_encounter._contact_unit(
            {**weak, "contact_instance_id": f"raider_weak:{sequence}"},
            "pirate_fear:warn_off") < weak_risk["warn_off_chance"])
    accepted = sea_encounter.parley(weak, "warn_off", world=world)
    assert accepted["agreed"] is True
    assert "backs down" in accepted["text"]
    assert weak["contact"]["range"] == "close"
    assert weak["contact"]["parley"]["warned_off"] is True
    sea_encounter.validate_sighting(weak)

    strong["contact_instance_id"] = next(
        f"raider_strong:{sequence}" for sequence in range(1, 1000)
        if sea_encounter._contact_unit(
            {**strong, "contact_instance_id": f"raider_strong:{sequence}"},
            "pirate_fear:warn_off") >= strong_risk["warn_off_chance"])
    refused = sea_encounter.parley(strong, "warn_off", world=world)
    assert refused["agreed"] is False
    assert strong["contact"]["range"] == "close"

    risk_rows = sea_encounter.sighting_rows(world, weak)
    assert any("Local risk:" in row and "observed crew/condition" in row for row in risk_rows)
    assert any("Hostility: prohibited" in row for row in risk_rows)


def test_pirate_parley_ui_shows_bounded_risk_and_resolves_warning_locally():
    class Scripted:
        def __init__(self, answers):
            self.answers = iter(answers)
            self.frames = []

        def render_list(self, *_args, **_kwargs):
            self.frames.append(_args[1:] if len(_args) > 1 else [])

        def choose(self, _count):
            return next(self.answers)

        def wait_any(self):
            pass

    world = encounter_world("raider_1", close=True)
    start_ticks = world.clock.total_ticks
    renderer = Scripted([6, 1, 0, None])
    voyage_menu(renderer, world)
    encounter = world.voyages.voyage["encounter"]
    assert world.clock.total_ticks == start_ticks + 6000
    assert encounter["contact"]["parley"]["topics"] == ["warn_off"]
    assert any("Visible-readiness warning estimate" in " ".join(map(str, frame))
               for frame in renderer.frames)
    assert any(("backs down" in " ".join(map(str, frame))
                or "refuses the warning" in " ".join(map(str, frame)))
               for frame in renderer.frames)
    assert not sea_encounter.hostile_commitment_authorized(world, encounter)


def test_close_parley_has_hostile_willing_friendly_refusing_and_warn_off_cases():
    hostile = find_id(
        lambda encounter: sea_encounter._unit(encounter, "parley:stand_down")
        < sea_encounter._consent_chance(encounter, {"parley": None}, "stand_down"),
        threat="marines")
    hostile["contact"] = sea_encounter.new_contact()
    hostile["contact"]["range"] = "close"
    assert sea_encounter.parley(hostile, "stand_down")["agreed"] is True

    refusing = find_id(
        lambda encounter: sea_encounter._unit(encounter, "parley:transfer_permission")
        >= sea_encounter._consent_chance(encounter, {"parley": None}, "transfer_permission"),
        threat="merchant")
    refusing["contact"] = sea_encounter.new_contact()
    refusing["contact"]["range"] = "close"
    sea_encounter.parley(refusing, "identity")
    refused = sea_encounter.parley(refusing, "transfer_permission")
    assert refused["agreed"] is False
    assert refusing["contact"]["parley"]["transfer_permission"] is False

    accepted_warn_off = find_id(
        lambda encounter: sea_encounter._unit(encounter, "parley:warn_off")
        < sea_encounter._consent_chance(encounter, {"parley": None}, "warn_off"),
        threat="merchant")
    accepted_warn_off["contact"] = sea_encounter.new_contact()
    accepted_warn_off["contact"]["range"] = "close"
    assert sea_encounter.parley(accepted_warn_off, "warn_off")["agreed"] is True
    crossing_world = encounter_world(close=True)
    with pytest.raises(ValueError, match="accepted your warn-off"):
        sea_encounter.request_crossing(
            crossing_world, accepted_warn_off, 0, "alone", ["captain"])


def test_transfer_permission_requires_identity_and_a_crossing_ends_with_the_contact():
    encounter = synthetic_encounter("permission_probe")
    encounter["contact"] = sea_encounter.new_contact()
    encounter["contact"]["range"] = "close"
    with pytest.raises(ValueError, match="Exchange identities"):
        sea_encounter.parley(encounter, "transfer_permission")

    world = encounter_world(close=True)
    live = world.voyages.voyage["encounter"]
    world.voyages.continue_after_sighting()
    assert world.voyages.voyage["encounter"] is None
    assert world.npcs.player_contacts == {}
    assert live["contact"]["range"] == "close"  # only the discarded sighting copy changed


def test_manifest_validates_real_living_crew_and_every_crossing_mode():
    world = encounter_world(close=True)
    living = [sailor.id for sailor in world.ship.crew if sailor.health > 0]
    assert sea_encounter.manifest(world, "alone") == [world.player.id]
    assert sea_encounter.manifest(world, "everyone") == [world.player.id, *living]
    assert sea_encounter.manifest(world, "escorts", living[:2]) == [world.player.id, *living[:2]]

    with pytest.raises(ValueError, match="at least one escort"):
        sea_encounter.manifest(world, "escorts", [])
    with pytest.raises(ValueError, match="named twice"):
        sea_encounter.manifest(world, "escorts", [living[0], living[0]])
    with pytest.raises(ValueError, match="living crew"):
        sea_encounter.manifest(world, "escorts", ["not_a_sailor"])
    with pytest.raises(ValueError, match="does not name individual escorts"):
        sea_encounter.manifest(world, "everyone", living[:1])


def test_consented_crossing_rejects_party_larger_than_receiving_deck():
    world = encounter_world(close=True)
    encounter = world.voyages.voyage["encounter"]
    encounter["contact"]["parley"] = {
        "topics": ["identity", "transfer_permission"],
        "stand_down": False, "transfer_permission": True, "warned_off": False,
    }
    participants = [world.player.id] + [sailor.id for sailor in world.ship.crew
                                         if sailor.health > 0]
    before = deepcopy(encounter["contact"])
    ticks = world.clock.total_ticks
    with pytest.raises(ValueError, match="at most 6 people"):
        world.voyages.request_crossing(world, "everyone")
    assert encounter["contact"] == before
    assert world.voyages.voyage["deck_visit"] is None
    assert world.clock.total_ticks == ticks
    assert len(participants) > sea_transfer.DECK_CAPACITY


def test_unsupported_crossing_is_blocked_atomically_before_m5():
    world = encounter_world(close=True)
    encounter = world.voyages.voyage["encounter"]
    positions = [world.player.x, world.player.y, world.player.z]
    crew = [(sailor.id, sailor.x, sailor.y, sailor.z) for sailor in world.ship.crew]
    combat_before = deepcopy(world.combat.snapshot()) if hasattr(world.combat, "snapshot") else None

    ticks = world.clock.total_ticks
    with pytest.raises(ValueError, match="no witnessed attack or verified warrant"):
        world.voyages.request_crossing(world, "alone")
    assert [world.player.x, world.player.y, world.player.z] == positions
    assert [(s.id, s.x, s.y, s.z) for s in world.ship.crew] == crew
    assert not world.combat.active
    assert world.clock.total_ticks == ticks
    assert encounter["contact"]["combat_pending"] is None
    assert encounter["contact"]["crossing"] is None
    if combat_before is not None:
        assert world.combat.snapshot() == combat_before

    world.voyages.continue_after_sighting()
    assert world.voyages.voyage["status"] == "underway"


@pytest.mark.parametrize("mode", ["alone", "escorts", "everyone"])
def test_unsupported_crossing_modes_are_blocked_without_partial_state(mode):
    world = encounter_world(close=True)
    living = [sailor.id for sailor in world.ship.crew if sailor.health > 0]
    escorts = [living[0]] if mode == "escorts" else ()
    expected = ([world.player.id] if mode == "alone" else
                [world.player.id, *living] if mode == "everyone" else
                [world.player.id, *escorts])
    start_ticks = world.clock.total_ticks

    encounter = world.voyages.voyage["encounter"]
    before = deepcopy(encounter["contact"])
    with pytest.raises(ValueError, match="no witnessed attack or verified warrant"):
        world.voyages.request_crossing(world, mode, escorts)
    assert world.clock.total_ticks == start_ticks
    assert encounter["contact"] == before
    assert encounter["contact"]["combat_pending"] is None
    assert encounter["contact"]["crossing"] is None


def test_welcomed_crossing_is_peaceful_and_persists_exactly_once(tmp_path):
    from omnisim.world import tile as T

    world = encounter_world(close=True)
    encounter = world.voyages.voyage["encounter"]
    contact = sea_encounter.contact_of(encounter)
    contact["parley"] = {"topics": ["identity", "transfer_permission"],
                         "stand_down": False, "transfer_permission": True,
                         "warned_off": False}

    request = world.voyages.request_crossing(world, "escorts", [world.ship.crew[0].id])
    assert request["status"] == "pending" and request["participants"] == [
        world.player.id, world.ship.crew[0].id]
    assert world.voyages.voyage["status"] == "encounter"

    path = tmp_path / "pending-crossing.json"
    world.save(path)
    restored = World.load(path)
    saved = restored.voyages.voyage["encounter"]["contact"]["crossing"]
    assert saved == request
    assert restored.voyages.voyage["status"] == "encounter"
    home = {actor.id: (actor.x, actor.y, actor.z)
            for actor in [restored.player, *restored.ship.crew]
            if actor.id in request["participants"]}
    visit = restored.voyages.complete_peaceful_crossing(restored)
    assert visit["participants"] == request["participants"]
    assert restored.voyages.voyage["deck_visit"] == visit
    assert all((next(actor for actor in [restored.player, *restored.ship.crew]
                     if actor.id == sid).x,
                next(actor for actor in [restored.player, *restored.ship.crew]
                     if actor.id == sid).y) in sea_transfer.LANDING_CELLS
               for sid in request["participants"])
    assert restored.voyages.voyage["encounter"]["contact"]["crossing_completed"] is True
    assert restored.game_map.tile_id(66, 23) == T.FLOOR
    tampered = deepcopy(restored.voyages.snapshot())
    tampered["voyage"]["deck_visit"]["contact_instance_id"] = "other-hull:9"
    with pytest.raises(ValueError, match="Invalid saved peaceful deck visit"):
        VoyageSystem.restore(tampered, restored)

    restored.save(path)
    restored = World.load(path)
    assert restored.voyages.voyage["deck_visit"] == visit
    assert restored.game_map.tile_id(66, 23) == T.FLOOR
    assert restored.player.x < 69
    assert restored.voyages.return_crossing_party(restored) == request["participants"]
    actors = {actor.id: actor for actor in [restored.player, *restored.ship.crew]}
    assert all(actors[sid].x >= 69 and actors[sid].x < 92
               for sid in request["participants"])
    assert set(home) == set(request["participants"])
    assert restored.voyages.voyage["deck_visit"] is None
    assert restored.game_map.tile_id(66, 23) == T.WATER

    ticks = restored.clock.total_ticks
    with pytest.raises(ValueError, match="already completed"):
        restored.voyages.request_crossing(
            restored, "escorts", [restored.ship.crew[0].id])
    assert restored.clock.total_ticks == ticks
    restored.voyages.continue_after_sighting()
    assert restored.voyages.voyage["status"] == "underway"


def test_blocked_gangplank_rejects_crossing_without_partial_state():
    from omnisim.world import tile as T

    world = encounter_world(close=True)
    encounter = world.voyages.voyage["encounter"]
    encounter["contact"]["parley"] = {
        "topics": ["identity", "transfer_permission"],
        "stand_down": False, "transfer_permission": True, "warned_off": False,
    }
    request = world.voyages.request_crossing(world, "alone")
    start_ticks = world.clock.total_ticks
    actor_positions = [(actor.id, actor.x, actor.y, actor.z)
                       for actor in [world.player, *world.ship.crew]]
    world.game_map.tiles[23, 69] = T.WALL_WOOD  # block the only sea-side gangway cell

    with pytest.raises(ValueError, match="No clear reachable path"):
        world.voyages.complete_peaceful_crossing(world)
    assert world.clock.total_ticks == start_ticks
    assert [(actor.id, actor.x, actor.y, actor.z)
            for actor in [world.player, *world.ship.crew]] == actor_positions
    assert encounter["contact"]["crossing"] == request
    assert encounter["contact"]["crossing_completed"] is False
    assert world.voyages.voyage["deck_visit"] is None
    assert world.game_map.tile_id(66, 23) == T.WATER
    assert world.game_map.tile_id(69, 23) == T.WALL_WOOD


def test_captain_can_walk_back_over_gangplank_and_cleanup_visit(tmp_path):
    from omnisim.world import tile as T

    world = encounter_world(close=True)
    encounter = world.voyages.voyage["encounter"]
    encounter["contact"]["parley"] = {
        "topics": ["identity", "transfer_permission"],
        "stand_down": False, "transfer_permission": True, "warned_off": False,
    }
    world.voyages.request_crossing(world, "alone")
    world.voyages.complete_peaceful_crossing(world)
    assert (world.player.x, world.player.y) == (66, 23)
    assert world.room_name(world.player.x, world.player.y) == f"{encounter['name']} deck"
    with pytest.raises(ValueError, match="Return everyone"):
        world.voyages.continue_after_sighting()

    assert world.move(1, 0) and world.player.x == 67
    assert world.move(1, 0) and world.player.x == 68
    assert world.room_name(world.player.x, world.player.y) == "Gangplank"
    path = tmp_path / "captain-on-gangplank.json"
    world.save(path)
    world = World.load(path)
    assert world.player.x == 68 and world.voyages.voyage["deck_visit"] is not None
    assert world.move(1, 0) and world.player.x == 69
    assert world.voyages.voyage["deck_visit"] is None
    assert world.game_map.tile_id(66, 23) == T.WATER


def test_encounter_menu_completes_and_returns_a_consented_crossing():
    class Scripted:
        def __init__(self, answers):
            self.answers = iter(answers)
            self.frames = []

        def render_list(self, *_args, **_kwargs):
            self.frames.append(_args[1:] if len(_args) > 1 else [])

        def choose(self, _count):
            return next(self.answers)

        def wait_any(self):
            pass

    world = encounter_world(close=True)
    world.voyages.voyage["encounter"]["contact"]["parley"] = {
        "topics": ["identity", "transfer_permission"],
        "stand_down": False, "transfer_permission": True, "warned_off": False,
    }
    world.voyages.request_crossing(world, "alone")
    crossing_ui = Scripted([6, 1, None])
    voyage_menu(crossing_ui, world)
    assert world.voyages.voyage["deck_visit"] is not None
    assert world.player.x < 69
    assert any("Crossing completed" in " ".join(map(str, frame))
               for frame in crossing_ui.frames)

    return_ui = Scripted([6, 1, None])
    voyage_menu(return_ui, world)
    assert world.voyages.voyage["deck_visit"] is None
    assert world.player.x >= 69
    assert any("Returned aboard" in " ".join(map(str, frame))
               for frame in return_ui.frames)


def test_finite_at_sea_trade_offer_quote_commit_and_retry_survive_save(tmp_path):
    world = encounter_world("syndicate_1", close=True)
    encounter = world.voyages.voyage["encounter"]
    contact = sea_encounter.contact_of(encounter)
    target = next(ship for ship in world.npcs.ships if ship["id"] == encounter["id"])
    before_stock = deepcopy(target["cargo"])
    before_silver = target["silver"]
    before_money = world.economy.money
    before_cargo = deepcopy(world.economy.cargo)
    before_ticks = world.clock.total_ticks
    assert "grain" not in " ".join(sea_encounter.sighting_rows(world, encounter))
    assert contact["trade_offer"] is None

    offer = world.voyages.request_sea_trade_offer(world)
    assert world.clock.total_ticks == before_ticks + 6000
    assert offer["offer_id"] == contact["trade_offer"]["offer_id"]
    assert next(row for row in offer["items"] if row["item_id"] == "grain")["sell_quantity"] == 12
    assert set(row["item_id"] for row in offer["items"]) <= set(sea_trade.TRADE_ITEMS)
    assert target["cargo"] == before_stock and target["silver"] == before_silver
    assert world.economy.money == before_money and world.economy.cargo == before_cargo
    duplicate_offer = world.voyages.request_sea_trade_offer(world)
    assert duplicate_offer == offer and world.clock.total_ticks == before_ticks + 6000

    quote = world.voyages.quote_sea_trade(world, "grain", 2, True)
    quote_tick = world.clock.total_ticks
    assert quote["total"] == quote["unit_price"] * 2
    assert world.clock.total_ticks == quote_tick
    assert world.economy.money == before_money
    assert target["cargo"] == before_stock and target["silver"] == before_silver
    assert world.economy.cargo == before_cargo

    path = tmp_path / "pending-sea-trade.json"
    world.save(path)
    restored = World.load(path)
    restored_encounter = restored.voyages.voyage["encounter"]
    restored_quote = restored_encounter["contact"]["trade_pending"]
    assert restored_quote == quote
    tampered_trade = deepcopy(restored.voyages.snapshot())
    tampered_trade["voyage"]["encounter"]["contact"]["trade_pending"]["total"] += 1
    with pytest.raises(ValueError, match="Invalid saved pending sea-trade quote"):
        VoyageSystem.restore(tampered_trade, restored)
    restored_target = next(ship for ship in restored.npcs.ships
                           if ship["id"] == restored_encounter["id"])
    saved_money = restored.economy.money
    saved_cargo = deepcopy(restored.economy.cargo)
    saved_stock = deepcopy(restored_target["cargo"])
    saved_silver = restored_target["silver"]
    commit_tick = restored.clock.total_ticks
    receipt = restored.voyages.commit_sea_trade(restored, quote["transaction_id"])
    assert receipt["transaction_id"] == quote["transaction_id"]
    assert restored.economy.money == saved_money - quote["total"]
    assert restored.economy.cargo["grain"] == saved_cargo.get("grain", 0) + 2
    assert restored_target["cargo"]["grain"] == saved_stock["grain"] - 2
    assert restored_target["silver"] == saved_silver + quote["total"]
    assert restored.clock.total_ticks == commit_tick + 6000
    committed_money = restored.economy.money
    committed_cargo = deepcopy(restored.economy.cargo)
    committed_stock = deepcopy(restored_target["cargo"])
    committed_silver = restored_target["silver"]
    committed_ticks = restored.clock.total_ticks
    assert restored.voyages.commit_sea_trade(restored, quote["transaction_id"]) == receipt
    assert restored.economy.money == committed_money
    assert restored.economy.cargo == committed_cargo
    assert restored_target["cargo"] == committed_stock
    assert restored_target["silver"] == committed_silver
    assert restored.clock.total_ticks == committed_ticks

    restored.save(path)
    resumed = World.load(path)
    resumed_target = next(ship for ship in resumed.npcs.ships
                          if ship["id"] == resumed.voyages.voyage["encounter"]["id"])
    retry_tick = resumed.clock.total_ticks
    assert resumed.voyages.commit_sea_trade(resumed, quote["transaction_id"]) == receipt
    assert resumed.clock.total_ticks == retry_tick
    assert resumed_target["cargo"] == committed_stock
    assert resumed_target["silver"] == committed_silver


def test_at_sea_trade_is_finite_conserved_cancellable_and_revalidates_atomic_failures():
    world = encounter_world("syndicate_1", close=True)
    target = next(ship for ship in world.npcs.ships
                  if ship["id"] == world.voyages.voyage["encounter"]["id"])
    world.voyages.request_sea_trade_offer(world)
    initial_total = world.economy.money + target["silver"]
    initial_grain = world.economy.cargo.get("grain", 0) + target["cargo"].get("grain", 0)

    buy_quote = world.voyages.quote_sea_trade(world, "grain", 3, True)
    world.voyages.cancel_sea_trade(world, buy_quote["transaction_id"])
    assert world.economy.cargo.get("grain", 0) == 0
    assert target["cargo"]["grain"] == 12
    assert world.economy.money + target["silver"] == initial_total

    world.economy.money = 0
    ticks = world.clock.total_ticks
    with pytest.raises(ValueError, match="Not enough Marks"):
        world.voyages.quote_sea_trade(world, "grain", 1, True)
    assert world.voyages.voyage["encounter"]["contact"]["trade_pending"] is None
    assert target["cargo"]["grain"] == 12 and target["silver"] == 240
    assert world.clock.total_ticks == ticks

    world.economy.money = 500
    world.economy.cargo["beans"] = 1
    sale = world.voyages.quote_sea_trade(world, "beans", 1, False)
    sale_tick = world.clock.total_ticks
    sale_receipt = world.voyages.commit_sea_trade(world, sale["transaction_id"])
    assert sale_receipt["total"] == sale["unit_price"]
    assert world.economy.cargo.get("beans", 0) == 0
    assert target["cargo"].get("beans", 0) == 1
    assert world.economy.money + target["silver"] == 500 + 240
    assert world.economy.cargo.get("grain", 0) + target["cargo"].get("grain", 0) == initial_grain
    assert world.clock.total_ticks == sale_tick + 6000

    quote = world.voyages.quote_sea_trade(world, "grain", 1, True)
    quote_id = quote["transaction_id"]
    target["cargo"]["grain"] = 0
    state = (world.economy.money, deepcopy(world.economy.cargo), target["silver"],
             deepcopy(target["cargo"]), world.clock.total_ticks)
    with pytest.raises(ValueError, match="stock changed"):
        world.voyages.commit_sea_trade(world, quote_id)
    assert (world.economy.money, world.economy.cargo, target["silver"],
            target["cargo"], world.clock.total_ticks) == state


def test_at_sea_trade_rejects_unreserved_capacity_and_nonmerchant_without_mutation():
    from pirates.economy import TRADE_MASS_G

    world = encounter_world("syndicate_1", close=True)
    target = next(ship for ship in world.npcs.ships
                  if ship["id"] == world.voyages.voyage["encounter"]["id"])
    world.voyages.request_sea_trade_offer(world)
    world.economy.cargo["beans"] = 1
    world.economy.contracts.append({
        "offer_id": "reserved-beans", "status": "active",
        "item": "beans", "quantity": 1,
    })
    before = (world.economy.money, deepcopy(world.economy.cargo), target["silver"],
              deepcopy(target["cargo"]), world.clock.total_ticks)
    with pytest.raises(ValueError, match="unreserved cargo"):
        world.voyages.quote_sea_trade(world, "beans", 1, False)
    assert (world.economy.money, world.economy.cargo, target["silver"],
            target["cargo"], world.clock.total_ticks) == before
    world.economy.contracts.clear()

    world.economy.load = lambda: (TRADE_MASS_G, 0)
    with pytest.raises(ValueError, match="cargo capacity"):
        world.voyages.quote_sea_trade(world, "grain", 1, True)
    assert world.voyages.voyage["encounter"]["contact"]["trade_pending"] is None
    assert (world.economy.money, world.economy.cargo, target["silver"],
            target["cargo"], world.clock.total_ticks) == before

    pirate = encounter_world("raider_1", close=True)
    pirate_before = (deepcopy(pirate.npcs.ships), pirate.clock.total_ticks,
                     pirate.economy.money, deepcopy(pirate.economy.cargo))
    with pytest.raises(ValueError, match="Only a publicly assessed merchant"):
        pirate.voyages.request_sea_trade_offer(pirate)
    assert (pirate.npcs.ships, pirate.clock.total_ticks,
            pirate.economy.money, pirate.economy.cargo) == pirate_before

    distant = encounter_world("syndicate_1", close=False)
    distant_before = (distant.clock.total_ticks, deepcopy(distant.npcs.ships),
                      distant.economy.money, deepcopy(distant.economy.cargo))
    with pytest.raises(ValueError, match="close-range"):
        distant.voyages.request_sea_trade_offer(distant)
    assert (distant.clock.total_ticks, distant.npcs.ships,
            distant.economy.money, distant.economy.cargo) == distant_before


def test_encounter_menu_requests_and_commits_the_explicit_trade_offer():
    class Scripted:
        def __init__(self, answers):
            self.answers = iter(answers)
            self.frames = []

        def render_list(self, *_args, **_kwargs):
            self.frames.append(_args[1:] if len(_args) > 1 else [])

        def choose(self, _count):
            return next(self.answers)

        def wait_any(self):
            pass

    world = encounter_world("syndicate_1", close=True)
    offer_ui = Scripted([6, 5, None])
    voyage_menu(offer_ui, world)
    contact = world.voyages.voyage["encounter"]["contact"]
    assert contact["trade_offer"] is not None
    assert any("Merchant's finite offer" in " ".join(map(str, frame))
               for frame in offer_ui.frames)

    money = world.economy.money
    stock = next(ship for ship in world.npcs.ships
                 if ship["id"] == world.voyages.voyage["encounter"]["id"])["cargo"]["grain"]
    # Trade choices are beans bid, grain ask, then the remaining disclosed terms.
    trade_ui = Scripted([6, 5, 1, 0, 0, None])
    voyage_menu(trade_ui, world)
    assert world.economy.money < money
    assert next(ship for ship in world.npcs.ships
                if ship["id"] == world.voyages.voyage["encounter"]["id"])["cargo"]["grain"] == stock - 1
    assert any("Sea trade committed once" in " ".join(map(str, frame))
               for frame in trade_ui.frames)


def test_at_sea_trade_commit_rechecks_contact_range_without_partial_mutation():
    world = encounter_world("syndicate_1", close=True)
    target = next(ship for ship in world.npcs.ships
                  if ship["id"] == world.voyages.voyage["encounter"]["id"])
    world.voyages.request_sea_trade_offer(world)
    quote = world.voyages.quote_sea_trade(world, "grain", 1, True)
    before = (world.economy.money, deepcopy(world.economy.cargo), target["silver"],
              deepcopy(target["cargo"]), world.clock.total_ticks)
    world.voyages.voyage["encounter"]["contact"]["range"] = "distant"
    with pytest.raises(ValueError, match="close-range eligibility"):
        world.voyages.commit_sea_trade(world, quote["transaction_id"])
    assert (world.economy.money, world.economy.cargo, target["silver"],
            target["cargo"], world.clock.total_ticks) == before


def test_at_sea_trade_commit_rechecks_player_purse_capacity_and_counterparty_purse():
    from pirates.economy import TRADE_MASS_G

    world = encounter_world("syndicate_1", close=True)
    target = next(ship for ship in world.npcs.ships
                  if ship["id"] == world.voyages.voyage["encounter"]["id"])
    world.voyages.request_sea_trade_offer(world)
    quote = world.voyages.quote_sea_trade(world, "grain", 1, True)
    world.economy.money = 0
    before = (world.economy.money, deepcopy(world.economy.cargo), target["silver"],
              deepcopy(target["cargo"]), world.clock.total_ticks)
    with pytest.raises(ValueError, match="Player purse changed"):
        world.voyages.commit_sea_trade(world, quote["transaction_id"])
    assert (world.economy.money, world.economy.cargo, target["silver"],
            target["cargo"], world.clock.total_ticks) == before
    world.voyages.cancel_sea_trade(world, quote["transaction_id"])

    world.economy.money = 500
    quote = world.voyages.quote_sea_trade(world, "grain", 1, True)
    world.economy.load = lambda: (TRADE_MASS_G, 0)
    before = (world.economy.money, deepcopy(world.economy.cargo), target["silver"],
              deepcopy(target["cargo"]), world.clock.total_ticks)
    with pytest.raises(ValueError, match="Cargo capacity changed"):
        world.voyages.commit_sea_trade(world, quote["transaction_id"])
    assert (world.economy.money, world.economy.cargo, target["silver"],
            target["cargo"], world.clock.total_ticks) == before

    world.voyages.cancel_sea_trade(world, quote["transaction_id"])
    world.economy.load = lambda: (0, 0)
    world.economy.cargo["beans"] = 1
    quote = world.voyages.quote_sea_trade(world, "beans", 1, False)
    target["silver"] = 0
    before = (world.economy.money, deepcopy(world.economy.cargo), target["silver"],
              deepcopy(target["cargo"]), world.clock.total_ticks)
    with pytest.raises(ValueError, match="Merchant purse changed"):
        world.voyages.commit_sea_trade(world, quote["transaction_id"])
    assert (world.economy.money, world.economy.cargo, target["silver"],
            target["cargo"], world.clock.total_ticks) == before


def test_pending_handoff_rejects_tampering_and_old_saves_default_to_distant():
    world = encounter_world(close=True)
    encounter = world.voyages.voyage["encounter"]
    contact = sea_encounter.contact_of(encounter)
    contact["parley"] = {"topics": ["identity"], "stand_down": False,
                         "transfer_permission": False, "warned_off": False}
    request = sea_encounter.build_handoff(
        world, encounter, "forced_boarding", world.clock.total_ticks,
        [world.player.id], {"kind": "cross_with_manifest", "mode": "alone",
                            "personnel_manifest": [world.player.id]})
    contact["combat_pending"] = request

    data = world.voyages.snapshot()
    migrated_pending = VoyageSystem.restore(deepcopy(data), world)
    assert migrated_pending.voyage["encounter"]["contact"]["combat_pending"] is None
    assert any("unsupported legacy hostile handoff" in event.lower()
               for event in migrated_pending.events)
    tampered = deepcopy(data)
    tampered["voyage"]["encounter"]["contact"]["combat_pending"]["request_id"] = "forged"
    with pytest.raises(ValueError, match="identity does not match"):
        VoyageSystem.restore(tampered, world)

    tampered = deepcopy(data)
    tampered["voyage"]["encounter"]["contact"]["combat_pending"]["range"] = "distant"
    with pytest.raises(ValueError, match="requires close range"):
        VoyageSystem.restore(tampered, world)

    tampered = deepcopy(data)
    tampered["voyage"]["encounter"]["contact"]["range"] = "alongside"
    with pytest.raises(ValueError, match="Invalid saved encounter range"):
        VoyageSystem.restore(tampered, world)

    legacy = deepcopy(data)
    legacy["voyage"]["encounter"].pop("contact")
    legacy.pop("conduct_ledger")
    restored = VoyageSystem.restore(legacy, world)
    assert restored.voyage["encounter"]["contact"] == sea_encounter.new_contact()
    assert restored.conduct_ledger.events == []


def test_cannon_commitment_is_blocked_without_evidence_and_is_atomic():
    world = encounter_world()
    world.ship.ammo["port_cannon"] = 1
    target = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
    target_before = deepcopy(target)
    ammo_before = deepcopy(world.ship.ammo)
    ticks_before = world.clock.total_ticks
    player_before = (world.player.x, world.player.y, world.player.z)
    crew_before = [(s.id, s.x, s.y, s.z, s.health) for s in world.ship.crew]

    encounter = world.voyages.voyage["encounter"]
    before_contact = deepcopy(encounter.get("contact"))
    with pytest.raises(ValueError, match="no witnessed attack or verified warrant"):
        world.voyages.request_naval_combat(world, "port_cannon")
    assert world.clock.total_ticks == ticks_before
    assert world.ship.ammo == ammo_before
    assert next(ship for ship in world.npcs.ships if ship["id"] == "government_1") == target_before
    assert (world.player.x, world.player.y, world.player.z) == player_before
    assert [(s.id, s.x, s.y, s.z, s.health) for s in world.ship.crew] == crew_before
    assert not world.combat.active
    assert encounter.get("contact") == before_contact
    assert "combat" not in encounter
    rows = sea_encounter.sighting_rows(world, encounter)
    assert any("Marine patrol: neutral by default" in row for row in rows)
    assert any("Hostility: prohibited" in row for row in rows)


def test_witnessed_fire_evidence_is_contact_scoped_and_saved_with_m5_handoff(tmp_path):
    from pirates.management import combat_handoff_rows

    world = encounter_world()
    world.ship.ammo["port_cannon"] = 1
    encounter = world.voyages.voyage["encounter"]
    instance_id = encounter.get("contact_instance_id", encounter["id"])
    evidence = world.voyages.conduct_ledger.record(
        tick=world.clock.total_ticks, cause="committed_fire",
        aggressor_id=encounter["id"], target_id=world.player.id,
        encounter_id=instance_id, witnesses=[world.player.id])

    assert sea_encounter.hostile_commitment_authorized(world, encounter)
    assert not world.voyages.conduct_ledger.authorization(
        target_id=encounter["id"], actor_id=world.player.id,
        encounter_id=f"{instance_id}:later", current_tick=world.clock.total_ticks)
    ammo_before = deepcopy(world.ship.ammo)
    request = world.voyages.request_naval_combat(world, "port_cannon")
    assert request["lawful_basis_id"] == evidence["event_id"]
    assert request["status"] == "pending"
    assert world.ship.ammo == ammo_before and not world.combat.active
    assert any(evidence["event_id"] in row
               for row in combat_handoff_rows(world, encounter, request))
    assert any("combat still requires the separate M5 authority" in row
               for row in sea_encounter.sighting_rows(world, encounter))

    path = tmp_path / "lawful-handoff.json"
    world.save(path)
    restored = World.load(path)
    restored_request = restored.voyages.voyage["encounter"]["contact"]["combat_pending"]
    assert restored_request == request
    assert restored.voyages.conduct_ledger.snapshot() == world.voyages.conduct_ledger.snapshot()
    assert not restored.combat.active

    tampered = deepcopy(world.voyages.snapshot())
    tampered["conduct_ledger"]["events"][0]["target_id"] = "forged-target"
    with pytest.raises(ValueError, match="identity does not match"):
        VoyageSystem.restore(tampered, world)

    tampered = deepcopy(world.voyages.snapshot())
    handoff = tampered["voyage"]["encounter"]["contact"]["combat_pending"]
    handoff["lawful_basis_id"] = "0" * 24
    handoff["request_id"] = sea_encounter._stable_handoff_id(
        {"id": handoff["encounter_id"]}, handoff["cause"], handoff["tick"],
        handoff["participants"], handoff["requested_action"], handoff["lawful_basis_id"])
    with pytest.raises(ValueError, match="lacks matching saved lawful evidence"):
        VoyageSystem.restore(tampered, world)


def test_malformed_handoff_payloads_fail_validation_without_type_errors():
    world = encounter_world(close=True)
    encounter = world.voyages.voyage["encounter"]
    contact = sea_encounter.contact_of(encounter)
    contact["combat_pending"] = sea_encounter.build_handoff(
        world, encounter, "forced_boarding", world.clock.total_ticks,
        [world.player.id], {"kind": "cross_with_manifest", "mode": "alone",
                            "personnel_manifest": [world.player.id]})
    base = world.voyages.snapshot()
    mutations = [
        lambda request: request.update(schema_version=True),
        lambda request: request.update(participants=["captain", []]),
        lambda request: request.update(position={"player_chart": [float("nan"), 1.0],
                                                 "target_node": "shoals"}),
        lambda request: request["requested_action"].update(mode=[]),
        lambda request: (
            request.update(participants=["captain", "ghost"]),
            request.update(requested_action={"kind": "cross_with_manifest", "mode": "escorts",
                                             "personnel_manifest": ["captain", "ghost"]}),
            request.update(request_id=sea_encounter._stable_request_id(
                {"id": request["encounter_id"]}, request["cause"], request["tick"],
                request["participants"], request["requested_action"])),
        ),
    ]
    for mutate in mutations:
        tampered = deepcopy(base)
        mutate(tampered["voyage"]["encounter"]["contact"]["combat_pending"])
        with pytest.raises(ValueError):
            VoyageSystem.restore(tampered, world)


def test_active_encounter_reserves_its_vessel_from_hourly_npc_movement():
    world = encounter_world()
    encounter = world.voyages.voyage["encounter"]
    live = next(ship for ship in world.npcs.ships if ship["id"] == encounter["id"])
    node = live["node"]
    next_hour = max(world.npcs.hour, world.clock.total_ticks // 360000) + 1
    world.clock.total_ticks = next_hour * 360000
    world.on_ship_second()
    assert live["node"] == node
    assert encounter["node"] == node


def test_active_combat_blocks_encounter_actions_without_spending_time():
    world = encounter_world()
    world.combat.active = True
    ticks = world.clock.total_ticks
    for call in (lambda: world.voyages.signal_sighting(world, "identify"),
                 lambda: world.voyages.approach_sighting(world),
                 lambda: world.voyages.evade_sighting(world)):
        with pytest.raises(ValueError, match="during active combat"):
            call()
    assert world.clock.total_ticks == ticks


def test_active_legacy_artillery_save_keeps_its_resume_authority_only():
    world = encounter_world()
    world.voyages.voyage["encounter"]["combat"] = {
        "status": "active", "seed": 7, "exchange": 0, "log": []}
    ticks = world.clock.total_ticks
    with pytest.raises(ValueError, match="legacy artillery exchange is active"):
        world.voyages.signal_sighting(world, "identify")
    with pytest.raises(ValueError, match="legacy artillery exchange is active"):
        world.voyages.request_crossing(world, "alone")
    assert world.clock.total_ticks == ticks


def test_encounter_menu_offers_signals_and_explains_prohibited_hostility():
    class Scripted:
        def __init__(self, answers):
            self.answers = iter(answers)

        def render_list(self, *_args, **_kwargs):
            pass

        def choose(self, _count):
            return next(self.answers)

        def wait_any(self):
            pass

    world = encounter_world()
    ticks = world.clock.total_ticks
    voyage_menu(Scripted([6, 1, 0, None]), world)
    signals = world.voyages.voyage["encounter"]["contact"]["signals"]
    assert [row["intent"] for row in signals] == ["identify"]
    assert world.clock.total_ticks == ticks + 6000

    encounter = world.voyages.voyage["encounter"]
    sea_encounter.contact_of(encounter)["range"] = "close"
    with pytest.raises(ValueError, match="no witnessed attack or verified warrant"):
        world.voyages.request_crossing(world, "alone")
    rows = sea_encounter.sighting_rows(world, encounter)
    assert any("Hostility: prohibited" in row for row in rows)
    assert encounter["contact"]["combat_pending"] is None
