"""M10 test-first XP formulas, guided accounting, cap and persistence."""
import pytest

from pirates.duel_system.config import load_config
from pirates.duel_system.model import EventType
from pirates.duel_system.progression import award_xp, kill_xp, xp_to_next
from pirates.duel_system.session import DuelSession
from test_duel_system_scheduler import arena


def test_xp_curve_and_level_ratio_clamps_and_guide_discounts():
    cfg = load_config()
    assert xp_to_next(1, cfg) == 100
    assert xp_to_next(5, cfg) == round(100 * 1.25**4)
    assert kill_xp(10, 10, cfg, "off") == 100
    assert kill_xp(10, 10, cfg, "hints") == 85
    assert kill_xp(10, 10, cfg, "full") == 70
    assert kill_xp(20, 1, cfg, "off") == 5
    assert kill_xp(1, 20, cfg, "off") == 400


def test_level_up_carries_remainder_and_does_not_refill_blood_or_stamina():
    state = arena()
    fighter = state.fighters[0]
    fighter.xp, fighter.stamina, fighter.blood = 90, 40, 60
    award_xp(state, fighter, 30, guided="hints")
    assert (fighter.level, fighter.xp) == (2, 20)
    assert fighter.stamina == 40 and fighter.blood == 60
    assert any(e.type == EventType.LEVEL_UP for e in state.event_log)
    log = next(e for e in state.event_log if e.type == EventType.XP_GAIN)
    assert log.deltas["guided"] and log.deltas["guide_mode"] == "hints"


def test_xp_cannot_exceed_level_cap_or_accept_negative_or_nonfinite_awards():
    state = arena()
    fighter = state.fighters[0]
    award_xp(state, fighter, 1_000_000, guided="off")
    assert fighter.level == 20
    for amount in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            award_xp(state, fighter, amount, guided="off")
    with pytest.raises(ValueError):
        award_xp(state, fighter, "not xp", guided="off")


def test_live_victory_reward_is_once_and_uses_guide_mode_at_victory():
    from pirates.duel_system.engine import check_end_conditions
    session = DuelSession.new(guide_mode="full")
    session.guide_mode = "off"
    session.enemy.unconscious = True
    check_end_conditions(session.state)
    session.settle_victory()
    assert (session.player.level, session.player.xp) == (2, 0)
    assert next(e for e in session.state.event_log if e.type == EventType.XP_GAIN).deltas["xp"] == 100
    session.settle_victory()
    assert (session.player.level, session.player.xp) == (2, 0)
    restored = DuelSession.from_data(session.to_data())
    restored.settle_victory()
    assert (restored.player.level, restored.player.xp) == (2, 0)


@pytest.mark.parametrize("mode,expected", [("off", 100), ("hints", 85), ("full", 70)])
def test_live_victory_applies_exact_guide_xp_discount(mode, expected):
    from pirates.duel_system.engine import check_end_conditions
    session = DuelSession.new(guide_mode=mode)
    session.enemy.yielded = True
    check_end_conditions(session.state)
    session.settle_victory()
    reward = next(e for e in session.state.event_log if e.type == EventType.XP_GAIN)
    assert reward.deltas["xp"] == expected
    assert reward.deltas["guided"] is (mode != "off")
    assert reward.deltas["guide_mode"] == mode


def test_flee_or_player_defeat_never_rewards_xp():
    from pirates.duel_system.engine import check_end_conditions
    for defeated in ("player", "enemy_fled"):
        session = DuelSession.new(guide_mode="off")
        if defeated == "player":
            session.player.unconscious = True
        else:
            session.enemy.fled = True
        check_end_conditions(session.state)
        session.settle_victory()
        assert session.player.xp == 0


def test_fear_yield_on_automatic_tick_settles_winner_xp_once():
    session = DuelSession.new(guide_mode="hints")
    session.enemy.morale, session.enemy.anger, session.enemy.fear_decisions = 0, 0, 2
    session.step()
    assert session.state.end_reason == "yield"
    assert session.player.xp == 85
    session.step()
    assert session.player.xp == 85


def test_retry_preserves_earned_progress_but_resets_fight_pools():
    session = DuelSession.new(guide_mode="off")
    session.player.level, session.player.xp = 3, 40
    session.player.blood = 20
    retry = session.restart()
    assert (retry.player.level, retry.player.xp) == (3, 40)
    assert retry.player.blood == 100
