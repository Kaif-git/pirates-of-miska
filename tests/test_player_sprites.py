import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

from pirates.game import Captain
from pirates.player_sprites import ASSET, CAPTAIN_CODEPOINT, install_player_sprite, sprite_for_entity


def test_captain_uses_only_the_character_sprite():
    assert sprite_for_entity(Captain()) == "captain"
    assert sprite_for_entity(type("Crew", (), {"get": lambda self, key, default=None: "crew" if key == "role" else default})()) is None


def test_kenney_character_asset_is_cc0_and_fits_atlas():
    class Atlas(dict):
        pass

    atlas = Atlas()
    sprites = install_player_sprite(atlas, 20, 24)
    assert ASSET.is_file()
    assert (ASSET.parent / "License.txt").is_file()
    assert sprites == {"captain": CAPTAIN_CODEPOINT}
    assert atlas[CAPTAIN_CODEPOINT].shape == (24, 20, 4)
