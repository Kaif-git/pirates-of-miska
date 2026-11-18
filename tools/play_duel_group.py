"""Play the P7 multi-actor combat sandbox in the native game dashboard.

Run from the repository root, for example:
    python tools/play_duel_group.py --actors 5
Esc pauses; press Enter in the terminal to resume or Q to close the sandbox.
"""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

import play
from duel_group_fixture import SUPPORTED_ACTOR_COUNTS, make_group_session
from pirates.duel_ui import run_sword_duel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actors", type=int, choices=SUPPORTED_ACTOR_COUNTS, default=5,
                        help="total actors including the player (default: 5)")
    parser.add_argument("--seed", type=int, default=41)
    args = parser.parse_args()

    world = play.World()
    world.duel_journal = make_group_session(args.actors, args.seed).to_data()
    renderer = play.PiratesRenderer(settings=play.RenderSettings(
        world_font_size=20, world_cell_width=20, world_cell_height=24))
    try:
        renderer.init_for(world.game_map.width, world.game_map.height)
        while True:
            result = run_sword_duel(renderer, world)
            if result == "quit":
                break
            response = input("Sandbox paused. Press Enter to resume, or Q then Enter to close: ").strip().lower()
            if response == "q":
                break
    finally:
        renderer.shutdown()


if __name__ == "__main__":
    main()
