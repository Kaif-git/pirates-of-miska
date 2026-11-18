"""Save-free mechanical examples, not a win-rate/optimal-strategy claim."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'engine')]
from pirates.duel_system.session import DuelSession
from pirates.duel_system.engine import Choice, commit_action, advance_one_tick
from pirates.duel_system.model import ActionKind, Zone
from pirates.duel_system.stats import effective_stats, phase_ticks


def snapshot(session):
    f, cfg = session.player, session.state.config
    eff = effective_stats(f, cfg, session.state.tick)
    return dict(speed=eff.speed, strength=eff.strength, accuracy=eff.accuracy,
                light_ticks=phase_ticks(cfg['actions']['LIGHT']['windup'], eff.speed, cfg),
                light_power=eff.strength*cfg['actions']['LIGHT']['power']+f.equipment.weapon.edge)


def exchange(player_kind, zone=Zone.TORSO, enemy_kind=ActionKind.LIGHT, enemy_strength=18):
    session = DuelSession.new(guide_mode='off')
    session.enemy.pos = (session.player.pos[0]+1, session.player.pos[1])
    session.enemy.strength = enemy_strength
    commit_action(session.state, session.player.id, Choice(player_kind, zone if player_kind in {ActionKind.LIGHT, ActionKind.HEAVY} else None))
    commit_action(session.state, session.enemy.id, Choice(enemy_kind, Zone.TORSO))
    for _ in range(40):
        advance_one_tick(session.state)
    relevant = {'HIT', 'WHIFF', 'PARRY', 'BLOCK', 'GUARD_BREAK'}
    return [dict(tick=e.tick, actor=e.actor, event=e.type.value,
                 severity=e.severity.value if e.severity else None, values=e.deltas)
            for e in session.state.event_log if e.type.value in relevant]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    results = {'scope': 'Controlled mechanical examples only. No universal winnability or win-rate claim.',
               'light_trade': exchange(ActionKind.LIGHT),
               'arm_trade': exchange(ActionKind.LIGHT, Zone.ARM),
               'light_guard': {str(strength): exchange(ActionKind.GUARD, enemy_strength=strength) for strength in (18, 25, 26)},
               'heavy_guard': exchange(ActionKind.GUARD, enemy_kind=ActionKind.HEAVY)}
    for field, values in (('fatigue', (0, 15, 35, 60, 85)), ('pain', (0, 15, 30, 50)), ('blood', (100, 50, 49, 25, 16))):
        results[field] = []
        for value in values:
            session = DuelSession.new(guide_mode='off')
            setattr(session.player, field, value)
            results[field].append({field: value, **snapshot(session)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
