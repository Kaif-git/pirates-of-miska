# Soulslike ASCII Combat System: Implementation Spec

Target: turn-based, NetHack-style ASCII roguelike. Scope for this version: **1v1 sword duels**. Everything is designed so other weapons and multi-enemy fights can be added later.

> **Instructions for the coding agent**
> 1. Language-agnostic. Pseudocode is Python-like. Port to the project's language.
> 2. **The combat engine must be a pure, deterministic, clonable simulation.** No UI, no globals, no unseeded randomness. The AI, the guide mode, the tests and the replay all depend on this.
> 3. **Every number in this document lives in one config file** (Section 20). Nothing hardcoded. Numbers are starting values to be tuned by the balance harness (Section 21).
> 4. The UI only reads a `RenderModel` produced by the engine. It never mutates combat state.
> 5. Build in the order of Section 22. Do not start the UI before milestone M6 passes its tests.

---

## 1. Design pillars

1. **Commitment is the rock-paper-scissors.** Actions take time. Once started they cannot be cancelled (except by being hit). You read what the enemy has committed to and punish it.
2. **Stats set what reads you can afford.** A perfect read fails if you lack the speed to arrive, the stamina to commit, or the accuracy to hit the zone.
3. **Attrition is a resource war.** Stamina (short-term), fatigue (long-term debt), blood (life), pain/anger/morale/fear (mental layer).
4. **Injuries bypass attrition.** A weak, fast fighter wins by wounding (blood, limbs, head), not by out-lasting a brute.
5. **The AI is deterministic and learnable.** Same situation gives the same action. The player's notebook records patterns and the guide mode teaches from them.
6. **Readable in plain ASCII.** Show commitments (windups, exposed zones, danger tiles), not just numbers.

---

## 2. Conventions

| Term | Meaning |
|---|---|
| **Tick** | Smallest time unit. All actions are measured in ticks. |
| **Turn** | 10 ticks. Used for bleeding, regen and decay rates ("per turn" = per 10 ticks). |
| Character sheet stats | 0 to 20 scale (`strength`, `speed`, `accuracy`, `stamina`, `endurance`). May exceed 20 after level bonus. |
| Pools | `stamina`, `fatigue`, `blood`, `pain`, `anger`, `morale` are 0 to 100 floats. |
| Distance | Chebyshev distance on the grid (8-directional). `gap = distance - weapon_reach`. Sword reach = 1. |
| Zones | `HEAD, TORSO, ARM, HAND, LEG` |

---

## 3. Data model

```python
@dataclass
class Fighter:
    id: str
    name: str
    level: int                 # 1..20
    # base sheet stats (0..20)
    strength: float
    speed: float
    accuracy: float
    stamina_stat: float        # max stamina pool = stamina_stat * 5
    endurance: float           # controls fatigue gain and penalty steepness
    # pools
    stamina: float             # 0..max
    fatigue: float             # 0..100
    blood: float               # 0..100 (starts 100)
    pain: float                # 0..100
    anger: float               # 0..100
    morale: float              # 0..100
    # state
    pos: (int, int)
    action: Optional[ActiveAction]   # current committed action
    last_action_cat: ActionCat
    last_strike_whiffed: bool
    exhausted: bool
    wounds: list[Wound]
    statuses: list[Status]     # dizzy, weak_grip, slowed, winded, disarmed, concussed...
    equipment: Equipment
    personality: Personality | None  # NPC only
    notebook: Notebook | None        # player only
    prefight: PreFightState

@dataclass
class ActiveAction:
    kind: ActionKind           # LIGHT, HEAVY, GUARD, DASH, WALK, RUN, WAIT, BANDAGE, EAT, DASH_LIGHT (macro)
    aim: Zone | None
    dir: (int, int) | None     # for movement
    start_tick: int
    phases: list[Phase]        # [(name, start, end)] windup/active/recovery/raise/hold...
    strike_tick: int | None
    cost_paid: float
    locked_stats: EffStats     # eff stats captured at start (durations are locked in)

@dataclass
class Wound:
    zone: Zone
    severity: GRAZE | CUT | DEEP | SEVERED | BRUISE
    bleed_per_turn: float
    bandaged: bool

@dataclass
class Equipment:
    weapon: Weapon             # edge, reach, weight, speed_mod
    armor: dict[Zone, ArmorPiece]   # armor value per zone, weight
    # total_weight derived

@dataclass
class PreFightState:           # carries over from before the fight
    fatigue: float; stamina: float; blood: float
    injuries: list[Wound]; nervousness: float  # 0..1 accuracy reduction at start

@dataclass
class Personality:             # NPC only (Section 15)
    archetype: str
    weights: dict[str, float]
    base: dict[str, float]
    reaction_delay: int        # ticks
    zone_pref: list[...]
```

`CombatState` = `{tick, fighters: [a, b], rng_seed, event_log}`. It must be deep-clonable cheaply.

---

## 4. Effective stats pipeline

Computed whenever an action starts or a strike resolves. Never stored permanently.

```python
LVL(f)   = 1 + 0.03 * (f.level - 1)
A        = f.anger / 100
F        = fear(f) / 100                      # Section 11
end_eff  = f.endurance * LVL(f)

fatigue_pen(f):
    x = max(0, f.fatigue - 15) / 85
    k = 1.6 - end_eff / 20                    # endurance sets steepness
    return min(0.6, x * k * 0.7)

pain_eff(f)  = f.pain * (1 - min(0.6, f.anger / 150))   # anger suppresses pain
pain_pen(f)  = clamp(pain_eff/100, 0, 1) * max(0.1, 1.2 - end_eff/20)

blood_pen(f):
    if f.blood >= 50: return 0
    if f.blood >= 25: return (50 - f.blood) / 25 * 0.25
    return 0.25 + (25 - f.blood) / 25 * 0.15

eff_speed = f.speed*LVL * (1-fatigue_pen) * (1-0.5*pain_pen) * (1-blood_pen)
            * (1+0.15*A) * status_speed_mult * exhaust_mult(0.6 if exhausted) * gear_speed_mult
eff_str   = f.strength*LVL * (1-0.8*fatigue_pen) * (1-0.5*pain_pen) * (1-blood_pen)
            * (1+0.25*A) * status_str_mult * exhaust_mult
eff_acc   = f.accuracy*LVL * (1-0.7*fatigue_pen) * (1-pain_pen) * (1-blood_pen)
            * (1-0.30*A) * (1-0.20*F) * (1-nervousness) * status_acc_mult
# floors: each eff stat >= 25% of its base. Round for display only, never for logic.
```

`nervousness` starts at `prefight.nervousness` and decays to 0 at 0.5 per turn after the first exchange.

---

## 5. Time and action system

### 5.1 Action table (base values at speed 10)

All phase durations scale: `ticks = max(1, ceil(base * 10 / eff_speed))`, where `eff_speed` is captured at action start. Recovery is further multiplied by `(1 + 0.02 * gear_weight)`.

| Action | Windup | Active | Recovery | Stamina | Power mult | Acc mod | Notes |
|---|---|---|---|---|---|---|---|
| `LIGHT` | 4 | 1 | 5 | 6 | 1.0 | 0 | Strike resolves at `start + windup`. |
| `HEAVY` | 12 | 2 | 6 | 16 | 1.6 | -2 | Unavailable when exhausted. |
| `GUARD` | 3 (raise) | hold until released | 3 (lower) | 0.2/tick while held | n/a | n/a | See Section 8. |
| `DASH` | 2 ticks per tile (max 3 tiles) | n/a | 4 | 8 flat | n/a | n/a | Position updates each tile. Unavailable when exhausted. |
| `DASH_LIGHT` | dash legs, then LIGHT windup | | LIGHT recovery | 8 + 6 | 1.0 | 0 | Macro. Skips dash recovery. |
| `WALK` | 10 ticks per tile | n/a | 0 | 1 | n/a | n/a | Position updates at the **end**. Defenseless while moving. |
| `RUN` | 4 ticks per tile | n/a | 0 | 3 per tile | n/a | n/a | Position updates at the end of each tile. |
| `WAIT` | 5 | n/a | 0 | 0 | n/a | n/a | Bonus regen (Section 9). |
| `BANDAGE` | 30 | n/a | 0 | 0 | n/a | n/a | Cancelled if hit. Cuts one wound's bleed by 60%. |
| `EAT/DRINK` | 20 | n/a | 0 | 0 | n/a | n/a | Cancelled if hit. Fatigue -20 (food), -10 (water). |

Fatigue gain per action: `stamina_cost * (1.5 - end_eff/20) * (1 + 0.03*gear_weight)`.
Stamina cost is multiplied by `(1 + 0.02*gear_weight)`.

### 5.2 Scheduler

```python
def run_until_decision(state):
    """Advance ticks until some fighter needs a decision. Returns the fighters who are free."""
    while True:
        free = [f for f in state.fighters if f.action is None and f.can_act()]
        if free: return free
        state.tick += 1
        advance_one_tick(state)

def advance_one_tick(s):
    apply_passive(s)          # regen, fatigue drain, bleed, decay (Sections 9-11)
    snap = snapshot_defense_states(s)          # phase + exposure at START of tick
    strikers = [f for f in s.fighters if f.action and f.action.strike_tick == s.tick]
    for f in sorted(strikers, key=lambda f: (f.action.windup_len, -eff_speed(f), f.id)):
        if f.action is not None and f.action.strike_tick == s.tick:   # may have been cancelled
            resolve_strike(s, f, other(f), snap)
    # interrupts from this tick are applied AFTER all same-tick strikes resolve (a trade)
    apply_pending_interrupts(s)
    update_movement(s)
    finish_completed_actions(s)
    check_end_conditions(s)
```

**Decision rules**
- A fighter decides only when `action is None`.
- If both fighters are free on the same tick, **both decide from the same pre-tick snapshot**. The AI cannot see the player's choice for that tick.
- If the AI became free earlier, it already committed and the player sees that commitment (this is the readable tell).
- The game pauses for the player whenever the player is free. Auto-advance while the player is busy.

**Interrupt rule:** a damaging hit on a fighter in `WINDUP` (or `BANDAGE`/`EAT`/`DASH`) cancels their action. Stamina already spent is not refunded. A hit on a fighter in `RECOVERY` does not cancel (already finished).

**Movement:** WALK and RUN commit the actor until the tile is entered. While moving, the actor is `DEFENSELESS` (cannot parry/guard, torso and leg exposed). This is the "walking in close range is punished" rule: a WALK tile takes 10 ticks at speed 10, but a LIGHT strikes after 4.

---

## 6. Strike resolution pipeline

```python
def resolve_strike(s, att, dfn, snap):
    act = att.action
    eff = act.locked_stats                      # use stats locked at start for timing, live eff for power
    # 1. RANGE
    if gap(att, dfn) > att.equipment.weapon.reach:
        emit(WHIFF, att); att.last_strike_whiffed = True
        add_anger(att, +CFG.anger_whiff)        # +8
        return
    # 2. ZONE (Section 7)
    zone, drifted = resolve_zone(att, dfn, act.aim, snap[dfn])
    # 3. DEFENSE (Section 8)
    d = snap[dfn].defense           # PARRY_WINDOW | GUARD_HOLD | DEFENSELESS | OPEN
    if zone in GUARD_LINE and d == PARRY_WINDOW and can_parry(att, dfn, act):
        return do_parry(s, att, dfn, act)
    if zone in GUARD_LINE and d in (PARRY_WINDOW, GUARD_HOLD):
        return do_block(s, att, dfn, act)       # may become guard break
    # 4. HIT
    power = eff_str(att) * act.power_mult + att.equipment.weapon.edge
    if is_dashing_attack(act): power *= 1.0     # (no bonus in v1)
    net = power - dfn.equipment.armor[zone].value
    wound = classify_wound(zone, net, act)      # Section 7
    apply_wound(dfn, wound)
    apply_pain(dfn, wound, power)
    apply_morale_hit(dfn, wound)
    if dfn.action and in_windup(dfn.action) or dfn.action.kind in (BANDAGE, EAT, DASH):
        queue_interrupt(dfn)
    add_anger(dfn, +0.5 * pain_added)           # being hit angers
    morale_gain(att, +4)
    att.last_strike_whiffed = False
    emit(HIT, att, dfn, zone, wound, cause_tags)
```

---

## 7. Zones, exposure, targeting, wounds

### 7.1 Zone difficulty and fallback

| Zone | Difficulty (needs `eff_acc` ≥) | Fallback if missed or not exposed |
|---|---|---|
| TORSO | 8 | none (always lands) |
| LEG | 12 | TORSO |
| ARM | 14 | TORSO |
| HEAD | 16 | TORSO |
| HAND | 20 | ARM, then TORSO |

Effective difficulty = `zone.difficulty + acc_mod(action)` where heavy adds 2 (it is harder to aim). If `eff_acc < difficulty`, the strike **drifts** to the fallback zone and is re-evaluated (`HAND → ARM → TORSO`). It almost never whiffs outright. A tired hand-chopper becomes a torso-slasher.

Optional jitter: `eff_acc += seeded_uniform(-J, +J)` with `J = CFG.acc_jitter` (default 1.0, set 0 for fully deterministic tests). The AI's decision table never uses RNG. Only strike accuracy has this jitter.

### 7.2 Exposure (which zones can be targeted right now)

| Defender phase | HEAD/TORSO/ARM/LEG | HAND |
|---|---|---|
| Idle / standing | exposed | **not exposed** |
| WINDUP / RECOVERY of any attack | exposed | **exposed** |
| STAGGER | exposed | **exposed** |
| GUARD raise | exposed (blocked, see §8) | exposed |
| GUARD hold | exposed (blocked, see §8) | exposed with +4 difficulty |
| WALK/RUN/DASH (moving) | exposed | not exposed |

If HAND is not exposed, the strike falls back immediately to ARM. Hand-chopping is a **punish**, not an opener.

### 7.3 Wound classification

`net = power - armor[zone]`

| net | Severity |
|---|---|
| ≤ 0 | BRUISE (no wound, only pain) |
| 1 to 7 | GRAZE |
| 8 to 15 | CUT |
| ≥ 16 | DEEP |
| ≥ 22, HAND zone, heavy attack, clean (not drifted) | SEVERED |

| Zone | GRAZE | CUT | DEEP | Special effects |
|---|---|---|---|---|
| TORSO | bleed 0.5 | bleed 3 | bleed 6, status `winded` (stamina -20) | none |
| LEG | bleed 0.5 | bleed 2, `slowed` (-25% speed, 120 ticks) | bleed 4, `slowed` -40% permanent until healed, no dash | net ≥ 24 heavy: `crippled`, collapses |
| ARM | bleed 0.5 | bleed 2, `weak_grip` (str -20%, acc -10%, 100 ticks) | bleed 4, `weak_grip` -35% | none |
| HAND | bleed 0.5 | bleed 2, `weak_grip` (str -25%, parry window -2, 100 ticks) | bleed 4, weapon dropped (`disarmed`), `weak_grip` | SEVERED: bleed 8, weapon dropped permanently, morale -30 |
| HEAD | bleed 0.5, `dizzy` light (acc -15%, 40 ticks) | bleed 3, `dizzy` (acc -30%, speed -15%, 60 ticks) | bleed 6, stagger 12 ticks, `concussed` (acc -40%, 100 ticks) | net ≥ 24: knocked unconscious |

- `bleed_per_turn` is per 10 ticks and is multiplied by an exertion multiplier (Section 10).
- Unarmed (after losing the weapon hand): power = `eff_str * 0.5`, no sever, only LIGHT attacks. Picking up a weapon takes 20 ticks.
- Wound stacking: bleed rates add. Cap total at 20/turn.

### 7.4 Pain from hits

```python
pain_added = { BRUISE: min(15, 0.3*power), GRAZE: 6, CUT: 14, DEEP: 28, SEVERED: 45 }[severity]
pain_added *= { HEAD: 1.3, HAND: 1.5 }.get(zone, 1.0)
# endurance check: fatigue spike on big pain
fatigue += pain_added * 0.5 * (1.5 - end_eff/20)       # low endurance suffers more
```

---

## 8. Guard, parry, stagger

**Guard** is a stance you hold.
- Raise takes 3 base ticks. Then the guard is in `GUARD_HOLD`. Holding costs 0.2 stamina per tick. Releasing starts a 3-tick lower recovery.
- `GUARD_LINE = {HEAD, TORSO, ARM}`. **Hand and leg strikes bypass guard.** (This is why leg targeting beats turtles.)
- Every blocked hit costs the defender stamina:
  `block_cost = max(2, 4 + (att_eff_str - def_eff_str) * 0.8) * (1.8 if heavy else 1.0)`
- **Guard break** (defender staggers for 8 ticks, all zones exposed) if any of:
  - stamina after cost < 0
  - heavy and `def_eff_str < 0.6 * att_eff_str`
- A block does not cancel the attacker's recovery. A block adds +3 pain to the defender for heavies only.

**Parry** is a timing window at the start of a guard.
- Window = `[guard_ready_tick, guard_ready_tick + W)` where `guard_ready_tick = start + raise_ticks` and
  `W = round(0.15 * eff_speed + 0.10 * eff_acc) - weak_grip_penalty`, minimum 1. Parrying a heavy gets **+2** because its windup is visible.
- `can_parry(att, dfn, act)`: `dfn.eff_str >= 0.4 * att.eff_str` for light, `>= 0.75 * att.eff_str` for heavy.
- A successful parry costs **0 stamina**, staggers the attacker for 6 ticks (all zones exposed), and gives the defender a **riposte**: the defender's next attack has windup halved (rounded up).
- A strike outside the window but while guard is held is a normal block.
- Parried attacker: anger +10, morale -5.

**Stagger**: actor is locked for N ticks. `phase = STAGGER`, all zones exposed (including HAND). Any strike that lands during stagger gets `clean_bonus` (+10% power).

### Rock-paper-scissors matrix (cause tags for logging and the "why" replay)

| Situation | Winner | Cause tag |
|---|---|---|
| Light vs enemy heavy windup (light strikes first) | Light | `LIGHT_INTERRUPTS_HEAVY` |
| Heavy vs guard | Heavy (guard break or big stamina drain) | `HEAVY_BEATS_GUARD` |
| Light vs guard (inside parry window) | Guard | `PARRY_WINDOW` |
| Dash-away vs heavy | Dash (heavy whiffs, you punish recovery) | `DODGE_WHIFF_PUNISHED` |
| Light vs dash-away (dash recovery) | Light | `LIGHT_CATCHES_RECOVERY` |
| Leg/hand strike vs guard | Strike | `GUARD_BYPASSED` |
| Walk into close range | Whoever strikes | `WALK_DEFENSELESS` |

---

## 9. Stamina, fatigue, exhaustion, recovery

- `max_stamina = stamina_stat * LVL * 5`.
- Actions pay stamina at start (Section 5.1) and add fatigue immediately.
- **Regen per turn (10 ticks):** acting or moving `+2` stamina; `WAIT`/idle for ≥3 ticks `+12` stamina.
- **Fatigue drain per turn:** idle/waiting `-3`, walking `-1`, anything else `0`.
- **Zero stamina (exhaustion):** if `stamina < cost`, pay what you have, convert the remainder to fatigue at 1.0 (no endurance discount), set `exhausted = true`. While exhausted: speed and strength x0.6, no dash, no heavy, guard breaks easily. Clear `exhausted` when stamina ≥ 25% of max.
- Rest is only useful when the enemy cannot reach you. The **gap is a resource**.
- Food/water reduce fatigue (Section 5.1).

---

## 10. Blood and bleeding

- Blood starts 100 (or `prefight.blood`). Death at ≤ 0.
- Each tick: `blood -= sum(wound.bleed_per_turn) / 10 * exertion_mult` (bandaged wounds count at 40%).
- `exertion_mult`: WAIT/idle 0.7, WALK 1.0, LIGHT/HEAVY/GUARD 1.3, DASH/RUN 1.6.
- Penalties (see `blood_pen`): below 50 blur and slowdown, below 25 severe.
- **Collapse (unconscious):** `blood <= 15` or (`blood < 25` and `fatigue > 70`). Collapsed fighters are out of the fight.
- This enables "run away until he collapses": an early torso cut makes the wounded fighter's blood a clock, and exertion speeds it up.

---

## 11. Pain, anger, morale, fear

**Pain**: 0 to 100. Decays 1 per turn. Effect via `pain_pen` (Section 4).

**Anger** 0 to 100. Decays 2 per turn (modified by `personality.composure`, default 1.0).

| Event | Anger change |
|---|---|
| Hit taken | `+0.5 * pain_added` |
| Own strike whiffs | +8 |
| Parried | +10 |
| Circled: opponent moves while within 2 tiles of you and you haven't landed a hit in the last 30 ticks | +3 per move |
| Took 2 hits in a row without landing one | +6 |
| Taunt (future) | +10 |

Effects: strength +25%, speed +15%, accuracy -30% at anger 100 (linear). Pain suppression (Section 4). **Anger crash:** when anger falls from above 60 to below 30 in under 20 ticks, or when exhaustion triggers while anger > 50, apply `fatigue += 0.25 * pain` and `stamina -= 10`. The suppressed pain arrives all at once.

**Morale** 0 to 100. Start: `70 + courage_trait - 5 * max(0, opp_level - level)`, clamped.

| Event | Morale |
|---|---|
| CUT taken | -8 |
| DEEP wound | -15 |
| Severed limb | -30 |
| Parried | -5 |
| Blood below 50 (once) | -10 |
| Blood below 25 (once) | -15 |
| Landing a hit | +4 |
| Successful parry | +6 |

**Fear**: `fear = clamp((50 - morale) * 2 - 0.5 * anger, 0, 100)`. Effects: accuracy -20% at 100 (Section 4), AI flee weight rises (Section 15). **Break:** `fear >= 80` for 3 consecutive AI decisions makes the NPC flee or yield. Anger offsets fear, so an enraged enemy won't break.

---

## 12. Gear

```python
Weapon: {edge, reach, weight, speed_mod}       # sword: edge 4, reach 1, weight 3, speed_mod 1.0
ArmorPiece: {value, weight, zones}             # leather 3 / chain 6 / plate 10 per zone
gear_weight = weapon.weight + sum(armor.weight)
gear_speed_mult = max(0.6, 1 - 0.01*gear_weight)
```
- Armor subtracts from `power` before wound classification. A hit that nets ≤ 0 is a BRUISE (pain only). This enables the "rage-bait on armor" loop.
- Weight makes recoveries longer (`x(1+0.02*weight)`), stamina costs higher (`x(1+0.02*weight)`) and fatigue per action higher (`x(1+0.03*weight)`). That is why "naked but fast" is viable.

---

## 13. Levels and XP

- Cap 20. `LVL = 1 + 0.03*(level-1)` applies to all five sheet stats.
- `xp_to_next(L) = round(100 * 1.25^(L-1))`. So 1 to 5 is quick, 19 to 20 is huge.
- Kill XP: `base_xp * clamp((enemy_level / player_level)^2, 0.05, 4.0)`.
- Guided kills: XP x0.7 (Full guide), x0.85 (Hints). Record `guided=true` in the kill log.
- Level never replaces reading. A level gap is ≤ ~57% stat difference at the extremes.

---

## 14. End of fight

Fight ends when any of these happens:
1. `blood <= 0` → death.
2. Collapse (unconscious).
3. **Yield/flee** (morale break, Section 11). A yielded NPC stops attacking and moves away or drops weapon. Player may spare or finish (outside scope: hook only).
4. Player or NPC leaves the arena (flee reaches map exit).

Emit `FIGHT_END(reason, winner)`. Post-fight, carry `PreFightState` (fatigue, blood, injuries) forward to the next fight.

---

## 15. NPC AI

### 15.1 Perception

The AI does **not** read the player's mind or the player's choice on the same tick.
- It knows: distance, own state, player's current phase and committed action **only after the action has been in progress for `reaction_delay` ticks**, player's last action category, player's visible mood (anger band), player's stamina band.
- If the player's action started less than `reaction_delay` ticks ago, the AI treats it as `UNSEEN` and relies on `opp_last_action`.
- Example: Ronin has `reaction_delay = 4`. Dorime's light windup is 2 ticks, so Ronin cannot react to it. Dorime's heavy windup is longer than 4, so Ronin can.

### 15.2 Situation buckets

```python
PublicKey = (gap_band, anger_band, vigor_band, opp_last_cat, opp_state)
FullKey   = PublicKey + (blood_band, fear_band, pain_band)     # hidden vars

gap_band:   0 -> ADJ, 1-2 -> NEAR, 3-5 -> MID, >=6 -> FAR
anger_band: <25 CALM, 25-60 WARM, 60-85 ANGRY, >=85 RAGE
vigor_band: LOW  if stamina% < 25 or fatigue > 60 or exhausted
            HIGH if stamina% >= 60 and fatigue < 30
            else MID
opp_last_cat: LIGHT, HEAVY, GUARD, DASH_IN, DASH_OUT, MOVE, WAIT, NONE
opp_state:  IDLE, WINDUP_L, WINDUP_H, GUARDING, RECOVERING, STAGGERED, EXHAUSTED, WHIFFED
blood_band: >60 OK, 30-60 HURT, <30 CRITICAL
fear_band:  <20 BOLD, 20-60 WARY, >60 SCARED
pain_band:  <20 LOW, 20-50 MID, >50 HIGH
```

The **AI scores using the full state**. The **notebook records only the PublicKey**. This is on purpose: when a hidden variable changes behavior, the player sees a habit break and learns something else matters.

### 15.3 Candidate actions

`LIGHT(zone)`, `HEAVY(zone)`, `GUARD`, `DASH_LIGHT(zone)` (gap 1-3), `DASH_OUT`, `DASH_IN`, `WALK_IN`, `WALK_OUT`, `RUN_OUT` (flee), `WAIT` (rest), `BANDAGE`, `YIELD`.
Availability filters by gap, stamina, exhaustion, and status (e.g. no dash when `slowed deep`).

### 15.4 Scoring (deterministic)

`score(a) = base[a] + sum(rule_bonus(a) * weights[rule])` over the rules below. Pick `argmax`. **Ties** break by the fixed order:
`GUARD, LIGHT, HEAVY, DASH_OUT, WALK_OUT, DASH_LIGHT, DASH_IN, WALK_IN, WAIT, BANDAGE`.

`remaining(opp)` = ticks until the opponent's current recovery/stagger ends. `strike_delay(a)` = ticks from now until action `a` strikes.

| # | Rule (weight key) | Bonus |
|---|---|---|
| R1 | Anger (`rage`) | `HEAVY += 0.6*anger`, `LIGHT += 0.1*anger`, `GUARD -= 0.5*anger` |
| R2 | Punish recovery (`opportunism`) | If `remaining(opp) >= strike_delay(LIGHT)` then `LIGHT += 40`. If `remaining(opp) >= strike_delay(HEAVY)` then `HEAVY += 40`. |
| R3 | Counter seen heavy windup (`reading`) | If my light strikes before his heavy: `LIGHT += 45`. Else if `can_parry_heavy`: `GUARD += 40`. Else `DASH_OUT += 40`. |
| R4 | Counter seen light windup (`reading`) | `GUARD += 25`. If my heavy windup > his strike tick: `HEAVY -= 30`. |
| R5 | Opponent guarding (`pressure`) | `HEAVY += 30`, `LIGHT -= 20`; leg-target bonus (zone choice). |
| R6 | Low vigor | `HEAVY -= max(0, 35 - stamina%) * 1.5` (and `-= 60` if exhausted). If `gap >= 3`: `WAIT += 30`. |
| R7 | Fear (`caution`) | `WALK_OUT/RUN_OUT/DASH_OUT += 0.8*fear`, all attacks `-= 0.3*fear`. |
| R8 | Blood critical | `GUARD += 15`, `DASH_OUT += 20 * (1 - anger/100)`. |
| R9 | Pain high and anger < 40 | `GUARD += 15`, `DASH_OUT += 10`. |
| R10 | Variety (`discipline`) | If the same attack was chosen the last 2 times: `-10` per repeat, times discipline. |
| R11 | Patience (`patience`) | If opponent is bleeding and `opp.blood < 40`: `DASH_OUT/WALK_OUT += 30` (kite and wait). |
| R12 | Closing distance | `gap 1-3`: `DASH_LIGHT += 35 + 0.2*anger`, `WALK_IN += 25`. `gap >= 4`: `WALK_IN += 30`, `RUN_IN += 10`. If opp heavy windup seen at gap 1-3: `WALK_IN -= 30`. |
| R13 | Gap rest | If `gap >= 3` and own fatigue > 40: `WAIT += 25`. |

**Zone choice for attacks** (deterministic): from `personality.zone_pref` in order, pick the first zone that is (a) exposed now or at strike time, and (b) has `eff_acc >= difficulty`. Else TORSO. Example: Ronin prefers `[HAND if opp in recovery, HEAD if opp staggered, LEG if opp guarding, TORSO]`.

### 15.5 Ronin archetype (Brute) data

```json
{
  "archetype": "brute",
  "reaction_delay": 4,
  "base": { "LIGHT": 34, "HEAVY": 20, "GUARD": 18, "DASH_OUT": 5, "WALK_OUT": 0,
            "DASH_LIGHT": 0, "DASH_IN": 0, "WALK_IN": 0, "WAIT": -50, "BANDAGE": -20 },
  "weights": { "rage": 1.3, "opportunism": 0.7, "reading": 0.6, "pressure": 1.0,
               "caution": 0.5, "discipline": 0.2, "patience": 0.1 },
  "composure": 0.8,
  "courage": 10,
  "zone_pref": ["HAND_IF_RECOVERING", "HEAD_IF_STAGGERED", "LEG_IF_GUARDING", "TORSO"]
}
```

### 15.6 Behavior regression tests (expected action only, not scores)

Write each as a unit test. Ronin at gap 0 unless stated. Stamina HIGH unless stated.

| # | Situation | Expected |
|---|---|---|
| E1 | anger 10, opp IDLE | `LIGHT` |
| E2 | anger 70, opp just whiffed and `remaining = 2` | `HEAVY` |
| E3 | anger 70, opp in recovery with `remaining = 12` | `HEAVY` (punish bonus applies) |
| E4 | calm, opp seen WINDUP_H, my light strikes first | `LIGHT` |
| E5 | calm, opp seen WINDUP_H, light too slow, can't parry | `DASH_OUT` |
| E6 | opp GUARDING, anger 50 | `HEAVY` |
| E7 | vigor LOW, gap 4, fatigue 50 | `WAIT` |
| E8 | fear 90, anger 0 | an escape action (`RUN_OUT` or `WALK_OUT`) |
| E9 | gap 2, calm | `DASH_LIGHT` |
| E10 | Same PublicKey run 20 times with identical full state | identical action every time |

---

## 16. Notebook (pattern learning)

Every time an NPC decides, record into the player's notebook (if the player could observe the NPC).

```python
@dataclass
class NotebookEntry:
    key: PublicKey
    counts: dict[ActionCat, int]
    total: int
    last_tick: int
    state: UNKNOWN | TENTATIVE | TENDENCY | HABIT | MIXED
    top_action: ActionCat | None
    hidden_diffs: list[str]      # which hidden vars differed when habit broke
```

```python
def state_of(e):
    if e.total == 0: return UNKNOWN
    top, n = max(e.counts.items(), key=lambda kv: kv[1]); share = n / e.total
    if e.total < 3: return TENTATIVE
    if e.total >= 5 and share == 1.0: return HABIT
    if share >= 0.6: return TENDENCY
    if e.total >= 4: return MIXED
    return TENTATIVE
```

| State | Display | Meaning |
|---|---|---|
| UNKNOWN | `?` | Guide plays safe. |
| TENTATIVE (1-2) | `heavy?` | Seen, not trusted. |
| TENDENCY (3+, ≥60%) | `heavy` plus ghost glyph `R^~` above him | Likely. |
| HABIT (5+, 100%) | `HABIT` plus predicted action on timeline | Reliable. |
| MIXED | `mixed` | Something hidden changed. |

**Habit break:** if an entry in HABIT sees a different action, set `MIXED` and compute `hidden_diffs` by comparing the FullKey of the new sample against the stored FullKeys of previous samples (keep the last 5 FullKeys per entry). Report the first differing dimension, e.g. *"Something else changed: he is badly hurt."* Show the hint only when the dimension is visible to the player (wounds, mood), otherwise show *"something else changed."*

**Trait inference:** after ≥ 6 TENDENCY/HABIT entries sharing a feature, emit a summary line, e.g. *"Aggressive: rarely guards when angry"* (rule: `GUARD` share < 15% across all entries where `anger_band >= ANGRY`). Summaries attach to the **archetype**, so learning transfers to other enemies of the same type.

**Persistence:** notebook is saved per archetype and per named NPC. Exact stats stay vague (`strength: high/medium/low`) until the player has traded blows (≥ 3 exchanges).

---

## 17. Guide mode

Setting: `OFF | HINTS | FULL`.

- **HINTS** names the principle (*"He's winding up a heavy. Light interrupts it."*).
- **FULL** gives action, zone, reason, risk and confidence.

### 17.1 Algorithm

```python
def guide(state, player, notebook):
    enemy = other(player)
    # 1. Predict enemy action
    if enemy.action and visible_to_player(enemy.action):
        predicted, conf = enemy.action, CERTAIN              # visible tell
    else:
        e = notebook.entry(public_key_from_enemy_pov(state))
        if e.state in (HABIT, TENDENCY): predicted, conf = e.top_action, LIKELY
        elif e.state == TENTATIVE:       predicted, conf = e.top_action, GUESSING
        else:                            predicted, conf = None, UNKNOWN
    # 2. Evaluate each affordable player action by forward simulation
    results = []
    for a in legal_player_actions(state):
        for e_act in ([predicted] if predicted else plausible_enemy_actions(state)):
            sim = clone(state); apply(sim, player, a); apply(sim, enemy, e_act)
            run_until_both_free_or(sim, max_ticks=24)
            results.append((a, e_act, evaluate(sim, player)))
    # 3. Aggregate: if predicted, take its result; else take the WORST case over e_act (safe play)
    # 4. Return best + runners-up when scores are within 10%
```

`evaluate(sim, me)`:
```
+ 1.0 * enemy_blood_lost
+ 0.3 * enemy_pain_added
+ 0.3 * enemy_morale_lost
+ 0.3 * enemy_fatigue_gained
+ 25  * enemy_severed_or_maimed
+ 40  * enemy_unconscious_or_dead
- 1.2 * my_blood_lost
- 0.3 * my_fatigue_gained
- 0.2 * my_stamina_spent
- 15  * my_stagger
```

**Hard rule: the guide never reads the AI's hidden weights or FullKey.** It only uses the notebook and visible tells. With an empty notebook it says *"Unknown. Keep distance and watch."* and gets sharper as the notebook fills. It is a tutor, not a cheat.

### 17.2 Output

```python
GuideAdvice {
    action, zone,
    why: list[str],         # derived from simulated event cause tags (Section 8 table)
    risk: list[str],        # e.g. "ACC 17/20 for hand, may drift to arm"
    confidence: CERTAIN | LIKELY | GUESSING | UNKNOWN,
    alternatives: list[...]
}
```

Example:
```
GUIDE: Light -> hand
Why: his heavy is in windup (hand OPEN); you land t5, he lands t9.
Risk: ACC 17/20, may drift to arm.
Confidence: certain (visible tell)
```

Respect stats: if the best play needs `eff_acc` it lacks, state the shortfall and offer the safer option.

### 17.3 "Why did that happen?" replay line

After each exchange show one line built from the cause tag table, e.g. *"You guarded, he used heavy: heavy beats guard."* Store the last 5 for the log.

---

## 18. UI/UX spec (ASCII, mobile-first)

Width target: 40 columns. The engine exposes a `RenderModel`; the UI formats it.

### 18.1 Layout

```
+--------------------------------------+
|RONIN Lv18 ANGRY            [?]       |
|STA [######----] FAT [##--------]     |
|ACT HEAVY [>>>>>>....] hand:OPEN      |
|--------------------------------------|
|  #  #  #  #  #  #  #  #  #  #       |
|  #  .  .  .  .  .  .  .  .  #       |
|  #  .  @  .  !  !  R^~ .  .  #      |
|  #  .  .  .  !  !  .  .  .  #       |
|  #  #  #  #  #  #  #  #  #  #       |
|--------------------------------------|
|t     0    5    10   15               |
|YOU   dddlL--                         |
|RON   hhhhhhhhhH-----   (HABIT)       |
|--------------------------------------|
|BLD [##########] STA [#####-----]     |
|FAT [###-------] SPD -15%             |
|PAIN [#---] ANGER [##--] MORALE [####]|
|torso v3/t                            |
|--------------------------------------|
|Ronin heavy whiffs. Anger +8.         |
|GUIDE: Light -> hand (certain)        |
|[Light][Heavy][Guard][Dash][Move][Wait]|
+--------------------------------------+
```

### 18.2 Components

1. **Enemy status block** (always visible): name, level, top mood tag, STA bar, FAT bar, **action bar** (windup progress; length encodes light vs heavy), exposed zone list (`hand:OPEN`). Long-press (mobile) or hover (desktop) shows the full sheet: strength, speed, accuracy, anger, pain, morale (hidden values appear as `?`/vague until learned).
2. **Sprite glyphs:** `R` idle, `R/` light, `R^` heavy raised, `R]` guard, `R>` dash, `R~` staggered, `R!` dead. Append `~` for a ghost (predicted, not committed) prediction when notebook state ≥ TENDENCY.
3. **Danger outline:** every tile the enemy's current committed action will reach is drawn with `!`. This is the most important readability tool.
4. **Timeline strip** (key piece): generated from scheduled phases for both fighters for the next ~20 ticks. Characters: lowercase windup (`l`, `h`, `g`, `d`), uppercase strike tick (`L`, `H`, `G`), `-` recovery, `.` idle, `~` predicted. Shown when previewing an action. Setting to hide it for experts.
5. **Action menu** (bottom): `[Light][Heavy][Guard][Dash][Move][Wait][More]`. Choosing an attack opens the zone sub-menu: `[h]ead [t]orso [a]rm [x]hand [l]eg` with difficulty and `OPEN/closed` shown next to each.
6. **Action preview** (before confirm, double-tap or Enter to confirm):
   ```
   > Dash + Light -> hand
     Cost 14 STA  +13 FAT
     Lands t5 (his heavy lands t9)
     Hand needs 20 ACC, you have 17: may drift to arm
   ```
7. **Your HUD:** BLD (biggest, the only killer), STA, FAT, PAIN, ANGER, MORALE. Wounds listed with bleed rate (`torso v3/t`). Bars tint when a penalty is active: FAT yellow → red, with a label such as `SPD -15%`. Provide a no-color fallback using symbols (`!`, `!!`).
8. **Floating popups** (one word on the tile, 1 turn): `PARRY!`, `GUARD BREAK`, `STAGGER`, `HAND SEVERED`, `WHIFF`, `+pain`, `-armor`. Big events pause one beat and flash a `*` frame.
9. **Combat log:** 2 lines max on mobile (full log in a pull-up panel).
10. **Notebook / bestiary screen:** list entries as `angry + adjacent + you whiffed -> heavy x5 [HABIT]`, trait summary at the top.
11. **Guide mode panel:** the `GUIDE:` block above the action menu, toggleable. Confidence tag colored.
12. **Pacing:** the game pauses at every player decision. Auto-advance while the player is busy. Optional "step 1 tick" key for learning.

### 18.3 Fight-start flow
1. Show both status blocks with `?` for hidden stats, mood tag, and the opening distance.
2. If the notebook has data for this archetype, show 1-2 known traits as a pre-fight hint.

---

## 19. Event log

Every state change emits a typed event with cause tags. UI, "why" replay, notebook and tests all consume this one log.

```python
Event {tick, type, actor, target, zone, severity, deltas: dict, cause_tags: list[str]}
types: ACTION_START, WHIFF, HIT, BLOCK, PARRY, GUARD_BREAK, STAGGER, WOUND, SEVER,
       INTERRUPT, EXHAUST, ANGER_CRASH, FEAR_BREAK, FIGHT_END
```

---

## 20. Config (single source of truth)

```yaml
time:        { ticks_per_turn: 10 }
stamina:     { pool_per_stat: 5, regen_active_per_turn: 2, regen_idle_per_turn: 12,
               exhaust_recover_pct: 0.25, exhaust_mult: 0.6 }
fatigue:     { deadzone: 15, k_base: 1.6, pen_cap: 0.6, gain_base: 1.5,
               drain_idle: 3, drain_walk: 1 }
blood:       { start: 100, collapse: 15, penalty_start: 50, exertion: {idle: 0.7, walk: 1.0, fight: 1.3, dash: 1.6},
               bandage_reduction: 0.6, total_bleed_cap_per_turn: 20 }
accuracy:    { jitter: 1.0 }
anger:       { whiff: 8, parried: 10, circled: 3, no_return_hits: 6, decay_per_turn: 2,
               str_bonus: 0.25, spd_bonus: 0.15, acc_penalty: 0.30, pain_suppress_cap: 0.6 }
morale:      { start: 70, cut: -8, deep: -15, sever: -30, parried: -5, land_hit: 4, parry_success: 6 }
fear:        { break_threshold: 80, break_decisions: 3 }
level:       { stat_per_level: 0.03, xp_growth: 1.25, xp_base: 100, kill_ratio_clamp: [0.05, 4.0],
               guided_xp: { hints: 0.85, full: 0.7 } }
zones:       { difficulty: {TORSO: 8, LEG: 12, ARM: 14, HEAD: 16, HAND: 20},
               heavy_acc_mod: 2, guard_hand_extra: 4, guard_line: [HEAD, TORSO, ARM] }
wound_net:   { graze: [1, 7], cut: [8, 15], deep: 16, sever: 22, unconscious_head: 24 }
guard:       { raise: 3, lower: 3, hold_cost_per_tick: 0.2, parry_speed_coef: 0.15, parry_acc_coef: 0.10,
               heavy_window_bonus: 2, parry_str_ratio: {light: 0.4, heavy: 0.75},
               break_str_ratio: 0.6, break_stagger: 8, parry_stagger: 6 }
gear:        { recovery_per_weight: 0.02, stamina_per_weight: 0.02, fatigue_per_weight: 0.03,
               speed_per_weight: 0.01, speed_floor: 0.6 }
ai:          { reaction_delay_default: 4, tie_order: [GUARD, LIGHT, HEAVY, DASH_OUT, WALK_OUT,
               DASH_LIGHT, DASH_IN, WALK_IN, WAIT, BANDAGE] }
notebook:    { tentative_min: 1, tendency_min: 3, tendency_share: 0.6, habit_min: 5, mixed_min: 4 }
```

Action table (Section 5.1) and wound table (Section 7.3) also live in config.

---

## 21. Testing and balance

### 21.1 Unit tests (must pass before UI work)

1. **Determinism:** same seed and same inputs produce identical event logs (run 100 times).
2. **Clone isolation:** cloning a state and simulating the clone never alters the original.
3. **Timing:** at speed 20, LIGHT windup = 2 and recovery = 3; at speed 14, HEAVY windup = 9.
4. **Fatigue gain:** Dorime (endurance 11) gains `cost*0.95`; Ronin (endurance 20) gains `cost*0.5`.
5. **Interrupt:** a light strike landing in a heavy windup cancels it.
6. **Parry window:** a light striking inside the window is parried (no stamina cost); one tick outside is a block.
7. **Guard line:** leg and hand strikes bypass guard; arm strike is blocked.
8. **Zone drift:** `eff_acc = 17`, aiming HAND (20) lands ARM; `eff_acc = 13`, aiming ARM lands TORSO.
9. **Exposure:** HAND aimed at an idle defender falls back to ARM.
10. **Exhaustion:** at 0 stamina the actor cannot DASH or HEAVY; stats x0.6.
11. **Blood:** a torso CUT (3/turn) at exertion 1.0 drains 0.3 blood/tick.
12. **Anger crash** triggers per Section 11.
13. **Notebook state machine** transitions for all states, including habit break.
14. **Behavior tests E1 to E10** (Section 15.6).
15. **Guide never reads hidden state:** test that guide output is identical when AI weights are changed but notebook and visible state are the same.

### 21.2 Worked example (use as an integration test)

Both level 1 (`LVL = 1.0`), fresh, gap 3 (distance 4), no armor, swords (edge 4).

| | Dorime | Ronin |
|---|---|---|
| strength | 10 | 18 |
| speed | 20 | 14 |
| accuracy | 20 | 16 |
| stamina_stat | 20 (pool 100) | 20 (pool 100) |
| endurance | 11 | 20 |

Timings: Dorime scale 0.5, Ronin scale 0.714.
- Dorime `DASH_LIGHT`: dash 3 ticks (3 tiles), LIGHT windup 2 → **strike at t5**. Cost 14 stamina, fatigue +13.3.
- Ronin `HEAVY`: windup 9 → **strike at t9**. Cost 16 stamina, fatigue +8.
- Ronin `GUARD`: ready at t3, parry window `round(0.15*14 + 0.10*16) = 4` ticks → parry window t3 to t6.

Cases (all start at t0, simultaneous decision):
1. **Ronin HEAVY:** Dorime's strike at t5 hits HAND (Ronin in windup, `eff_acc 20 >= 20`). Power `10*1.0+4 = 14`, net 14 → CUT. Hand cut: `weak_grip`, bleed 2/turn, pain `14*1.5 = 21`. Ronin's heavy is cancelled.
2. **Ronin GUARD:** Dorime aims HAND, but hand under guard needs 24, `eff_acc 20 < 24`, falls to ARM → inside `GUARD_LINE` and inside the parry window (t5 in 3 to 6): **PARRIED**. Dorime staggered 6 ticks, Ronin gets a riposte (halved windup).
3. **Ronin LIGHT:** Ronin windup `ceil(4*0.714) = 3`, strike t3, Dorime is mid-dash (defenseless and interruptible): Ronin hits first. Dorime's dash is interrupted.
4. **Ronin WALK/WAIT:** Dorime's strike lands on exposed torso or leg (hand not exposed).

### 21.3 Balance harness

Script: run N=2000 fights per matchup with scripted bots, seeded, jitter on.

Bots: `RandomBot`, `SpamLightBot`, `SpamHeavyBot`, `NaiveGuardBot`, `ScriptedPunisherBot` (reads visible tells and applies the RPS matrix, so it approximates a good human), `KiteBot` (hit and run).

Targets (adjust config until true):
- `ScriptedPunisherBot` as Dorime (Lv10) vs Ronin (Lv10): win rate **45% to 65%**.
- Any single-move spam bot vs Ronin: win rate **< 15%**.
- `KiteBot` forever: wins **< 25%** (fatigue and walking costs must make pure kiting weak).
- Equal level, equal stats mirror match with `ScriptedPunisherBot` vs same bot: **45% to 55%**.
- Level gap of +5 for the better bot should raise its win rate by ~15 to 25 points, never to 100%.
- Median fight length: **20 to 60 turns of decisions** (not a 2-hit coin flip).
- Log the distribution of end reasons (blood loss, collapse, yield). Yield should happen in at least 15% of fights where the loser lost a limb.

---

## 22. Implementation roadmap

| Milestone | Deliverable | Acceptance |
|---|---|---|
| **M0** | Project skeleton, config loader, `CombatState`, seeded RNG, clone, event log | Tests 1, 2 |
| **M1** | Effective stats pipeline (Section 4), stamina, fatigue, exhaustion | Tests 3, 4, 10 |
| **M2** | Action table, scheduler, movement, decision rules, interrupts | Tests 3, 5 |
| **M3** | Zones, exposure, drift, wounds, bleeding, pain | Tests 8, 9, 11 |
| **M4** | Guard, parry, block, guard break, stagger | Tests 6, 7 |
| **M5** | Anger, morale, fear, crash, end-of-fight, gear | Test 12, worked example 21.2 |
| **M6** | NPC AI: perception, buckets, scoring, Ronin archetype | Tests 14, 15 (partial) |
| **M7** | Notebook, trait inference | Test 13 |
| **M8** | Guide mode with forward simulation | Test 15 |
| **M9** | UI: RenderModel, status blocks, timeline strip, preview, HUD, popups, danger outline | Manual playtest on mobile width |
| **M10** | Levels/XP, balance harness, tuning pass | Targets in 21.3 |

**Agent rules for each milestone:** write the tests first, keep functions pure, never read the clock or global RNG, update the config file instead of hardcoding, and keep a `CHANGELOG` of tuned constants with the reason.

---

## 23. Future extensions (out of scope, keep hooks)

- Weapon types (spear reach 2, axe heavier edge, shield as guard modifier, two-handed grips).
- Facing and flanking (circling becomes a real mechanic instead of an anger event).
- Multi-enemy fights (retreat to corridors). Requires the AI to pick a target and the scheduler to handle >2 actors. The engine already loops over `fighters`, so keep it list-based.
- Terrain modifiers (mud slows, doors block).
- Taunt action, intimidation using the fear system.
- Spare/execute mechanic after yield.
