"""M10 harness integrity: real simulations, seeded bots and honest target gates."""
from pirates.duel_system.balance import assess, run_fight, run_matchup
from pirates.duel_system.balance import _guard_release, choose_bot
from pirates.duel_system.config import load_config
from pirates.duel_system.engine import Choice, commit_action
from pirates.duel_system.model import ActionKind, Zone


def test_seeded_full_fight_is_reproducible_and_resolves_with_real_events():
    first = run_fight("SpamLightBot", "Ronin", seed=17)
    second = run_fight("SpamLightBot", "Ronin", seed=17)
    assert first == second
    assert first["ticks"] > 0 and first["decisions"] > 0
    assert first["events"] > 0 and first["event_sha256"]
    assert first["reason"] != "timeout"


def test_matchup_reports_counts_end_reasons_length_and_config_fingerprint():
    report = run_matchup("SpamHeavyBot", "Ronin", fights=3, seed=20)
    assert report["fights"] == 3
    assert report["wins"] + report["losses"] + report["draws"] == 3
    assert sum(report["end_reasons"].values()) == 3
    assert report["median_player_decisions"] > 0
    assert report["config_sha256"]


def test_under_sampled_or_missing_matchups_cannot_claim_balance_acceptance():
    report = run_matchup("ScriptedPunisherBot", "Ronin", fights=2, seed=1)
    assessment = assess({"punisher": report}, load_config())
    assert assessment["passed"] is False
    assert "sample_size" in assessment["failed"]
    assert "missing_matchups" in assessment["failed"]


def test_mirror_seat_swaps_are_explicit_and_counted_not_fabricated_draws():
    report = run_matchup("ScriptedPunisherBot", "ScriptedPunisherBot", fights=4, seed=10, mirror=True)
    assert report["seat_counts"] == {"first": 2, "second": 2}
    assert report["wins"] + report["losses"] + report["draws"] == 4


def test_scripted_punisher_uses_visible_rps_against_light_windup():
    from pirates.duel_system.balance import make_fight
    import random
    config = load_config()
    config["ai"]["reaction_delay_default"] = 0
    state = make_fight("ScriptedPunisherBot", "Ronin", seed=5, config=config)
    punisher, ronin = state.fighters
    punisher.pos, ronin.pos = (4, 3), (5, 3)
    commit_action(state, ronin.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    state.tick = ronin.action.start_tick  # zero-delay observation, two ticks before the light lands.
    choice = choose_bot(state, punisher.id, "ScriptedPunisherBot", random.Random(1))
    assert choice.kind == ActionKind.GUARD


def test_scripted_punisher_does_not_read_unrevealed_action_commitment():
    import random
    from pirates.duel_system.balance import make_fight
    state = make_fight("ScriptedPunisherBot", "Ronin", seed=5, config=load_config())
    plain = state.clone()
    punisher, ronin = state.fighters
    commit_action(state, ronin.id, Choice(ActionKind.LIGHT, Zone.TORSO))
    # Before reaction_delay, adding a private commitment cannot alter the bot's choice.
    hidden_choice = choose_bot(state, punisher.id, "ScriptedPunisherBot", random.Random(1))
    plain_choice = choose_bot(plain, plain.fighters[0].id, "ScriptedPunisherBot", random.Random(1))
    assert hidden_choice == plain_choice


def test_scripted_punisher_interrupts_a_visible_heavy_when_light_wins_the_tick_race():
    import random
    from pirates.duel_system.balance import make_fight
    state = make_fight("ScriptedPunisherBot", "Ronin", seed=9, config=load_config())
    punisher, ronin = state.fighters
    punisher.pos, ronin.pos = (4, 3), (5, 3)
    commit_action(state, ronin.id, Choice(ActionKind.HEAVY, Zone.TORSO))
    state.tick = state.config["ai"]["reaction_delay_default"]
    choice = choose_bot(state, punisher.id, "ScriptedPunisherBot", random.Random(1))
    assert choice.kind == ActionKind.LIGHT


def test_scripted_punisher_selects_leg_against_idle_target_to_slow_them():
    import random
    from pirates.duel_system.balance import make_fight
    state = make_fight("ScriptedPunisherBot", "Ronin", seed=9, config=load_config())
    punisher, ronin = state.fighters
    punisher.pos, ronin.pos = (4, 3), (5, 3)
    choice = choose_bot(state, punisher.id, "ScriptedPunisherBot", random.Random(1))
    assert choice.kind == ActionKind.LIGHT
    assert choice.aim == Zone.LEG


def test_scripted_punisher_uses_heavy_to_punish_a_long_stagger_opening():
    import random
    from pirates.duel_system.balance import make_fight
    state = make_fight("ScriptedPunisherBot", "Ronin", seed=9, config=load_config())
    punisher, ronin = state.fighters
    punisher.pos, ronin.pos = (4, 3), (5, 3)
    ronin.stagger_until = state.tick + 20
    choice = choose_bot(state, punisher.id, "ScriptedPunisherBot", random.Random(1))
    assert choice.kind == ActionKind.HEAVY
    assert choice.aim in {Zone.HAND, Zone.LEG}


def test_scripted_punisher_rests_when_low_vigor_and_safely_out_of_range():
    import random
    from pirates.duel_system.balance import make_fight
    state = make_fight("ScriptedPunisherBot", "Ronin", seed=9, config=load_config())
    punisher, ronin = state.fighters
    punisher.pos, ronin.pos = (4, 3), (8, 3)
    punisher.fatigue = 75
    punisher.stamina = 10
    choice = choose_bot(state, punisher.id, "ScriptedPunisherBot", random.Random(1))
    assert choice.kind == ActionKind.WAIT


def test_harness_guard_release_does_not_extend_for_hidden_heavy_commitment():
    from pirates.duel_system.balance import make_fight
    config = load_config()
    config["ai"]["reaction_delay_default"] = 20
    state = make_fight("ScriptedPunisherBot", "Ronin", seed=11, config=config)
    punisher, ronin = state.fighters
    commit_action(state, punisher.id, Choice(ActionKind.GUARD))
    commit_action(state, ronin.id, Choice(ActionKind.HEAVY, Zone.TORSO))
    state.tick = punisher.action.guard_ready_tick + punisher.action.parry_window
    _guard_release(state, punisher, "ScriptedPunisherBot")
    assert punisher.action.released
