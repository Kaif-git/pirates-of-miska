"""Authored post-undertaking review: consent, amendment and migration."""
import json

import pytest

from pirates.game import World
from pirates.management import crew_menu
from tales_sim.clock import TICKS_PER_DAY, TICKS_PER_HOUR


def ashore(world, port=None):
    if port is not None:
        world.economy.port = port
    world.at_sea = False
    world.player.x, world.player.y, world.player.z = 32, 22, 0


def set_day(world, day, hour=9):
    world.clock.total_ticks = (day - 1) * TICKS_PER_DAY + hour * TICKS_PER_HOUR


def satisfied_maelin(day=4):
    world = World()
    ashore(world, "verdant")
    sailor = world.economy.recruit_candidate(world, "maelin_orr")
    set_day(world, day, 20)
    ashore(world, "cinder")
    world.advance(0)
    assert world.economy.employment.record(sailor.id)["undertaking"]["status"] == "satisfied"
    return world, sailor


def test_keeping_the_promise_offers_the_authored_review():
    world, sailor = satisfied_maelin()
    record = world.economy.employment.record(sailor.id)
    assert record["review"] == {"status": "offered", "day": 4}
    offer = world.economy.employment.review_offer(sailor.id)
    assert offer["role"] == "Ship's clerk — records and rigging"
    assert offer["wage_per_day"] == 3
    assert record["role"] == "Deckhand — records and rigging"
    assert record["wage_per_day"] == 2


def test_no_review_is_invented_for_an_ordinary_candidate():
    world = World()
    ashore(world, "verdant")
    sailor = world.economy.recruit_candidate(world, "pippin_ash")
    record = world.economy.employment.record(sailor.id)
    assert record["review"] is None
    assert world.economy.employment.review_offer(sailor.id) is None


def test_accepting_the_review_changes_role_and_future_wages_only():
    world, sailor = satisfied_maelin()
    offer = world.economy.employment.review_offer(sailor.id)
    record = world.economy.employment.accept_review(world, sailor.id)
    assert record["role"] == offer["role"]
    assert record["wage_per_day"] == 3
    assert record["review"] == {"status": "accepted", "day": 4}
    assert record["signing_payment"] == 60  # no second signing payment
    assert world.economy.employment.review_offer(sailor.id) is None

    settle_before = record["paid_total"]
    set_day(world, 5, 9)
    world.economy.employment.settle_day(world, 5)
    record = world.economy.employment.record(sailor.id)
    assert record["paid_total"] == settle_before + 3


def test_review_requires_the_captain_ashore_and_declining_keeps_terms():
    world, sailor = satisfied_maelin()
    world.at_sea = True
    world.player.x, world.player.y, world.player.z = 76, 25, 0
    with pytest.raises(ValueError, match="ashore at a port"):
        world.economy.employment.accept_review(world, sailor.id)
    assert world.economy.employment.record(sailor.id)["review"]["status"] == "offered"

    ashore(world, "cinder")
    record = world.economy.employment.decline_review(world, sailor.id)
    assert record["role"] == "Deckhand — records and rigging"
    assert record["wage_per_day"] == 2
    assert record["review"] == {"status": "declined", "day": 4}
    assert world.economy.employment.review_offer(sailor.id) is None
    with pytest.raises(ValueError, match="No employment review"):
        world.economy.employment.decline_review(world, sailor.id)


def test_review_state_survives_save_and_version_one_saves_migrate(tmp_path):
    world, sailor = satisfied_maelin()
    path = tmp_path / "review.json"
    world.save(path)
    loaded = World.load(path)
    assert loaded.economy.employment.record(sailor.id)["review"] == {
        "status": "offered", "day": 4}

    data = json.loads(path.read_text(encoding="utf-8"))
    ledger = data["economy"]["employment"]
    assert ledger["version"] == 2
    ledger["version"] = 1
    del ledger["records"][sailor.id]["review"]
    legacy = tmp_path / "review-v1.json"
    legacy.write_text(json.dumps(data), encoding="utf-8")
    migrated = World.load(legacy)
    record = migrated.economy.employment.record(sailor.id)
    assert record["review"] is None
    assert record["wage_per_day"] == 2
    assert record["undertaking"]["status"] == "satisfied"
    assert migrated.economy.employment.settled_day == 4


def test_port_review_menu_applies_the_choice_after_explicit_confirmation():
    class Scripted:
        def __init__(self):
            self.answers = iter((6, 8, 0, None, None))
            self.titles = []

        def render_list(self, _world, title, *_args, **_kwargs):
            self.titles.append(title)

        def choose(self, _count):
            return next(self.answers)

        def wait_any(self):
            pass

    world, sailor = satisfied_maelin()
    crew_menu(Scripted(), world)
    record = world.economy.employment.record(sailor.id)
    assert record["role"] == "Ship's clerk — records and rigging"
    assert record["wage_per_day"] == 3
    assert record["review"]["status"] == "accepted"


def test_arrival_report_offers_the_review_follow_up():
    world, sailor = satisfied_maelin()
    world.voyages.voyage = {"status": "arrived", "destination": "cinder", "outcomes": [],
                            "departure_snapshot": None, "arrival_snapshot": None}
    report = world.voyages.arrival_report(world)
    assert any("asked to discuss amended terms" in line for line in report)
