from dataclasses import replace

from pirates.duel_coach import coach
from pirates.duel_system.engine import Choice, commit_action, advance_one_tick
from pirates.duel_system.model import ActionKind, Zone
from pirates.duel_system.session import DuelSession
from pirates.duel_ui import DuelUI


def test_coach_toggle_has_no_effect_on_combat_rng_or_guide_xp_setting():
    ui = DuelUI(DuelSession.new(guide_mode='off'), rounds=True)
    before = ui.session.to_data()
    ui.activate('Coach')
    assert ui.coach_enabled
    ui.activate('Strategy')
    assert ui.sidebar_rows(ui.model())[0] == 'STRATEGY COACH'
    ui.activate('Back')
    assert ui.session.to_data() == before
    assert ui.session.guide_mode == 'off'


def test_first_advice_uses_only_own_visible_strengths_and_unknown_opponent():
    ui = DuelUI(DuelSession.new(guide_mode='off'), rounds=True)
    model = ui.model()
    assert not model.enemy_readout and not model.enemy_observed
    hint = coach(model)
    assert hint.action == 'Move'
    assert 'not learned' in hint.evidence
    assert 'unknown' in ' '.join(hint.cautions)
    assert hint.confidence.startswith('Unknown')


def test_grounded_coach_chooses_only_a_visible_legal_recovery_action():
    session = DuelSession.new(guide_mode='off')
    session.player.stability, session.player.grounded, session.player.grounded_since = 0, True, 0
    ui = DuelUI(session, rounds=True)
    model = ui.model()
    hint = coach(model)
    assert hint.action in {option.label for option in model.actions if option.enabled}
    assert hint.action == 'Rise'
    assert 'exposed' in hint.headline.lower() and 'grounded' in ' '.join(hint.cautions).lower()
    assert 'no attack' in ' '.join(hint.cautions).lower()


def test_coach_confidence_uses_only_named_npc_observations_for_current_public_conditions():
    from pirates.duel_coach import strategy_rows
    from pirates.duel_system.ai import FullKey
    from pirates.duel_system.guide import _public_key

    session = DuelSession.new(guide_mode='off')
    key = _public_key(session.state, session.enemy)
    full = FullKey(key, 'OK', 'BOLD', 'LOW')
    for tick in range(5):
        session.notebook.record('brute', session.enemy.id, key, 'HEAVY',
                                tick=tick, full_key=full)
    model = DuelUI(session, rounds=True).model()
    advice = coach(model)
    rows = strategy_rows(model, advice)
    assert advice.confidence.startswith('Strong tendency, not certainty')
    assert any(row.startswith('CONDITIONAL OPTION:') for row in rows)
    assert any(row.startswith('EVIDENCE:') for row in rows)
    assert any(row.startswith('CONFIDENCE:') for row in rows)
    assert 'RISKS' in rows


def test_coach_cannot_change_when_only_hidden_ai_or_rng_changes():
    session = DuelSession.new(guide_mode='off')
    ui = DuelUI(session, rounds=True)
    first = coach(ui.model())
    session.enemy.personality.fear_gain_susceptibility = 1.5
    session.enemy.personality.stress_composure = 0
    session.enemy.morale, session.enemy.pain = 0, 100
    session.state.rng_seed = 98765
    assert coach(ui.model()) == first


def test_degradation_shows_consequences_not_just_a_percentage():
    ui = DuelUI(DuelSession.new(guide_mode='off'), rounds=True)
    ui.session.player.fatigue = 75
    ui.session.player.pain = 30
    model = ui.model()
    values = dict(model.own_readout)
    assert values['light_ticks'] > values['fresh_light_ticks']
    assert values['Accuracy'] == values['fresh_Accuracy']
    assert any('windup' in alert for alert in model.functional_alerts)
    assert not any('aim:' in alert for alert in model.functional_alerts)


def test_recap_reports_both_choices_and_physical_consequences_without_trade_score():
    session = DuelSession.new(guide_mode='off')
    session.enemy.pos = (session.player.pos[0]+1, session.player.pos[1])
    # Freeze the NPC choice for this controlled example, not a live AI promise.
    session._npc_choice = lambda: Choice(ActionKind.LIGHT, Zone.TORSO)
    ui = DuelUI(session, rounds=True)
    ui.activate('Light')
    ui.activate('Torso')
    text = '\n'.join(ui.round_result)
    assert 'You chose:' in text and 'Ronin chose:' in text
    assert any(term in text.lower() for term in
               ('cut', 'deep', 'missed', 'parried', 'blocked', 'weapon paths intersected'))
    assert not any(term in text.lower() for term in ('damage trade', 'net damage', 'power - armor'))


def test_observed_light_generates_conditional_parry_not_future_certainty():
    session = DuelSession.new(guide_mode='off')
    session.enemy.pos = (session.player.pos[0]+1, session.player.pos[1])
    commit_action(session.state, session.enemy.id,
                  Choice(ActionKind.LIGHT, Zone.TORSO, target_id=session.player.id))
    ui = DuelUI(session, rounds=True)
    hint = coach(ui.model())
    assert hint.action in {'Guard', 'Light'}
    assert 'Next enemy choice is unknown.' in ' '.join(hint.cautions)
    assert 'bypass' in ' '.join(hint.cautions)


def test_successful_parry_teaches_the_real_riposte_opportunity():
    session = DuelSession.new(guide_mode='off')
    session.enemy.pos = (session.player.pos[0]+1, session.player.pos[1])
    session._npc_choice = lambda: Choice(ActionKind.LIGHT, Zone.TORSO)
    ui = DuelUI(session, rounds=True)
    ui.activate('Defend')
    hint = coach(ui.model())
    assert hint.action == 'Light' and 'riposte' in hint.headline
    from pirates.duel_system.render_model import preview_action
    preview = preview_action(session.state, Choice(ActionKind.LIGHT, Zone.TORSO))
    assert preview.strike_tick-session.state.tick == dict(ui.model().own_readout)['light_ticks']


def test_unlearned_coach_uses_recorded_evidence_not_secret_current_stats():
    session = DuelSession.new(guide_mode='off')
    session.enemy.pos = (session.player.pos[0]+1, session.player.pos[1])
    session._npc_choice = lambda: Choice(ActionKind.LIGHT, Zone.TORSO)
    ui = DuelUI(session, rounds=True)
    ui.activate('Wait')
    before = coach(ui.model())
    session.enemy.strength = session.enemy.speed = session.enemy.accuracy = 999
    assert not ui.model().enemy_readout
    assert coach(ui.model()) == before


def test_heavy_target_picker_does_not_modify_permanent_accuracy_requirement():
    ui = DuelUI(DuelSession.new(guide_mode='off'), rounds=True)
    ui.activate('Light')
    light = {z.zone: z.difficulty for z in ui.model().zones}
    ui.activate('Back')
    ui.activate('Heavy')
    for zone in ui.model().zones:
        assert zone.difficulty == light[zone.zone]


def test_fresh_reference_and_readout_never_mutate_state():
    session = DuelSession.new(guide_mode='off')
    session.player.pain, session.player.fatigue = 40, 70
    before = session.to_data()
    model = DuelUI(session, rounds=True).model()
    assert model.functional_alerts
    assert session.to_data() == before


def test_coach_never_promises_a_disabling_cut_when_armor_prevents_it():
    from pirates.duel_system.model import ArmorPiece
    session = DuelSession.new(guide_mode='off')
    session.enemy.pos = (session.player.pos[0]+1, session.player.pos[1])
    session.enemy.equipment.armor[Zone.ARM] = ArmorPiece(value=100, weight=0)
    hint = coach(DuelUI(session, rounds=True).model())
    assert 'cannot cause a disabling arm cut' in hint.headline
    assert hint.action != 'Light'


def test_coach_preference_survives_real_world_save_load(tmp_path):
    from pirates.game import World
    world = World()
    session = DuelSession.new(guide_mode='off')
    world.duel_journal = session.to_data()
    world.duel_journal['coach_enabled'] = True
    path = tmp_path/'coach-save.json'
    world.save(path)
    restored = World.load(path)
    assert restored.duel_journal['coach_enabled'] is True
    assert DuelSession.from_data(restored.duel_journal).state.to_data() == session.state.to_data()
