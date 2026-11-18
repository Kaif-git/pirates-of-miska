"""Reusable deterministic group encounter for native P7 verification/play."""
from copy import deepcopy

from pirates.duel_system.config import load_config
from pirates.duel_system.model import Equipment, Fighter, Personality, Weapon
from pirates.duel_system.session import DuelSession


SUPPORTED_ACTOR_COUNTS = (2, 5, 10, 20)


def make_group_session(actor_count=5, seed=1, *, guide_mode="hints"):
    """Build an isolated N-actor ring scenario; actor_count includes the player."""
    if actor_count not in SUPPORTED_ACTOR_COUNTS:
        raise ValueError(f"Actor count must be one of {SUPPORTED_ACTOR_COUNTS}")
    cfg = load_config()
    arena = cfg["arena"]
    player_pos = tuple(arena["player_pos"])
    exits = {tuple(pos) for pos in arena["exits"]}
    floor = {(x, y) for x in range(1, arena["width"]-1)
             for y in range(1, arena["height"]-1)}
    candidates = sorted((pos for pos in floor if pos != player_pos
                         and pos not in exits
                         and max(abs(pos[0]-player_pos[0]), abs(pos[1]-player_pos[1])) >= 3),
                       key=lambda pos: (max(abs(pos[0]-player_pos[0]), abs(pos[1]-player_pos[1])),
                                        abs(pos[0]-player_pos[0])+abs(pos[1]-player_pos[1]),
                                        pos[1], pos[0]))
    if actor_count-1 > len(candidates):
        raise ValueError("The configured arena cannot hold that many fighters")

    def build_fighter(identity, name, team, pos, fixture, profile_name=None):
        row = Fighter(identity, name, cfg["defaults"]["level"], **cfg["fixtures"][fixture],
                      pos=pos, team_id=team,
                      equipment=Equipment(Weapon(**cfg["gear"]["weapon"],
                                                  item_id=f"{identity}:weapon:{seed}")),
                      bandages=arena["bandages"], food=arena["food"], water=arena["water"])
        if profile_name:
            profile = deepcopy(cfg["ronin"])
            profile.update(deepcopy(cfg["ai_profiles"][profile_name]))
            profile["archetype"] = profile_name
            row.personality = Personality(**profile)
        return row

    fighters = [build_fighter("player", "Dorime", "players", player_pos, "dorime")]
    roster = tuple(cfg["personality"]["roster"])
    for index, pos in enumerate(candidates[:actor_count-1], 1):
        profile = roster[(index + seed - 1) % len(roster)]
        fighters.append(build_fighter(f"raider_{index}", f"Raider {index}",
                                      "raiders", pos, "ronin", profile))
    leader = fighters[1].id if len(fighters) > 1 else None
    leaders = {"players": fighters[0].id}
    if leader:
        leaders["raiders"] = leader
    return DuelSession.new_group(fighters, config=cfg, seed=seed, guide_mode=guide_mode,
                                 walkable=floor, exits=exits, leaders=leaders)
