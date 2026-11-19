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
    outcomes, checks = [], []
    for event in events:
        kind, data = event.type.value, event.deltas
        actor, target = who(event.actor), who(event.target)
        if kind == "WHIFF":
            outcomes.append(f"{actor} missed.")
            reason = (f"Distance {data['distance']:g} > reach {data['reach']:g}."
                      if "distance" in data else "Target was outside weapon reach.")
            checks.append((70, "Reach: " + reason))
        elif kind == "HIT":
            possessive = "your" if target == "You" else f"{target}'s"
            site = data.get("site", event.zone.value).replace("_", " ").lower()
            side = f"{data['side'].lower()} " if data.get("side") else ""
            outcomes.append(f"{actor} hit {possessive} {side}{site} ({event.severity.value.lower()}).")
            if data.get("structure"):
                checks.append((60, f"Injury: {side}{site}; {data.get('damage_type', 'cutting').lower()} contact reached "
                               f"{data.get('depth', event.severity.value.lower()).lower()} "
                               f"{data['structure'].replace('_', ' ').lower()}; "
                               f"function: {data.get('function', 'bleeding and pain only').replace('_', ' ')}."))
            if data.get("drifted") and "accuracy" in data:
                if data.get("aimed_zone") == "HAND" and not data.get("hand_exposed", True):
                    checks.append((90, f"Exposure: hand covered → {event.zone.value.lower()} instead."))
                else:
                    accuracy, rolled, required = data['accuracy'], data['rolled_accuracy'], data['required_accuracy']
                    aim = (f"roll {rolled:.1f} < {required:g} needed (Accuracy {accuracy:.1f})"
                           if abs(accuracy-rolled) > .001 else f"Accuracy {accuracy:.1f} < {required:g} needed")
                    checks.append((90, f"Aim: {aim} → {event.zone.value.lower()}."))
            if "LIGHT_INTERRUPTS_HEAVY" in event.cause_tags:
                other = choices.get(event.target)
                if other and other.deltas.get('strike_tick', event.tick) > event.tick:
                    checks.append((85, f"Timing: light landed at t{event.tick}, before heavy at t{other.deltas['strike_tick']}."))
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
            if response == "recoil":
                outcomes.append(f"{actor} recoiled one tile from the injury.")
            elif response == "braced":
                blocker = data.get("blocker", "an obstruction").replace("_", " ").lower()
                outcomes.append(f"{actor} braced; recoil was blocked by {blocker}.")
            elif response == "refractory":
                outcomes.append(f"{actor} held position during recoil recovery.")
            elif response == "flinch":
                outcomes.append(f"{actor} flinched from the impact.")
        elif kind == "FEINT":
            if data.get("outcome") == "GUARD_DISRUPTED":
                outcomes.append(f"{actor}'s feint disrupted {target}'s guard.")
                checks.append((65, f"Feint: guard interrupted through t{data.get('opening_until_tick')}; "
                               f"drained {data.get('guard_stamina_drained', 0):.1f} stamina; "
                               f"riposte consumed={bool(data.get('riposte_consumed'))}."))
            else:
                outcomes.append(f"{actor}'s feint had no effect.")
                checks.append((65, "Feint: no active guard was in reach at completion."))
        elif kind == "SHOVE":
            outcome = data.get("outcome", "NO_EFFECT")
            outcomes.append(f"{actor}'s shove {outcome.replace('_', ' ').lower()} against {target}.")
            checks.append((75, f"Shove: strength/balance {data.get('attacker_score', 0):.1f} vs "
                           f"{data.get('defender_score', 0):.1f}; destination {tuple(data.get('destination', ()))}, "
                           f"guard braced={bool(data.get('braced'))}."))
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
            outcome = ("weapon hand lost; weapon use is disabled" if data.get("weapon_hand_lost")
                       else "supporting hand lost; one-handed weapon remains usable")
            outcomes.append(f"{target} lost the {side}hand ({outcome}).")
        elif kind == "EYE_INJURY":
            outcomes.append(f"{target} sustained a permanent eye injury ({data['eyes_damaged_after']} eye(s) impaired).")
            checks.append((95, "Eye injury: clean qualifying contact caused a persistent vision impairment."))
        elif kind == "DECAPITATION":
            outcomes.append(f"{target} died from a clean heavy cutting head strike.")
            checks.append((100, "Decapitation: clean heavy cutting head contact met the fatal injury condition."))
        elif kind == "INCAPACITATED":
            outcomes.append(f"{target} was incapacitated by {data.get('cause', 'injury').replace('_', ' ').lower()}.")
    if not outcomes:
        for actor, choice in choices.items():
            moved = sum(event.actor == actor and event.type.value == "MOVE" for event in events)
            interrupted = any(event.actor == actor and "MOVEMENT_BLOCKED" in event.cause_tags for event in events)
            if moved:
                outcomes.append(f"{who(actor)} moved {moved} tile{'s' if moved != 1 else ''}.")
            elif interrupted:
                outcomes.append(f"{who(actor)}'s movement was blocked.")
                checks.append((50, "Position: the destination was occupied."))
            else:
                outcomes.append(f"{who(actor)}: {choice.deltas['kind'].replace('_', '+').lower()}.")
        moving = next((choice for choice in choices.values() if choice.deltas.get('direction') and 'speed' in choice.deltas), None)
        if moving:
            checks.append((10, f"Movement: Speed {moving.deltas['speed']:.1f} set step timing."))
    consequences = []
    bleed_totals, forced_steps = {}, {}
    for event in events:
        kind, data = event.type.value, event.deltas
        actor = who(event.actor)
        if kind == "POOL_CHANGE" and data.get("bleed_loss", 0) > 0:
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
                consequences.append(f"{who(event.target)}: {side}{site} bleeding {bleed:g}/turn")
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
            why = (f"distance {data['distance']:g} > weapon reach {data['reach']:g} at the strike"
                   if "distance" in data else "target was outside weapon reach at the strike")
            rows.append(f"{actor}: missed {target}. Why: {why}; this was positioning, not an accuracy roll.")
        elif kind == "HIT":
            zone = event.zone.value.lower()
            reason = []
            if "accuracy" in data:
                rolled, needed = data['rolled_accuracy'], data['required_accuracy']
                comparison = ">=" if rolled >= needed else "<"
                if data["aimed_zone"] == "TORSO":
                    reason.append("torso is the accuracy fallback")
                else:
                    reason.append(f"Accuracy {data['accuracy']:.1f} (rolled {rolled:.1f}) {comparison} target requirement {needed:g}")
                if data.get("drifted"):
                    reason.append(f"aim drifted from {data['aimed_zone'].lower()} to {zone}"
                                  + (" because the hand was covered" if data['aimed_zone'] == "HAND" and not data['hand_exposed'] else ""))
            tags = event.cause_tags
            if "LIGHT_INTERRUPTS_HEAVY" in tags:
                reason.append("light strike landed during heavy windup, interrupting it")
            if "WALK_DEFENSELESS" in tags:
                reason.append("walking left the target defenseless")
            if "GUARD_BYPASSED" in tags:
                reason.append("this target bypassed the guard line")
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
            if data.get("structure"):
                rows.append(f"{actor}: {data.get('damage_type', 'cutting').lower()} contact reached "
                            f"{data.get('depth', event.severity.value.lower()).lower()} "
                            f"{data['structure'].replace('_', ' ').lower()} at {site} ({zone}); "
                            f"function: {data.get('function', 'bleeding and pain only').replace('_', ' ')}; "
                            f"pain increased by {data.get('pain', 0):g}.")
            else:
                rows.append(f"{target}: {zone} {event.severity.value.lower()} contact; "
                            "see current condition for resulting function and bleeding.")
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
                rows.append(f"{actor}: feint had no effect; no active guard was in reach at completion.")
        elif kind == "SHOVE":
            outcome = data.get("outcome", "NO_EFFECT").replace("_", " ").lower()
            contest = (f"Strength/balance {data['attacker_score']:.1f} vs {data['defender_score']:.1f}; "
                       if "attacker_score" in data else "")
            rows.append(f"{actor}: shove {target} {outcome}. Why: {contest}"
                        f"guard brace={bool(data.get('braced'))}; destination {tuple(data.get('destination', ())) }.")
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
        elif kind == "STATUS" and "MOVEMENT_BLOCKED" in event.cause_tags:
            location = tuple(data.get("position", ()))
            blocker = who(event.target)
            rows.append(f"{actor}: movement blocked at {location}; {blocker} occupied the destination when the step resolved.")
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
