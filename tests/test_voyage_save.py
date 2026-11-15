import json

from pirates.game import World


def test_new_voyage_default_and_empty_legacy_roster_roundtrip(tmp_path):
    world = World()
    assert world.voyages.crew_order["members"] == [world.voyages.crew_order["leader"]]
    path = tmp_path / "voyage.json"
    world.save(path)
    data = json.loads(path.read_text())
    data["voyage"]["crew_order"]["members"] = []
    path.write_text(json.dumps(data))
    loaded = World.load(path)
    assert loaded.voyages.crew_order["members"] == [loaded.voyages.crew_order["leader"]]
