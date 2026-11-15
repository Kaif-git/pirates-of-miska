import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

from omnisim.world import tile as T
from pirates.urizen_tileset import (
    ASSET_ROOT,
    DOOR_SPRITES,
    GROUND_SPRITES,
    TILE_SPRITES_BY_ID,
    _records,
    install_sprite_atlas,
    sprite_for_ground,
    sprite_for_tile,
)


def test_every_game_tile_has_a_urizen_mapping():
    assert set(T.BY_ID) == set(TILE_SPRITES_BY_ID) | {T.DOOR}
    assert sprite_for_tile(T.DOOR, door_open=False) == DOOR_SPRITES[False]
    assert sprite_for_tile(T.DOOR, door_open=True) == DOOR_SPRITES[True]
    assert sprite_for_tile(T.WATER) == "water"
    assert sprite_for_tile(999) is None


def test_ground_items_use_urizen_sprite_when_available():
    assert sprite_for_ground([{"id": "rope"}]) == GROUND_SPRITES["rope"]
    assert sprite_for_ground([{"id": "biscuit"}]) == GROUND_SPRITES["biscuit"]
    assert sprite_for_ground([{"id": "unknown"}]) is None


def test_urizen_pack_is_present_and_atlas_installs_native_tiles():
    class Atlas(dict):
        pass

    atlas = Atlas()
    codepoints = install_sprite_atlas(atlas, 20, 24)
    assert len(_records()) == 1177
    assert set(TILE_SPRITES_BY_ID.values()) | set(DOOR_SPRITES.values()) | set(GROUND_SPRITES.values()) == codepoints.keys()
    assert "water" in codepoints and "door_open" in codepoints
    assert atlas[codepoints["water"]].shape == (24, 20, 4)
    assert (ASSET_ROOT / "larger" / "assembled").is_dir()
