"""Explain completed exchanges using recorded resolution facts, never guesses."""


DIRECTIONS = {(0, -1): "north", (1, -1): "northeast", (1, 0): "east", (1, 1): "southeast",
              (0, 1): "south", (-1, 1): "southwest", (-1, 0): "west", (-1, -1): "northwest"}


def describe_choice(data):
    label = data["kind"].replace("_", " + ").title()
    if data.get("aim"):
        label += f" at {data['aim'].lower()}"
    if data.get("direction"):
        tiles = data.get("tiles", 1)
        label += f", {tiles} tile{'s' if tiles != 1 else ''} {DIRECTIONS.get(tuple(data['direction']), str(data['direction']))}"
    return label


def injury_numbers(event, private_actor_ids=(), *, compact=False):
    """Describe a resolved wound using recorded force, thresholds and consequences."""
    data = event.deltas
    if event.actor in private_actor_ids or event.target in private_actor_ids:
        return ""
    context = data.get("injury_numbers", {})
    net = data.get("net")
    if net is None:
        return ""
    def number(value):
        if value is None:
            return "?"
        return f"{float(value):.1f}".rstrip("0").rstrip(".")
    bands = context.get("bands", {})
    threshold_text = (f"per-hit bands: graze {bands['graze_min']:g}–{bands['cut_min']-1:g}; "
                      f"cut {bands['cut_min']:g}–{bands['deep_min']-1:g}; deep ≥{bands['deep_min']:g} wound-force points"
                      if "graze_min" in bands and "cut_min" in bands and "deep_min" in bands else "")
    if context.get("next_label") and context.get("net_to_next") is not None:
        next_text = (f"{context['next_label']} starts at {number(context['next_net_threshold'])} points "
                     f"(+{number(context['net_to_next'])} on one hit)")
    elif net >= bands.get("deep_min", float("inf")):
        next_text = "deep band reached"
    else:
        next_text = ""
    # Net is the resolved post-armor impact. Keep the recap decision-useful without
    # turning the player-facing summary into a raw calculation ledger.
    power, armor = data.get("power"), data.get("armor_applied")
    armor_text = (f"attack {number(power)} − {event.zone.value.lower()} protection {number(armor)}"
                  if power is not None and armor is not None else "post-armor")
    impact = f"wound force {number(net)} points ({armor_text})"
    if context.get("special"):
        specials = []
        for item in context["special"]:
            if item["label"] == "lasting eye injury" and "trauma_current" in item:
                current = data.get("eye_trauma_after", item["trauma_current"])
                before = data.get("eye_trauma_before", item["trauma_current"])
                before_eyes = data.get("eyes_damaged_before", 0)
                eyes = data.get("eyes_damaged_after")
                if eyes is not None and eyes > before_eyes:
                    special_text = (f"Clean eye contact reached {number(item['net_threshold'])} post-armor force: "
                                    f"eye {eyes}/2 impaired; a later clean CUT-or-worse contact can impair the other eye")
                elif current >= item["net_threshold"] and before_eyes < 2:
                    special_text = (f"eye-trauma threshold already banked ({number(current)} points; "
                                    f"threshold {number(item['net_threshold']):g}); next eligible clean eye wound "
                                    f"permanently impairs eye {before_eyes+1}/2")
                else:
                    special_text = (f"eye trauma {number(current)} points; a clean CUT-or-worse contact "
                                    f"reaches the {number(item['net_threshold'])}-point impairment threshold")
                specials.append(special_text)
            elif item["net_threshold"] > net:
                specials.append(f"{item['label']} ≥{number(item['net_threshold'])} (+{number(item['net_threshold']-net)})")
            else:
                specials.append(f"{item['label']} threshold {item['net_threshold']:g} reached")
        if specials:
            next_text = "; ".join(part for part in (next_text, *specials) if part)
    if compact:
        return f"{impact}; {next_text or threshold_text}".rstrip("; ")
    rows = [f"Wound force: {number(net)} points after zone protection ({armor_text}); "
            f"{threshold_text or 'threshold bands not recorded'}"
            + (f"; next: {next_text}" if next_text else ".")]
    effects = []
    numeric = data.get("numeric_effects", {})
    for key, label in (("strength_mult", "Strength"), ("speed_mult", "Speed"),
                       ("accuracy_mult", "Accuracy")):
        if key in numeric:
            effects.append(f"{label} ×{number(numeric[key])}")
    if "parry_penalty" in numeric:
        effects.append(f"parry penalty +{number(numeric['parry_penalty'])}")
    if numeric.get("duration_ticks"):
        effects.append(f"{number(numeric['duration_ticks'])} ticks")
    effects.append(f"bleed {number(data.get('bleed_per_turn', 0))} blood/turn")
    if data.get("initial_blood_loss", 0) > 0:
        effects.append(f"immediate blood loss {number(data['initial_blood_loss'])}")
    effects.append(f"pain +{number(data.get('pain', 0))}")
    rows.append("Injury numbers: " + "; ".join(effects) + ".")
    return "\n".join(rows)


def action_stat_comparison(hit, choices, names, private_actor_ids=()):
    """Explain the committed numerical matchup without inventing a stat contest."""
    if hit.actor in private_actor_ids or hit.target in private_actor_ids:
        return ""
    own, other = choices.get(hit.actor), choices.get(hit.target)
    if own is None or other is None:
        return ""
    own_stats, other_stats = own.deltas, other.deltas
    actor = names.get(hit.actor, hit.actor)
    target = names.get(hit.target, hit.target)
    fields = []
    for key, label in (("speed", "Speed"), ("strength", "Strength"), ("accuracy", "Accuracy")):
        if key in own_stats and key in other_stats:
            delta = own_stats[key] - other_stats[key]
            fields.append(f"{label} {actor} {own_stats[key]:.1f} vs {target} {other_stats[key]:.1f} ({delta:+.1f})")
    own_tick, other_tick = own_stats.get("strike_tick"), other_stats.get("strike_tick")
    if own_tick is not None and other_tick is not None:
        delta = other_tick - own_tick
        if delta > 0:
            fields.append(f"impact due t{own_tick} vs t{other_tick}; {actor} had a {delta}-tick timing edge; "
                          "the later commitment can still resolve unless an INTERRUPT is recorded")
        elif delta < 0:
            fields.append(f"impact due t{own_tick} vs t{other_tick}; {target} was scheduled {-delta} tick(s) earlier; "
                          "check its separate HIT/WHIFF/CLASH outcome")
        else:
            fields.append(f"both impacts due t{own_tick}; same-tick strike checks precede movement, "
                          "so both can land unless a clash or interruption prevents one")
    if not fields:
        return ""
    return ("Action comparison: " + "; ".join(fields)
            + "; ordinary body contact is not a single-stat contest.")


def condition_change_line(event, names, private_actor_ids=()):
    if event.target in private_actor_ids:
        return ""
    before = event.deltas.get("target_condition_before")
    after = event.deltas.get("target_condition_after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return ""
    fields = []
    for key, label in (("pain", "Pain"), ("effective_pain", "Effective pain"),
                       ("fear", "Fear"), ("anger", "Anger"), ("morale", "Morale"),
                       ("blood", "Blood"), ("stamina", "Stamina"), ("fatigue", "Fatigue"),
                       ("stability", "Stability"), ("strength", "Effective Strength"),
                       ("speed", "Effective Speed"), ("accuracy", "Effective Accuracy")):
        start, end = before.get(key), after.get(key)
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            fields.append(f"{label} {start:.1f}→{end:.1f} ({end-start:+.1f})")
    modes_before, modes_after = before.get("behavior_modes", {}), after.get("behavior_modes", {})
    for key, label in (("pain", "Pain mode"), ("fear", "Fear mode"), ("anger", "Anger mode")):
        if modes_before.get(key) != modes_after.get(key):
            fields.append(f"{label} {modes_before.get(key, '?')}→{modes_after.get(key, '?')}")
    target = names.get(event.target, event.target)
    return f"{target} condition from this hit: " + "; ".join(fields) + "." if fields else ""


def summarize_exchange(events, names, *, deep_threshold=None, private_actor_ids=()):
    """Compact choices, bodily outcomes and causal explanation; never score damage."""
    choices = {event.actor: event for event in events if event.type.value == "ACTION_START"}
    def who(actor):
        return names.get(actor, actor or "Fight")
    def short_choice(data):
        label = data["kind"].replace("_", "+").title()
        if data.get("aim"):
            label += f" → {data['aim'].title()}"
        if data.get("direction"):
            compass = {value: key for key, value in
                       {"N": (0, -1), "NE": (1, -1), "E": (1, 0), "SE": (1, 1),
                        "S": (0, 1), "SW": (-1, 1), "W": (-1, 0), "NW": (-1, -1)}.items()}
            label += f" · {data.get('tiles', 1)} {compass.get(tuple(data['direction']), '?')}"
        return label
    def stat_comparison(hit):
        return action_stat_comparison(hit, choices, names, private_actor_ids)

    def condition_delta(hit):
        return condition_change_line(hit, names, private_actor_ids)
    outcomes, checks, condition_changes = [], [], []
    for event in events:
        kind, data = event.type.value, event.deltas
        actor, target = who(event.actor), who(event.target)
        if kind == "WHIFF":
            outcomes.append(f"{actor} missed.")
            moved_before = next((move for move in events
                                 if move.type.value == "MOVE" and move.actor == event.target
                                 and move.tick < event.tick), None)
            if "AIM_DISPERSION_MISS" in event.cause_tags:
                reason = (f"Aimed at {data.get('aimed_zone', 'target').lower()}; seeded dispersion left no clean or "
                          f"nearby-site contact (clean chance {data.get('clean_probability', 0):.0%}).")
                checks.append((75, "Aim: " + reason))
            elif "OUTSIDE_LOCKED_ATTACK_GEOMETRY" in event.cause_tags and moved_before:
                reason = (f"Target moved to {tuple(moved_before.deltas['to'])} before impact t{event.tick}; "
                          "the committed strike's locked sector no longer covered that position.")
            elif "OUT_OF_REACH" in event.cause_tags and moved_before:
                reason = (f"Target moved before impact t{event.tick}; distance {data.get('distance', '?')} "
                          f"exceeded reach {data.get('reach', '?')}.")
            else:
                reason = (f"Distance {data['distance']:g} > reach {data['reach']:g}."
                          if "distance" in data else "Target was outside weapon reach.")
            if "AIM_DISPERSION_MISS" not in event.cause_tags:
                checks.append((70, "Reach: " + reason))
        elif kind == "CLASH":
            lane = data.get("lane", "MID").lower()
            outcomes.append(f"{actor} and {target}'s {lane}-line weapon paths clashed; neither body strike landed.")
            checks.append((95, f"Clash: committed paths intersected on the {lane} line within "
                           f"{data.get('tick_gap', 0)} tick(s); both strikes were consumed."))
        elif kind == "HIT":
            possessive = "your" if target == "You" else f"{target}'s"
            site = data.get("site", event.zone.value).replace("_", " ").lower()
            side = f"{data['side'].lower()} " if data.get("side") else ""
            numbers = injury_numbers(event, private_actor_ids, compact=True)
            number_text = f"; {numbers}" if numbers else ""
            outcomes.append(f"{actor} hit {possessive} {side}{site} ({event.severity.value.lower()}{number_text}).")
            comparison = stat_comparison(event)
            if comparison:
                checks.append((85, comparison))
            changes = condition_delta(event)
            if changes:
                condition_changes.append(changes)
            if data.get("structure"):
                depth = data.get('depth', event.severity.value.lower()).replace('_', ' ').lower()
                structure = data['structure'].replace('_', ' ').lower()
                reached = f"reached {depth}" if depth == structure else f"reached {depth} tissue ({structure})"
                checks.append((60, f"Injury: {side}{site}; {event.severity.value.lower()} "
                               f"{data.get('damage_type', 'cutting').lower()} contact {reached}; "
                               f"function: {data.get('function', 'bleeding and pain only').replace('_', ' ')}."))
            if "GUARD_BYPASSED" in event.cause_tags:
                checks.append((100, f"Guard: it was active, but the resolved {event.zone.value.lower()} contact "
                                    "was outside the covered guard line; the strike was not absorbed."))
            elif (choices.get(event.target)
                  and choices[event.target].deltas.get("kind") in {"GUARD", "DEFEND"}
                  and data.get("defender_phase") not in {"GUARD_RAISE", "GUARD_HOLD"}):
                checks.append((100, f"Guard: no active guard absorbed the strike; target was "
                                    f"{data['defender_phase'].replace('_', ' ').lower()} at impact."))
            if data.get("drifted") and "accuracy" in data:
                if data.get("aimed_zone") == "HAND" and not data.get("hand_exposed", True):
                    checks.append((90, f"Exposure: hand covered → {event.zone.value.lower()} instead."))
                else:
                    checks.append((90, f"Aim: seeded dispersion resolved {data.get('aimed_zone', 'target').lower()} "
                                   f"to {event.zone.value.lower()} (clean chance "
                                   f"{data.get('clean_probability', 0):.0%}); Accuracy "
                                   f"{data['accuracy']:.1f} is unchanged."))
            if "LIGHT_INTERRUPTS_HEAVY" in event.cause_tags:
                other = choices.get(event.target)
                if other and other.deltas.get('strike_tick', event.tick) > event.tick:
                    own = choices.get(event.actor)
                    speeds = (f" (effective Speed {own.deltas['speed']:.1f} vs "
                              f"{other.deltas['speed']:.1f})"
                              if own and "speed" in own.deltas and "speed" in other.deltas else "")
                    checks.append((98, f"Timing: Light impact t{event.tick} preceded Heavy t{other.deltas['strike_tick']}"
                                   f"{speeds}; it interrupted during windup."))
        elif kind == "GUARD_BREAK":
            guard = "your guard" if target == "You" else f"{target}'s guard"
            outcomes.append(f"{actor} broke {guard}.")
            if data.get('stamina_before', float('inf')) < data.get('stamina_cost', 0):
                checks.append((100, f"Guard: stamina {data['stamina_before']:.1f} < {data['stamina_cost']:.1f} needed."))
            elif "defender_strength" in data:
                checks.append((100, f"Guard: Strength {data['defender_strength']:.1f} < {data['break_ratio']*data['attacker_strength']:.1f} needed."))
            else:
                checks.append((100, "Guard: insufficient strength or stamina."))
        elif kind == "PARRY":
            outcomes.append(f"{actor} parried {target}.")
            reason = (f"Strength {data['defender_strength']:.1f} ≥ {data['parry_ratio']*data['attacker_strength']:.1f} needed; inside timing window."
                      if "defender_strength" in data else "Strike arrived inside the parry window.")
            checks.append((60, "Parry: " + reason))
        elif kind == "BLOCK":
            outcomes.append(f"{actor} blocked {target}.")
            checks.append((40, f"Guard: block cost {data.get('stamina_cost', 0):.1f} stamina."))
        elif kind == "INTERRUPT":
            outcomes.append(f"{actor}: {data['kind'].replace('_', '+').lower()} interrupted.")
        elif kind == "INJURY_RESPONSE":
            response = data.get("response", "reacted").lower()
            if response in {"flinch", "recoil"}:
                outcomes.append(f"{actor} was forced back one tile by the injury reflex.")
            elif response == "braced":
                blocker = data.get("blocker", "an obstruction").replace("_", " ").lower()
                outcomes.append(f"{actor} braced; one-tile injury reflex was blocked by {blocker}.")
            elif response == "refractory":
                outcomes.append(f"{actor} held position during the injury-reflex cooldown.")
        elif kind == "FEINT":
            if data.get("outcome") == "GUARD_DISRUPTED":
                outcomes.append(f"{actor}'s feint disrupted {target}'s guard.")
                checks.append((65, f"Feint: guard interrupted through t{data.get('opening_until_tick')}; "
                               f"drained {data.get('guard_stamina_drained', 0):.1f} stamina; "
                               f"riposte consumed={bool(data.get('riposte_consumed'))}."))
            else:
                outcomes.append(f"{actor}'s feint had no effect.")
                if data.get("guard_active"):
                    reason = (f"Guard was active but out of reach at completion "
                              f"(distance {data.get('distance')}; reach {data.get('reach')}).")
                else:
                    phase = data.get("guard_phase") or "no guard action"
                    reason = (f"No active Guard at completion (observed phase {phase}; "
                              f"distance {data.get('distance')}; reach {data.get('reach')}).")
                checks.append((65, "Feint: " + reason))
        elif kind == "SHOVE":
            outcome = data.get("outcome", "NO_EFFECT")
            outcome_text = ("collapsed both fighters into a ground pile" if data.get("pile_up") else
                            outcome.replace("_", " ").lower())
            outcomes.append(f"{actor}'s shove {outcome_text} against {target}.")
            route = (f"route blocked by {who(data['blocker'])}; moved "
                     f"{data.get('actual_steps', 0)}/{data.get('requested_steps', 0)} steps"
                     if data.get("blocker") else
                     f"moved {data.get('actual_steps', 0)}/{data.get('requested_steps', 0)} steps")
            if data.get("blocker") == "DESTINATION_CONFLICT":
                route = ("simultaneous shove routes claimed a shared destination; neither shove won displacement; "
                         f"moved {data.get('actual_steps', 0)}/{data.get('requested_steps', 0)} steps")
            checks.append((75, f"Shove: strength/balance {data.get('attacker_score', 0):.1f} vs "
                           f"{data.get('defender_score', 0):.1f}; destination {tuple(data.get('destination', ()))}, "
                           f"guard braced={bool(data.get('braced'))}; {route}."))
            target_choice = choices.get(event.target)
            target_hit = next((hit for hit in events if hit.type.value == "HIT"
                               and hit.actor == event.target and hit.target == event.actor), None)
            if target_hit and target_hit.tick <= event.tick:
                checks.append((86, f"Ordering: target's strike hit at t{target_hit.tick} before Shove resolved at t{event.tick}; the hit came first."))
            elif data.get("actual_steps", 0) > 0 and any(
                    item.type.value == "INTERRUPT" and item.actor == event.target
                    and item.tick == event.tick for item in events):
                checks.append((150, f"Ordering: Shove displacement at t{event.tick} interrupted the target's committed action."))
            elif data.get("committed_heavy"):
                checks.append((150, f"Ordering: the off-balance Shove at t{event.tick} interrupted the committed Heavy before its strike."))
            elif (data.get("actual_steps", 0) > 0 and target_choice
                  and target_choice.deltas.get("strike_tick") is not None
                  and target_choice.deltas["strike_tick"] > event.tick):
                checks.append((86, f"Ordering: Shove displaced the target at t{event.tick} before its committed strike at "
                               f"t{target_choice.deltas['strike_tick']}; displacement interrupted that action."))
            elif data.get("outcome") == "OFF_BALANCE":
                checks.append((70, f"{target} lost stability but held position; no committed Heavy was interrupted."))
        elif kind == "DISARM":
            outcome = data.get("outcome", "GRIP_HELD").replace("_", " ").lower()
            outcomes.append(f"{actor}'s disarm attempt {outcome} against {target}.")
            if "attacker_strength" in data:
                checks.append((80, f"Grip: Strength {data['attacker_strength']:.1f} vs "
                               f"{data['defender_strength']:.1f} × {data['required_ratio']:g}; "
                               f"clean hand={not data.get('drifted', False)}."))
            else:
                checks.append((80, "Disarm: guard, range, aim drift or grip contest prevented it."))
        elif kind == "ADRENALINE":
            outcome = data.get("outcome")
            if outcome == "ACTIVATED":
                outcomes.append(f"{actor} activated adrenaline for {data['duration_ticks']} ticks.")
                checks.append((70, f"Adrenaline: pain penalty suppressed {data['pain_suppression']:.0%}; "
                               f"fatigue debt {data['debt_due']:g} due at expiry."))
            elif outcome == "EXPIRED":
                outcomes.append(f"{actor}'s adrenaline expired; fatigue debt {data['debt_applied']:g} applied.")
                checks.append((70, f"Adrenaline debt: {data['debt_applied']:g} applied "
                               f"(requested {data['debt_due']:g})."))
            elif outcome == "INTERRUPTED":
                outcomes.append(f"{actor}'s committed adrenaline was interrupted before activation.")
                checks.append((70, "Adrenaline: use was spent; no surge or fatigue debt was applied."))
        elif kind == "WEAPON_STRESS":
            checks.append((45, f"Weapon contact: {data.get('name', 'weapon')} took {data['wear']:.1f} condition wear "
                           f"from {data['contact'].lower()} ({data['material'].lower()}); "
                           f"{data['condition_after']:.1f}% remains."))
        elif kind == "WEAPON_DROP":
            outcomes.append(f"{actor} dropped {data.get('name', 'a weapon')} ({data.get('cause', 'combat').lower()}).")
        elif kind == "WEAPON_BREAK":
            outcomes.append(f"{actor}'s {data.get('name', 'weapon')} broke from {data.get('contact', 'contact').lower()} stress.")
        elif kind == "WEAPON_PICKUP":
            outcomes.append(f"{actor} picked up {data.get('name', 'a weapon')} at {data['condition']:.0f}% condition.")
        elif kind == "SEVER":
            side = f"{data['side'].lower()} " if data.get("side") else ""
            if data.get("ear_lost"):
                outcomes.append(f"{target} lost the {side}ear; side-specific hearing is not modeled.")
            else:
                outcome = ("weapon hand lost; weapon use is disabled" if data.get("weapon_hand_lost")
                           else "supporting hand lost; one-handed weapon remains usable")
                outcomes.append(f"{target} lost the {side}hand ({outcome}).")
        elif kind == "EYE_INJURY":
            outcomes.append(f"{target} sustained a permanent eye injury ({data['eyes_damaged_after']} eye(s) impaired).")
            cause = (f"clean eye contact reached {data['net_after_armor']:.1f} post-armor force "
                     f"(threshold {data['eye_trauma_threshold']:.1f}); a clean CUT-or-worse hit damages one eye.")
            checks.append((95, "Eye injury: " + cause))
        elif kind == "DECAPITATION":
            outcomes.append(f"{target} died from a clean heavy cutting head strike.")
            checks.append((100, "Decapitation: clean heavy cutting head contact met the fatal injury condition."))
        elif kind == "INCAPACITATED":
            outcomes.append(f"{target} was incapacitated by {data.get('cause', 'injury').replace('_', ' ').lower()}.")
    if not outcomes:
        for actor, choice in choices.items():
            moved = sum(event.actor == actor and event.type.value == "MOVE" for event in events)
            interrupted = any(event.actor == actor and
                              ({"MOVEMENT_BLOCKED", "MOVEMENT_CONFLICT"} & set(event.cause_tags))
                              for event in events)
            if moved:
                outcomes.append(f"{who(actor)} moved {moved} tile{'s' if moved != 1 else ''}.")
            elif interrupted:
                outcomes.append(f"{who(actor)}'s movement was blocked.")
                if any(event.actor == actor and "MOVEMENT_CONFLICT" in event.cause_tags
                       for event in events):
                    checks.append((50, "Position: simultaneous movement claims collided; no fighter won the tile."))
                else:
                    checks.append((50, "Position: the destination was occupied when the step resolved."))
            else:
                outcomes.append(f"{who(actor)}: {choice.deltas['kind'].replace('_', '+').lower()}.")
        moving = next((choice for choice in choices.values() if choice.deltas.get('direction') and 'speed' in choice.deltas), None)
        if moving:
            checks.append((10, f"Movement: Speed {moving.deltas['speed']:.1f} set step timing."))
    consequences = list(condition_changes)
    bleed_totals, forced_steps = {}, {}
    for event in events:
        kind, data = event.type.value, event.deltas
        actor = who(event.actor)
        if kind == "INJURY_RESPONSE" and data.get("response", "").lower() in {"flinch", "recoil"}:
            consequences.append(f"{actor} forced back 1 tile (injury reflex)")
        elif kind == "INJURY_RESPONSE" and data.get("response", "").lower() == "braced":
            blocker = data.get("blocker", "obstruction").replace("_", " ").lower()
            consequences.append(f"{actor} braced; injury reflex blocked by {blocker}")
        elif kind == "INJURY_RESPONSE" and data.get("response", "").lower() == "refractory":
            consequences.append(f"{actor} held position (injury-reflex cooldown)")
        elif kind == "POOL_CHANGE" and data.get("bleed_loss", 0) > 0:
            bleed_totals[event.actor] = bleed_totals.get(event.actor, 0) + data["bleed_loss"]
        elif kind == "HIT":
            zone = event.zone.value.lower() if event.zone else "body"
            site = data.get("site", zone.replace(" ", "_")).replace("_", " ").lower()
            if data.get("side"):
                site = data["side"].lower() + " " + site
            structure = data.get("structure", "tissue").replace("_", " ").lower()
            depth = data.get("depth", event.severity.value if event.severity else "wound").lower()
            function = data.get("function", "bleeding and pain only").replace("_", " ")
            injury = structure if depth.replace("_", " ") == structure else f"{depth} {structure}"
            consequences.append(f"{who(event.target)}: {zone} ({site}) {injury} injury; "
                                f"pain +{data.get('pain', 0):g}; {function}")
        elif kind == "CLASH":
            consequences.append(f"{who(event.actor)} and {who(event.target)}: weapon paths crossed on the "
                                f"{data.get('lane', 'MID').lower()} line; neither struck the body")
        elif kind == "WOUND":
            bleed = data.get("bleed_per_turn", 0)
            if data.get("initial_blood_loss", 0) > 0:
                site = data.get("site", event.zone.value).replace("_", " ").lower()
                side = f"{data['side'].lower()} " if data.get("side") else ""
                amount = ("one-off superficial loss; amount unknown" if event.target in private_actor_ids
                          else f"one-off blood loss {data['initial_blood_loss']:.2f}")
                consequences.append(f"{who(event.target)}: {side}{site} graze caused {amount}; "
                                    "no continuing bleed")
            if bleed > 0:
                site = data.get("site", event.zone.value).replace("_", " ").lower()
                side = f"{data['side'].lower()} " if data.get("side") else ""
                consequences.append(f"{who(event.target)}: {side}{site} bleeding {bleed:g} blood/turn")
        elif kind == "STATUS" and data.get("status"):
            consequences.append(f"{actor}: {data['status'].replace('_', ' ').lower()}")
        elif kind == "STABILITY" and "loss" in data:
            if event.actor in private_actor_ids:
                consequences.append(f"{actor} off-balance")
            else:
                loss = f"{data['loss']:.1f}".rstrip("0").rstrip(".")
                consequences.append(f"{actor} −{loss} stability")
        elif kind == "STABILITY" and "gain" in data:
            if event.actor in private_actor_ids:
                consequences.append(f"{actor} steadied")
            else:
                gain = f"{data['gain']:.1f}".rstrip("0").rstrip(".")
                consequences.append(f"{actor} +{gain} stability")
        elif kind == "MOVE" and data.get("forced"):
            forced_steps[event.actor] = forced_steps.get(event.actor, 0) + 1
        elif kind == "FALL":
            consequences.append(f"{actor} grounded")
        elif kind == "INCAPACITATED":
            consequences.append(f"{who(event.target)} incapacitated")
        elif kind == "STATUS" and "BANDAGE_COMPLETED" in event.cause_tags:
            consequences.append(f"{actor} treated a wound")
    for actor, amount in bleed_totals.items():
        consequences.append(f"{who(actor)} bleeding; amount unknown" if actor in private_actor_ids
                            else f"{who(actor)} −{amount:.1f} blood")
    for actor, steps in forced_steps.items():
        consequences.append(f"{who(actor)} pushed {steps} tile{'s' if steps != 1 else ''}")
    for actor, choice in choices.items():
        if actor not in private_actor_ids and choice.deltas.get("paid", 0) > 0:
            consequences.append(f"{who(actor)} paid {choice.deltas['paid']:.1f} stamina"
                                + (f"; fatigue +{choice.deltas['fatigue_added']:.1f}"
                                   if choice.deltas.get("fatigue_added", 0) > 0 else ""))
    # Keep the summary bounded and preserve separate detailed facts behind Details.
    consequence_line = "Effects: " + (
        "; ".join(list(dict.fromkeys(consequences))[:4]) if consequences
        else "no additional control, injury, bleeding or recovery change")
    return (" | ".join(f"{who(actor)}: {short_choice(choice.deltas)}" for actor, choice in choices.items()),
            " ".join(outcomes[:2]) or "No strike exchanged.",
            consequence_line,
            max(checks, key=lambda item: item[0])[1] if checks else "No attack check was needed.")


def format_exchange(events, names, *, before=None, fighters=(), private_actor_ids=()):
    """Choice, result and measured reason; older saved events omit unknown numbers."""
    choices = {event.actor: event for event in events if event.type.value == "ACTION_START"}
    rows = [f"{names.get(actor, actor)} chose: {describe_choice(event.deltas)}."
            for actor, event in choices.items()]
    def who(actor):
        return names.get(actor, actor or "Fight")
    for event in events:
        kind, data = event.type.value, event.deltas
        actor, target = who(event.actor), who(event.target)
        if kind == "WHIFF":
            if "AIM_DISPERSION_MISS" in event.cause_tags:
                why = (f"aimed at {data.get('aimed_zone', 'target').lower()}; seeded dispersion left no clean or "
                       f"nearby-site contact (clean chance {data.get('clean_probability', 0):.0%})")
            else:
                why = (f"distance {data['distance']:g} > weapon reach {data['reach']:g} at the strike"
                       if "distance" in data else "target was outside weapon reach at the strike")
            explanation = ("this was a seeded aim-dispersion miss, not a reach failure"
                           if "AIM_DISPERSION_MISS" in event.cause_tags
                           else "this was positioning, not an accuracy roll")
            rows.append(f"{actor}: missed {target}. Why: {why}; {explanation}.")
        elif kind == "CLASH":
            rows.append(f"{actor} and {target}: weapon paths intersected on the "
                        f"{data.get('lane', 'MID').lower()} line within {data.get('tick_gap', 0)} tick(s); "
                        "both body strikes were stopped.")
        elif kind == "HIT":
            zone = event.zone.value.lower()
            reason = []
            if "accuracy" in data:
                reason.append(f"seeded aim dispersion: clean chance {data.get('clean_probability', 0):.0%}; "
                              f"Accuracy {data['accuracy']:.1f} is unchanged")
                if data.get("drifted"):
                    reason.append(f"aim resolved from {data['aimed_zone'].lower()} to {zone}")
            tags = event.cause_tags
            if "LIGHT_INTERRUPTS_HEAVY" in tags:
                opponent_choice = choices.get(event.target)
                own_choice = choices.get(event.actor)
                if opponent_choice and own_choice:
                    own_due = own_choice.deltas.get("strike_tick")
                    other_due = opponent_choice.deltas.get("strike_tick")
                    own_speed = own_choice.deltas.get("speed")
                    other_speed = opponent_choice.deltas.get("speed")
                    if own_due is not None and other_due is not None:
                        reason.append(
                            f"Light impact was due t{own_due} (effective Speed {own_speed:g}) "
                            f"before the opponent's Heavy at t{other_due} "
                            f"(effective Speed {other_speed:g}); the hit landed during Heavy windup and interrupted it")
                    else:
                        reason.append("light strike landed during heavy windup, interrupting it")
                else:
                    reason.append("light strike landed during heavy windup, interrupting it")
            if "WALK_DEFENSELESS" in tags:
                reason.append("walking left the target defenseless")
            if "GUARD_BYPASSED" in tags:
                reason.append(f"guard was active, but the resolved {zone} contact was outside its covered line")
            if "LIGHT_CATCHES_RECOVERY" in tags:
                reason.append("target was still recovering")
            if not reason:
                reason.append("contact reached the target; see the wound and function below")
            if data.get("defender_phase") == "STAGGER":
                reason.append("staggered target granted the clean-hit power bonus")
            possessive = "your" if target == "You" else f"{target}'s"
            site = data.get("site", zone.upper()).replace("_", " ").lower()
            if data.get("side"):
                site = data["side"].lower() + " " + site
            rows.append(f"{actor}: hit {possessive} {zone} ({site}; {event.severity.value.lower()}). Why: "
                        + "; ".join(reason)+".")
            comparison = action_stat_comparison(event, choices, names, private_actor_ids)
            if comparison:
                rows.append(comparison)
            if data.get("structure"):
                depth = data.get('depth', event.severity.value.lower()).replace('_', ' ').lower()
                structure = data['structure'].replace('_', ' ').lower()
                reached = f"reached {depth}" if depth == structure else f"reached {depth} tissue ({structure})"
                rows.append(f"{actor}: {data.get('damage_type', 'cutting').lower()} contact {reached} at {site} ({zone}); "
                            f"function: {data.get('function', 'bleeding and pain only').replace('_', ' ')}; "
                            f"pain increased by {data.get('pain', 0):g}.")
            else:
                rows.append(f"{target}: {zone} {event.severity.value.lower()} contact; "
                            "see current condition for resulting function and bleeding.")
            rows.extend(injury_numbers(event, private_actor_ids).splitlines())
            changes = condition_change_line(event, names, private_actor_ids)
            if changes:
                rows.append(changes)
        elif kind in {"PARRY", "BLOCK", "GUARD_BREAK"}:
            if kind == "PARRY":
                reason = (f"inside the {data['parry_window']}-tick parry window; defender Strength {data['defender_strength']:.1f} "
                          f">= {data['parry_ratio']:g} x attacker Strength {data['attacker_strength']:.1f}"
                          if "defender_strength" in data else "strike arrived inside the parry window with enough defender Strength")
                rows.append(f"{actor}: parried {target}. Why: {reason}.")
            else:
                reason = f"guard absorbed the strike for {data.get('stamina_cost', 0):.1f} stamina"
                if "defender_strength" in data:
                    reason += f" (defender Strength {data['defender_strength']:.1f} vs attacker {data['attacker_strength']:.1f})"
                    if kind == "GUARD_BREAK":
                        reason += (f"; stamina {data['stamina_before']:.1f} < cost" if data['stamina_before'] < data['stamina_cost']
                                   else f"; defender Strength < {data['break_ratio']:g} x attacker Strength")
                    elif not data['in_parry_window']:
                        reason += "; too early/late for the parry window"
                    else:
                        reason += f"; defender Strength < {data['parry_ratio']:g} x attacker Strength needed to parry"
                rows.append(f"{actor}: {'broke '+target+' guard' if kind == 'GUARD_BREAK' else 'blocked '+target}. Why: {reason}.")
        elif kind == "INTERRUPT":
            rows.append(f"{actor}: {data['kind'].replace('_', ' ').lower()} interrupted before completion.")
        elif kind == "INJURY_RESPONSE":
            response = data.get("response", "reacted").lower()
            if response == "recoil":
                rows.append(f"{actor}: injury shock forced one tile away from {target}, "
                            f"{tuple(data.get('from_pos', ()))} → {tuple(data.get('to_pos', ())) }; "
                            "no attack or stamina refund was granted.")
            elif response == "braced":
                blocker = data.get("blocker", "obstruction").replace("_", " ")
                rows.append(f"{actor}: tried to recoil but braced at {blocker}; no overlap or teleport occurred.")
            elif response == "refractory":
                rows.append(f"{actor}: no second forced recoil before t{data.get('next_recoil_tick')}; "
                            "the wound and any normal interruption still apply.")
            elif response == "flinch":
                rows.append(f"{actor}: flinched; injury response did not grant an action or move.")
        elif kind == "FEINT":
            if data.get("outcome") == "GUARD_DISRUPTED":
                rows.append(f"{actor}: feint disrupted {target}'s committed guard; "
                            f"{data.get('guard_stamina_drained', 0):.1f} guard stamina drained, "
                            f"stored riposte consumed={bool(data.get('riposte_consumed'))}; "
                            f"opening until t{data.get('opening_until_tick')}.")
            else:
                if data.get("guard_active"):
                    rows.append(f"{actor}: feint had no effect; Guard active but out of reach "
                                f"({data.get('distance')} tiles; reach {data.get('reach')}).")
                else:
                    rows.append(f"{actor}: feint had no effect; no active Guard at completion "
                                f"(phase {data.get('guard_phase') or 'none'}, distance {data.get('distance')}, "
                                f"reach {data.get('reach')}).")
        elif kind == "SHOVE":
            outcome = data.get("outcome", "NO_EFFECT").replace("_", " ").lower()
            contest = (f"Strength/balance {data['attacker_score']:.1f} vs {data['defender_score']:.1f}; "
                       if "attacker_score" in data else "")
            circumstance = (f"route blocked by {who(data['blocker'])}; "
                            f"moved {data.get('actual_steps', 0)}/{data.get('requested_steps', 0)} steps."
                            if data.get("blocker") else
                             f"moved {data.get('actual_steps', 0)}/{data.get('requested_steps', 0)} steps.")
            if data.get("blocker") == "DESTINATION_CONFLICT":
                circumstance = ("simultaneous shove routes claimed a shared destination; neither shove won "
                                f"displacement; moved {data.get('actual_steps', 0)}/"
                                f"{data.get('requested_steps', 0)} steps.")
            elif data.get("pile_up"):
                circumstance += " The blocked impact collapsed both fighters into a ground pile."
            elif outcome == "resisted":
                circumstance = "defender's strength/balance met or exceeded the shove contest."
            elif outcome == "off balance":
                circumstance = "contest margin was positive but below the displacement threshold."
            target_choice = choices.get(event.target)
            target_hit = next((hit for hit in events if hit.type.value == "HIT"
                               and hit.actor == event.target and hit.target == event.actor
                               and hit.tick <= event.tick), None)
            if target_hit:
                circumstance += f" Target's strike hit at t{target_hit.tick}, before Shove resolved at t{event.tick}."
            elif data.get("actual_steps", 0) > 0 and any(
                    item.type.value == "INTERRUPT" and item.actor == event.target
                    and item.tick == event.tick for item in events):
                circumstance += f" Displacement at t{event.tick} interrupted the target's committed action."
            elif data.get("committed_heavy"):
                circumstance += (f" Off-balance Shove at t{event.tick} interrupted the committed Heavy "
                                 "before its strike.")
            elif (data.get("actual_steps", 0) > 0 and target_choice
                  and target_choice.deltas.get("strike_tick") is not None
                  and target_choice.deltas["strike_tick"] > event.tick):
                circumstance += (f" Displacement at t{event.tick} interrupted the target's committed strike "
                                 f"due t{target_choice.deltas['strike_tick']}.")
            rows.append(f"{actor}: shove {target} {outcome}. Why: {contest}"
                        f"guard brace={bool(data.get('braced'))}; {circumstance} "
                        f"destination {tuple(data.get('destination', ())) }.")
        elif kind == "DISARM":
            outcome = data.get("outcome", "GRIP_HELD").replace("_", " ").lower()
            if "attacker_strength" in data:
                why = (f"Grip contest: Strength {data['attacker_strength']:.1f} vs {data['defender_strength']:.1f} × "
                       f"{data['required_ratio']:g}; hand drifted={bool(data.get('drifted'))}")
            else:
                why = "setup, guard or reach prevented clean hand contact"
            rows.append(f"{actor}: disarm attempt on {target} {outcome}. Why: {why}.")
        elif kind == "ADRENALINE":
            outcome = data.get("outcome")
            if outcome == "COMMITTED":
                reasons = ", ".join(data.get("trigger_reasons", ()))
                rows.append(f"{actor}: adrenaline committed once this duel (trigger: {reasons or 'danger/exertion'}).")
            elif outcome == "ACTIVATED":
                rows.append(f"{actor}: adrenaline active through t{data['until_tick']}; pain stat penalty suppressed "
                            f"{data['pain_suppression']:.0%}; fatigue debt {data['debt_due']:g} due at expiry.")
            elif outcome == "EXPIRED":
                rows.append(f"{actor}: adrenaline expired; fatigue debt {data['debt_applied']:g} applied "
                            f"(requested {data['debt_due']:g}).")
            elif outcome == "INTERRUPTED":
                rows.append(f"{actor}: adrenaline action interrupted before activation; use remains spent, no debt is due.")
        elif kind == "STABILITY" and "source" in data:
            rows.append(f"{actor}: stability {data.get('loss', data.get('gain', 0)):+.1f} from "
                        f"{data['source'].replace('_', ' ').lower()}.")
        elif kind == "STATUS" and ({"MOVEMENT_BLOCKED", "MOVEMENT_CONFLICT"} & set(event.cause_tags)):
            location = tuple(data.get("position", ()))
            blocker = who(event.target)
            if data.get("reason") == "DESTINATION_CONFLICT":
                contenders = ", ".join(
                    f"{who(actor_id)} ({kind.replace('_', '+').lower()})"
                    for actor_id, kind in data.get("contenders", ()))
                rows.append(f"{actor}: movement blocked at {location}; simultaneous routes claimed the same tile "
                            f"({contenders}). Neither action won the tile; both remained collision-safe.")
            elif blocker:
                rows.append(f"{actor}: movement blocked at {location}; {blocker} occupied the destination when the step resolved.")
            else:
                reason = data.get("reason", "OCCUPIED").replace("_", " ").lower()
                rows.append(f"{actor}: movement blocked at {location} by {reason}; no move-through occurred.")
        elif kind == "STATUS" and "BANDAGE_COMPLETED" in event.cause_tags:
            if event.actor in private_actor_ids:
                rows.append(f"{actor}: bandaged a {data['zone'].lower()} wound; remaining bleeding is uncertain.")
            else:
                rows.append(f"{actor}: bandaged the {data['zone'].lower()} {data['severity'].lower()} wound; "
                            f"bleed rate {data['rate_before']:g} → {data['rate_after']:g} per turn; "
                            f"{data['bandages_remaining']} bandage(s) remain.")
        elif kind == "FIGHT_END":
            rows.append(f"Fight ended: {data['reason']}.")
    for actor in choices:
        moves = [event for event in events if event.actor == actor and event.type.value == "MOVE"]
        if moves:
            rows.append(f"{who(actor)} movement: {len(moves)} tile{'s' if len(moves) != 1 else ''}, "
                        f"{tuple(moves[0].deltas['from'])} -> {tuple(moves[-1].deltas['to'])}. "
                        "Speed set step timing; positions at the strike determined reach.")
        ended = [event for event in events if event.actor == actor and event.type.value == "ACTION_END"]
        if ended and choices[actor].deltas['kind'] in {"WAIT", "GUARD", "BANDAGE", "EAT", "DRINK", "PICKUP"}:
            rows.append(f"{who(actor)}: {choices[actor].deltas['kind'].lower()} completed."
                        + (" No strike was attempted." if choices[actor].deltas['kind'] == "WAIT" else ""))
    bleed_totals, bleed_rates = {}, {}
    initial_loss_totals = {}
    for event in events:
        if event.type.value == "POOL_CHANGE" and event.deltas.get("bleed_loss", 0) > 0:
            bleed_totals[event.actor] = bleed_totals.get(event.actor, 0) + event.deltas["bleed_loss"]
            bleed_rates[event.actor] = max(bleed_rates.get(event.actor, 0),
                                           event.deltas.get("bleed_rate_per_turn", 0))
        elif event.type.value == "POOL_CHANGE" and event.deltas.get("initial_blood_loss", 0) > 0:
            initial_loss_totals[event.actor] = (initial_loss_totals.get(event.actor, 0)
                                                + event.deltas["initial_blood_loss"])
    for actor, amount in bleed_totals.items():
        if actor in private_actor_ids:
            rows.append(f"{who(actor)} appears to be bleeding; exact blood loss and rate are unknown.")
        else:
            rows.append(f"{who(actor)} bleeding: lost {amount:.2f} blood during this exchange "
                        f"(observed rate up to {bleed_rates[actor]:g}/turn before exertion).")
    for actor, amount in initial_loss_totals.items():
        if actor in private_actor_ids:
            rows.append(f"{who(actor)} sustained a superficial wound; exact blood loss is unknown.")
        else:
            rows.append(f"{who(actor)} superficial graze: one-off blood loss {amount:.2f}; no continuing bleed.")
    for actor, event in choices.items():
        data = event.deltas
        if "speed" in data:
            timing = (f"strike at t{data['strike_tick']}" if data.get("strike_tick") is not None
                      else f"ready at t{data['end_tick']}" if data.get("end_tick") is not None else "guard raised and held")
            rows.append(f"{who(actor)} timing: effective Speed {data['speed']:.1f}, windup {data['windup_ticks']} ticks; {timing}. "
                        "Speed changes timing, not action count.")
            if "fatigue_added" in data:
                rows.append(f"{who(actor)} cost: stamina {data['paid']:.1f}/{data['cost']:.1f} paid, fatigue +{data['fatigue_added']:.1f}. "
                            f"Endurance {data['endurance']:.1f} and gear weight {data['weight']:g} shaped fatigue gain."
                            + (" Unpaid stamina became extra fatigue/exhaustion." if data['paid'] < data['cost'] else ""))
            penalties = [f"{label} {data[key]*100:.0f}%" for key, label in
                         (("fatigue_penalty", "fatigue"), ("pain_penalty", "pain"), ("blood_penalty", "blood loss")) if data.get(key, 0) > 0]
            if penalties:
                rows.append(f"{who(actor)} stat penalties: " + ", ".join(penalties)+" affected effective stats.")
    if before is not None:
        for fighter in fighters:
            blood, stamina, fatigue = before[fighter.id]
            rows.append(f"{who(fighter.id)} resources: blood {fighter.blood-blood:+.1f}, stamina {fighter.stamina-stamina:+.1f}, fatigue {fighter.fatigue-fatigue:+.1f}.")
    return tuple(rows)
