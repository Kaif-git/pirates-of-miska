"""Cheap, observation-only strategy coaching. No simulation, hidden AI or RNG."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Coaching:
    action: str
    headline: str
    advantages: tuple[str, ...]
    cautions: tuple[str, ...]
    evidence: str
    confidence: str = "Unknown — no named-NPC observations in similar conditions."


def coach(model, summary=()):
    own, learned, observed, rules = map(dict, (model.own_readout, model.enemy_readout, model.enemy_observed, model.combat_rules))
    if not own:
        return Coaching("", "Observe a few exchanges first.", (), (), "No mechanical readout available.")
    pools = {key: value for key, value, _ in model.pools}
    enemy = learned or observed
    source = "Learned current stats" if learned else "Last observed commitment/strike; may have changed" if observed else "Opponent stats not learned yet"
    confidence = model.enemy_confidence
    advantages, cautions = [], ["Next enemy choice is unknown. Advice is conditional, not a guaranteed counter."]
    cautions.extend(f"Your injury: {row}" for row in model.player_injury_rows[:2])
    if model.group_combat:
        if any(word in model.player_state for word in ("DEFEAT", "VICTORY", "FLED")):
            return Coaching("", "Group encounter over. Inspect the witnessed outcomes; off-screen actors remain unknown.",
                            (), tuple(cautions), "Encounter terminal record", "Terminal outcome recorded.")
        if model.player_action != "IDLE":
            return Coaching("", "Your action is committed; wait for its result. Other actors do not grant a second action.",
                            (), tuple(cautions + ["Group commitments resolve on the shared tick timeline."]),
                            "Current player commitment", "Committed action is certain; other choices remain unknown.")
        threats = model.target_options
        if threats:
            advantages.append("Visible hostiles: " + "; ".join(
                f"[{actor.glyph}] {actor.name} at about {actor.distance} tiles ({actor.posture})"
                for actor in model.threats))
            if model.threat_overflow:
                advantages.append(f"{model.threat_overflow} additional visible hostile(s) are beyond the short summary.")
            cautions.append("Choose a visible hostile explicitly; attacks stay locked to that target and can be obstructed.")
        else:
            advantages.append("No living hostile is currently visible; stale hearing is not a legal attack target.")
        cautions.append("Off-screen actor positions, conditions, and queued choices are not included in this advice.")
        return Coaching("Choose target" if threats else "Wait",
                        "Group view: assess the nearest visible threats before committing; no single-opponent rollout is used.",
                        tuple(advantages), tuple(cautions),
                        "Current sight, public posture and witnessed events only",
                        "Visible actors only; unobserved hostiles remain unknown.")
    for key in ("Speed", "Accuracy", "Strength", "Endurance"):
        if key in enemy:
            relation = ">" if own[key] > enemy[key] else "<" if own[key] < enemy[key] else "="
            advantages.append(f"{key}: you {own[key]:.1f} {relation} opponent {enemy[key]:.1f} ({'current' if learned else 'last seen'}).")
    if not enemy:
        advantages.append(f"Your Accuracy {own['Accuracy']:.1f}: compare target requirements before aiming at limbs.")
    if learned:
        advantages.append(f"Light windup: you {own['light_ticks']:g}t, opponent {learned['light_ticks']:g}t. Equal ticks can trade.")
    cautions += list(model.functional_alerts[:2])
    if any(word in model.player_state for word in ("DEFEAT", "VICTORY", "FLED")):
        return Coaching("", "Fight over. Inspect the trade, then retry or leave.", tuple(advantages), tuple(cautions), source, confidence)
    if model.player_grounded:
        if model.player_action != "IDLE":
            action = model.player_action.replace("_", " ").title()
            return Coaching("", f"Your {action} is committed; wait for its result. A hit can interrupt Rise.",
                            tuple(advantages), tuple(cautions + ["Grounded actions do not grant a second action."]),
                            source, confidence)
        legal = {option.label for option in model.actions if option.enabled}
        if not legal:
            return Coaching("", "You cannot commit while control is interrupted; wait for control to return.",
                            tuple(advantages), tuple(cautions + ["You remain grounded; no attack is available."]),
                            source, confidence)
        attack = model.enemy_action in {"LIGHT", "HEAVY", "DASH_LIGHT"}
        cover_window = own.get("guard_ticks", 3) + own.get("parry_window", 0)
        if attack and model.enemy_remaining is not None and model.enemy_remaining <= cover_window and "Cover" in legal:
            action = "Cover"
            reason = "A visible committed attack is close; Cover may defend but is not guaranteed."
        elif "Rise" in legal:
            action = "Rise"
            reason = "Attempt the timed, exposed Rise; a qualifying hit can interrupt it and leave you grounded."
        elif "Crawl" in legal:
            action = "Crawl"
            reason = "Crawl one clear tile to reposition; it is slow and you remain grounded."
        else:
            action = "Cover"
            reason = "Cover is the only available grounded choice; it does not move or attack."
        cause = next((row for row in reversed(model.full_log) if "fell from" in row),
                     "Stability reached zero; you are grounded.")
        cautions.extend(("Grounded: only Cover, Crawl or Rise; attacks and standing movement are illegal.",
                         "Rise is exposed and interruptible; Crawl is slow, collision-checked and grants no attack."))
        return Coaching(action, f"{cause} {reason}", tuple(advantages), tuple(cautions), source, confidence)
    if own.get('exhausted') or pools.get('STA', 0) < own['light_cost']:
        return Coaching("Move" if model.gap <= 3 else "Wait",
                        "Create distance before resting; your stamina cannot fund another clean attack.",
                        tuple(advantages), tuple(cautions+["Walking can be hit; never treat retreat or rest as invincible." ]), source, confidence)
    if pools.get('BLD', 100) < 35 and own.get('unbandaged', 0) and own.get('bandages', 0):
        return Coaching("Bandage" if model.gap > 3 else "Dash",
                        "Bleeding is winning the fight: seek space, then bandage.", tuple(advantages),
                        tuple(cautions+["Bandaging is slow and interruptible; a distant enemy may still close the gap."]), source, confidence)
    if model.gap > 0:
        if pools.get('FAT', 0) > 35 and own['Speed'] < .8*own['fresh_Speed']:
            action = ('Eat' if own.get('food') else 'Drink' if own.get('water') else 'Wait') if model.gap > 3 else 'Move'
            return Coaching(action, 'Your condition is erasing your speed edge: make space, then recover fatigue.',
                            tuple(advantages), tuple(cautions+['Food/drink and rest take an exchange; the opponent can close. Recovery does not heal injuries or replace blood.']), source, confidence)
        return Coaching("Move", "Close deliberately; do not swing outside reach.", tuple(advantages),
                        tuple(cautions+["Choose a legal direction toward the opponent. A dash costs stamina and grants no invincibility."]), source, confidence)
    # A repeated-light suggestion needs an actual observed commitment, not the
    # NPC's archetype, private coefficients, queued choice or future random draw.
    arm = next((zone for zone in model.zones if zone.zone.value == 'ARM'), None)
    arm_cut = own['light_power']-dict(model.enemy_armor).get('ARM', 0) >= rules['cut']
    if own.get('riposte'):
        target = 'Arm' if arm and arm_cut else 'Torso'
        return Coaching('Light', f"Use your riposte: Light → {target} has a shortened {own['light_ticks']:g}-tick windup.",
                        tuple(advantages), tuple(cautions+['A riposte is not a free hit. The enemy can guard or reposition; check reach.']), source, confidence)
    if observed.get('plain_light') and 'Strength' in enemy:
        need = rules['parry_light']*enemy['Strength']
        arrival = learned.get('light_ticks', observed.get('arrival', -1))
        window_fits = own['guard_ticks'] <= arrival < own['guard_ticks']+own['parry_window']
        if own['Strength'] >= need and window_fits and not own.get('disarmed'):
            advantages.append(f"If light repeats: guard ready {own['guard_ticks']:g}t, hit ~{arrival:g}t; Strength {own['Strength']:.1f} ≥ {need:.1f} needed.")
            return Coaching("Guard", "Try Guard if light repeats; your parry window and strength fit that observed attack.",
                            tuple(advantages), tuple(cautions+["Leg/hand attacks bypass the guard line. Heavy attacks need more parry strength; the opponent can change tactics."]), source, confidence)
    if pools.get('FAT', 0) > 35 and own['Speed'] < .8*own['fresh_Speed']:
        action = 'Dash' if pools.get('STA', 0) >= own['dash_cost'] and not own.get('no_dash') else 'Move'
        return Coaching(action, 'Your speed edge is fading: try to disengage before resting or using supplies.',
                        tuple(advantages), tuple(cautions+['Retreat can be hit; walking is especially slow. A wounded leg may prevent dashing.']), source, confidence)
    if arm and arm_cut:
        return Coaching("Light", "Try Light → Arm: a sufficient wound can weaken striking force; Accuracy shapes clean-contact odds, not legal targeting.",
                         tuple(advantages+[dict(model.target_effects).get('ARM', '')]),
                         tuple(cautions+["Arm is in the guard line, but attacks can bypass or break guard. It only weakens on a sufficient wound; the enemy may still strike back."]), source, confidence)
    headline = ("Your light cannot cause a disabling arm cut through this armor; seek an opening or disengage."
                if not arm_cut else "Aim variation can drift or miss; choose a useful exposed site and keep a safe response ready.")
    return Coaching("Move" if own.get('disarmed') else "Guard", headline,
                    tuple(advantages), tuple(cautions+["Guard can be bypassed or broken. Look for a safer exchange or retreat; no winning route is guaranteed."]), source, confidence)


def strategy_rows(model, advice):
    if model.group_combat:
        return ("GROUP STRATEGY COACH", "CONDITIONAL NOTE: " + advice.headline,
                "EVIDENCE: " + advice.evidence, "CONFIDENCE: " + advice.confidence,
                "VISIBLE THREATS", *advice.advantages, "RISKS", *advice.cautions,
                "Commit once per participant per exchange. Faster actors do not gain extra actions.",
                "Targets do not auto-switch; bodies, walls and occupied movement destinations can block.",
                "No universal one-versus-many win claim is implied.", "[Back]")
    rules = dict(model.combat_rules)
    return ("STRATEGY COACH", "CONDITIONAL OPTION: "+advice.headline,
            "EVIDENCE: "+advice.evidence, "CONFIDENCE: "+advice.confidence,
            "YOUR ADVANTAGES / DISADVANTAGES", *advice.advantages,
            "RISKS", *advice.cautions, "WHAT YOUR CONDITION CHANGES", *model.functional_alerts,
            "HOW TO PLAN AN EXCHANGE",
            "1. Compare both hits, not just whether both connected. Trading can favor the stronger fighter.",
            (f"2. After-armor impact ≥ {rules['deep']:g} is deep. Aim controls WHERE; Strength/action/weapon/armor control severity."
             if 'deep' in rules else "2. Compare after-armor impact against the wound thresholds in Details."),
            "Impact is not instant blood damage: wounds create bleeding, pain and zone-specific penalties. A deep torso wound also drains stamina immediately.",
            "3. Arm cuts reduce force; leg cuts slow future movement/windups; severe head contact can cause concussion or unconsciousness. Accuracy stays unchanged; changes do not reschedule an already committed action.",
            "4. Speed is rounded into phase ticks: a speed advantage can still tie. Same-tick strikes can trade; faster never means extra attacks.",
            "5. Precision uses a requirement plus small seeded variation. Covered hands can force drift; torso is the fallback, not a general dodge roll.",
            "6. Stamina funds actions. Fatigue, pain, blood loss and injuries weaken effective stats. Watch actual windup/aim warnings, not bars alone.",
            "7. Rest when you can afford exposure. Food/drink reduce fatigue; bandages reduce bleeding but do not restore lost blood or erase wounds.",
            "8. Observe repeated choices, test a conditional counter, then verify the outcome. One observed action is not a reliable habit.",
            "NOT EVERY MATCHUP HAS A WINNING COUNTER",
            "There is no universal stat-ratio cutoff. Check parry strength AND timing, whether you can beat armor, precision requirements and room to escape.",
            "Retreat is valid. An all-round stat/gear disadvantage can remove viable options; this coach does not claim a guaranteed win.",
            "YOUR CURRENT INJURIES", *(model.player_injury_rows or ("No localized wounds.",)),
            "Depth follows post-armor impact; structure and reflex are recorded per hit. Reflex never grants an attack.",
            "YOUR EMOTIONS", *model.player_emotion_rows,
            "PUBLIC READ OF THIS OPPONENT", *model.enemy_tell,
            "A tendency is evidence, not a promise; his next decision is unknown.", "[Back]")
