from copy import deepcopy

import pytest

from pirates import sea_encounter
from pirates.game import World
from pirates.management import crossing_request_rows, voyage_menu
from pirates.ship import LABORS
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


def test_refused_crossing_is_one_boarding_handoff_that_moves_nobody_and_blocks_departure():
    world = encounter_world(close=True)
    encounter = world.voyages.voyage["encounter"]
    positions = [world.player.x, world.player.y, world.player.z]
    crew = [(sailor.id, sailor.x, sailor.y, sailor.z) for sailor in world.ship.crew]
    combat_before = deepcopy(world.combat.snapshot()) if hasattr(world.combat, "snapshot") else None

    request = world.voyages.request_crossing(world, "alone")
    assert request["cause"] == "forced_boarding"
    assert request["participants"] == [world.player.id]
    assert request["requested_action"] == {
        "kind": "cross_with_manifest", "mode": "alone",
        "personnel_manifest": [world.player.id]}
    assert request["status"] == "pending" and request["request_id"]
    assert world.voyages.request_crossing(world, "alone") == request
    assert [world.player.x, world.player.y, world.player.z] == positions
    assert [(s.id, s.x, s.y, s.z) for s in world.ship.crew] == crew
    assert not world.combat.active
    if combat_before is not None:
        assert world.combat.snapshot() == combat_before

    with pytest.raises(ValueError, match="pending encounter request"):
        world.voyages.continue_after_sighting()
    with pytest.raises(ValueError, match="pending encounter request"):
        world.voyages.turn_back(world)
    with pytest.raises(ValueError, match="pending encounter request"):
        world.voyages.signal_sighting(world, "intentions")

    ticks = world.clock.total_ticks
    with pytest.raises(ValueError, match="cannot be withdrawn"):
        world.voyages.withdraw_crossing_request(world)
    assert encounter["contact"]["combat_pending"] == request
    assert world.clock.total_ticks == ticks
    assert world.voyages.voyage["status"] == "encounter"


@pytest.mark.parametrize("mode", ["alone", "escorts", "everyone"])
def test_refused_crossing_modes_create_one_exact_manifest(mode):
    world = encounter_world(close=True)
    living = [sailor.id for sailor in world.ship.crew if sailor.health > 0]
    escorts = [living[0]] if mode == "escorts" else ()
    expected = ([world.player.id] if mode == "alone" else
                [world.player.id, *living] if mode == "everyone" else
                [world.player.id, *escorts])
    start_ticks = world.clock.total_ticks

    first = world.voyages.request_crossing(world, mode, escorts)
    assert first["cause"] == "forced_boarding"
    assert first["requested_action"]["mode"] == mode
    assert first["participants"] == expected
    assert first["requested_action"]["personnel_manifest"] == expected
    assert world.clock.total_ticks == start_ticks + 6000
    assert world.voyages.request_crossing(world, mode, escorts) == first
    assert world.clock.total_ticks == start_ticks + 6000
    rows = crossing_request_rows(world, world.voyages.voyage["encounter"], first)
    if mode == "everyone":
        assert all(sailor.name in " ".join(rows) for sailor in world.ship.crew if sailor.health > 0)


def test_welcomed_crossing_is_peaceful_and_persists_exactly_once(tmp_path):
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
    restored.voyages.withdraw_crossing_request(restored)
    restored.voyages.continue_after_sighting()
    assert restored.voyages.voyage["status"] == "underway"


def test_pending_handoff_rejects_tampering_and_old_saves_default_to_distant():
    world = encounter_world(close=True)
    encounter = world.voyages.voyage["encounter"]
    contact = sea_encounter.contact_of(encounter)
    contact["parley"] = {"topics": ["identity"], "stand_down": False,
                         "transfer_permission": False, "warned_off": False}
    world.voyages.request_crossing(world, "alone")

    data = world.voyages.snapshot()
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
    restored = VoyageSystem.restore(legacy, world)
    assert restored.voyage["encounter"]["contact"] == sea_encounter.new_contact()


def test_cannon_intent_is_idempotent_persists_and_never_fires_or_consumes_ammunition(tmp_path):
    from pirates.management import combat_handoff_rows

    world = encounter_world()
    world.ship.ammo["port_cannon"] = 1
    target = next(ship for ship in world.npcs.ships if ship["id"] == "government_1")
    target_before = deepcopy(target)
    ammo_before = deepcopy(world.ship.ammo)
    ticks_before = world.clock.total_ticks
    player_before = (world.player.x, world.player.y, world.player.z)
    crew_before = [(s.id, s.x, s.y, s.z, s.health) for s in world.ship.crew]

    request = world.voyages.request_naval_combat(world, "port_cannon")
    assert request["cause"] == "cannon_fire"
    assert request["requested_action"] == {"kind": "fire_cannon", "cannon": "port_cannon"}
    assert request["request_id"]
    panel_rows = combat_handoff_rows(world, world.voyages.voyage["encounter"], request)
    assert any("Government marine patrol" in row for row in panel_rows)
    assert any(world.player.name in row for row in panel_rows)
    assert any("no combat result was resolved" in row for row in panel_rows)
    assert world.voyages.request_naval_combat(world, "port_cannon") == request
    assert world.clock.total_ticks == ticks_before
    assert world.ship.ammo == ammo_before
    assert next(ship for ship in world.npcs.ships if ship["id"] == "government_1") == target_before
    assert (world.player.x, world.player.y, world.player.z) == player_before
    assert [(s.id, s.x, s.y, s.z, s.health) for s in world.ship.crew] == crew_before
    assert not world.combat.active
    assert "combat" not in world.voyages.voyage["encounter"]

    path = tmp_path / "pending-cannon-handoff.json"
    world.save(path)
    restored = World.load(path)
    restored_request = restored.voyages.voyage["encounter"]["contact"]["combat_pending"]
    assert restored_request == request
    restored_ammo = deepcopy(restored.ship.ammo)
    with pytest.raises(ValueError, match="pending encounter request"):
        restored.voyages.continue_after_sighting()
    assert restored.voyages.request_naval_combat(restored, "port_cannon") == request
    assert restored.ship.ammo == restored_ammo
    assert not restored.combat.active


def test_malformed_handoff_payloads_fail_validation_without_type_errors():
    world = encounter_world(close=True)
    world.voyages.request_crossing(world, "alone")
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


def test_encounter_menu_offers_signals_then_displays_pending_handoff():
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

    sea_encounter.contact_of(world.voyages.voyage["encounter"])["range"] = "close"
    world.voyages.request_crossing(world, "alone")
    voyage_menu(Scripted([6, 0, None]), world)
    assert world.voyages.voyage["encounter"]["contact"]["combat_pending"] is not None
