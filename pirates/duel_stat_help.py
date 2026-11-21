"""Read-only explanations of displayed combat numbers."""
STATS = ("Strength", "Speed", "Accuracy", "Endurance", "Blood", "Stamina", "Fatigue", "Pain", "Fear", "Anger", "Morale", "Stability")
POOL_NAMES = {"Blood": "BLD", "Stamina": "STA", "Fatigue": "FAT", "Pain": "PAIN", "Fear": "FEAR", "Anger": "ANGER", "Morale": "MORALE"}
MEANINGS = {
    "Strength": "Strike force and guard contests. Strike power also includes the action multiplier and weapon edge; armor is subtracted afterwards.",
    "Speed": "Shortens action windup, recovery and movement. It changes when an action lands, never how many actions you get per exchange.",
    "Accuracy": "Authored skill stays fixed, but effective Accuracy is reduced by fear and anger. Fatigue, pain, blood, eye injury and status do not directly reduce it. Target difficulty, visibility and exposure still constrain contact.",
    "Endurance": "Reduces fatigue gained from exertion and pain, and softens some fatigue/pain penalties. It is not the stamina pool size.",
    "Blood": "Remaining life reserve. Bleeding drains it; low blood weakens effective stats and can cause collapse. Zero blood is fatal.",
    "Stamina": "Available action fuel. Actions and blocks spend it; rest restores it. Unpaid costs turn into fatigue and exhaustion, not a free action.",
    "Fatigue": "Accumulated strain, not spare stamina. Higher fatigue can reduce effective stats; endurance mitigates its pressure. Rest reduces it.",
    "Pain": "Raw injury pain remains visible. Pain tolerance lowers its effective burden on focus and on fear/anger gains; wounds still cause their full injury effects.",
    "Fear": "Starts from low confidence and grows from hits, pain and continuing blood loss. Fear reduces effective Speed and Accuracy during this fight.",
    "Anger": "Tolerance moderates anger gained from hits, injury, pain and bleeding. Anger can raise Speed/Strength, lower Accuracy and worsen footing; it is not an unconditional bonus.",
    "Morale": "Legacy confidence/resolve record. The new fight fear pool is tracked separately and starts from the confidence trait.",
    "Stability": "Current balance reserve. Heavy overreach and successful fast turns can reduce it; zero causes a fall. Standing Wait restores it, while a hit can interrupt an exposed Rise.",
}


def stat_rows(model, name, *, enemy=False):
    """Use public RenderModel values only; never infer unlearned enemy stats."""
    # The dashboard's meter may include a live balance annotation, e.g.
    # "Stability (bal 44)". Explanations are keyed by the stable stat name.
    name = name.split(" (bal ", 1)[0]
    pools = {key: (value, maximum) for key, value, maximum in (model.enemy_pools if enemy else model.pools)}
    sheet = dict(model.enemy_sheet if enemy else model.player_sheet)
    if name == "Stability":
        current = ("Not observed in group combat" if enemy and model.group_combat else
                   f"{model.player_stability:.1f} / 100; balance {model.player_balance:.1f} / 100"
                   if not enemy else "Not shown")
    elif name in POOL_NAMES and POOL_NAMES[name] in pools:
        value, maximum = pools[POOL_NAMES[name]]
        current = f"{value:.1f} / {maximum:g}"
    else:
        current = sheet.get(name, "Not observed yet")
    rows = [f"{model.enemy_name if enemy else 'You'} — {name}: {current}", MEANINGS[name]]
    if not enemy and model.own_readout:
        own, rules = dict(model.own_readout), dict(model.combat_rules)
        if name == 'Speed':
            rows.append(f"Practical effect: light windup {own['light_ticks']:g} ticks now, {own['fresh_light_ticks']:g} when fresh with the same gear/level. A stat loss matters most when it crosses a timing tick.")
        elif name == 'Strength':
            rows.append(f"Practical effect: light power {own['light_power']:.1f} now vs {own['fresh_light_power']:.1f} fresh, before armor. Cut starts at net {rules['cut']:g}; deep at {rules['deep']:g}.")
        elif name == 'Accuracy':
            rows.append('Practical effect: authored Accuracy is permanent; effective Accuracy is reduced by current fear and anger, then shapes seeded clean-contact odds. Aim variation may drift to a nearby site or miss.')
        if name != "Accuracy":
            rows.extend(model.functional_alerts[:2])
    if not enemy and name in {"Strength", "Speed", "Accuracy", "Endurance"}:
        rows += ["Current/base values above include active combat effects.",
                  *(f"{label} pressure: {value*100:.1f}%" for label, value in model.player_penalties
                    if value > 0 and not (name == "Accuracy" and label in {"Fatigue", "Pain", "Blood", "Status", "Eye injury"})),
                  "Authored Accuracy is permanent; fear and anger apply separate, bounded effective-stat modifiers."]
    return tuple(rows)
