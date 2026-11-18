# Pirates of Miska: Combat Rework Implementation Plan

Status: proposal. Scope: `pirates/combat_core.py`, `pirates/combat.py`, `content/combat_arena.json`, `play.py`, tests and tools.
All numeric values are starting points for tuning, not final balance.

---

## 1. Goals and non-goals

### Goals
1. **Deadly, readable duels.** One clean slash can kill, maim, or knock a fighter unconscious. Fights are decided in 2 to 4 landed hits.
2. **No free hit-and-run.** Disengaging costs time, stamina, or exposure. A slower enemy is not enough to beat the system.
3. **Turn-based, no time pressure.** Keep the tick scheduler and `run_until_player()`. Add no real-time requirements.
4. **Real mouse and keyboard control.** Run toggle, dash with a direction, click-to-move, hover previews.
5. **A UI that explains itself.** The player always knows what each action will cost and what the enemy will do before confirming.

### Non-goals
- Real-time or action-game input.
- Rebuilding boarding or ship projection (the adapter keeps working).
- Persistent wounds across arenas (stay arena-temporary, except the new capture outcome).
- Editing vendored `engine/` files.

---

## 2. Diagnosis (why it feels wrong today)

| # | Problem | Cause in code/doc | Effect |
|---|---|---|---|
| 1 | Every attack is escapable by walking away | `telegraph_windup()` extends windup to cover the captain's ready delay plus a real escape step plus one tick | Attacks never threaten; enemy speed is irrelevant |
| 2 | Hit-and-run is free | Walk is the same cost in every direction; no lunge; attacks lock tiles at commit | Kiting beats slow or average enemies |
| 3 | Dash and run seem missing | `D` dashes along an invisible eight-way facing; run is Shift+arrow; no preview; retreat is free so they are never needed | Features exist but are undiscoverable and pointless |
| 4 | Fights are attrition, not lethal | HP pool with armor subtraction; wound tiers are cosmetic add-ons | No "one slash ends it" |
| 5 | UI redundancy | Left panel, overhead sentence labels, sidebar controls list, bottom prompt, countdown numbers on tiles | Information overload, no clear hierarchy |
| 6 | Too many keys | About 25 bindings | High learning cost |

---

## 3. Target design

### 3.1 Time model
- Keep integer ticks, the event queue, tokens, and `run_until_player()`.
- The player always gets unlimited thinking time.
- Replace "everything is escapable" with "every attack has at least one counter" (see 3.6).

### 3.2 Movement economy

All times are base ticks at speed 100, converted with the existing `ceil(cost * 100 / speed)`.

| Move | Time per tile | Stamina | Notes |
|---|---|---|---|
| Walk forward (toward target) | 90 | 0 | |
| Sidestep (perpendicular) | 110 | 0 | |
| Backstep (away, still facing target) | 150 | 0 | Guard stays up |
| Turn and flee (move away, facing changes) | 100 | 0 | Exposes back (see 3.4) |
| Run | 60 | 8 per tile | Stopping costs 40 recovery. Off-balance state for the next action |
| Dash (up to 3 tiles, any cardinal direction) | 80 total | 35 | 120 recovery. No parry or guard during recovery. Off-balance |
| Wait | 50 | restores stamina | |

Rules:
- **Position changes on arrival.** A tile step occupies the old tile until its arrival event. An attack resolving before that tick hits the old tile.
- **Lunge.** Every melee pattern gets a `lunge` value (0 to 2). A committed attack may include a forward move that extends reach by that many tiles. A single backstep no longer dodges a thrust.
- **Stamina scaling.** Below 30% stamina: step time +25%, windup +15%, aim drift (3.3) increases. At 0: no run, no dash, no heavy attacks.
- **Enemies obey the same costs.** A slow enemy is slower per tile but also cannot run or dash forever. AI refuses to chase when winded.
- **Engagement.** If an enemy is adjacent and ready, a backstep leaves you in its lunge reach (tick math, no special rule needed).

Safe-disengage test (used by UI warnings and AI):
```
safe = my_arrival_tick <= enemy_ready_tick
       OR dest_tile not in enemy.reach(lunge) at enemy_resolve_tick
```

### 3.3 Accuracy via balance (deterministic)
- Add `balance` to `Actor` (0 to 100). It drops after run stop, dash recovery, being shoved, or low stamina, and recovers while waiting or walking.
- Off-balance attacks add **aim drift**: the intended body part may shift to an adjacent part (head to arm, torso to arm/leg).
- Drift uses the seeded combat RNG, so replays stay deterministic. The chance is shown in the UI.

### 3.4 Contact quality and wounds

Replace the single HP pool with:
- **Blood** (drains through bleeding wounds). Zero means death.
- **Shock** (rises from pain, head trauma, blood loss). At the threshold the actor drops unconscious.
- **Part structure** per body part (head, torso, L/R arm, L/R leg), each with its own damage state.

**Contact quality** is computed at resolution from geometry:

| Quality | Condition |
|---|---|
| Clean | Target in the pattern's sweet-spot band, stationary, approaching, or in recovery |
| Graze | Target arrived on the edge tile, or stepped away during windup |
| Glancing | Target parried, braced, or off-axis |
| Back hit | Attacker is behind the target's facing. Not parryable. Severity +1 tier |

**Severity** = f(weapon damage type, contact quality, swing weight, body part, armor coverage). Armor lowers severity by one tier per coverage level.

| Severity | Effect |
|---|---|
| Graze | Small bleed |
| Cut | Bleed + pain |
| Deep | Heavy bleed + large shock. Arm: weapon dropped or arm disabled. Leg: fall, cannot move for N ticks |
| Severe | Unarmored head or torso: kill or knockout (see below) |

Head/torso severe rolls use the seeded RNG weighted by quality: a clean heavy head hit is a kill or KO, a glancing one is a deep cut. Target tuning: about 1 in 5 clean hits is immediately decisive, and a full fight lasts 2 to 4 landed hits.

**Outcomes:**
- Dead (blood 0, or severe kill).
- Unconscious (shock threshold). Arena ends for that actor. For the captain this triggers a defeat outcome (3.7).
- Fled and Surrendered (morale, see 3.5).

### 3.5 Enemy morale
- Each personality has a `nerve` threshold. Morale drops on: ally down, own deep wound, being flanked, low blood, back hit.
- Break behavior: flee, surrender, or fight on, chosen by personality.
- Surrendered enemies feed the existing capture flow.

### 3.6 Counter rule (replaces walk-escapability)
Every damaging pattern must have at least one valid counter:
- **Quick patterns** (windup around 60 ticks): not escapable by stepping, but parryable or blockable.
- **Heavy patterns** (windup around 150 ticks): dodgeable (sidestep or dash), break a plain guard, but parryable.
- **Feints** (zero damage): bait guards and dodges. Punishable during their recovery.
- `telegraph_audit()` verifies a counter exists per damaging pattern, and that the exact counter timing is achievable given the player's costs.

### 3.7 Defeat consequences (pirate flavor)
When the captain is unconscious: captured, robbed (items or gold), or left for dead (injury persists in the world layer, optional). Plug into the existing capture flow. Boarding keeps its continuation behavior.

### 3.8 Tracking telegraph (optional, Phase 4)
Two-phase tell: during the first ~65% of windup, the footprint re-aims at the captain's tile whenever the captain acts, shown as yellow. At the lock tick a new frozen `Intent` replaces it (still immutable, replaced not mutated), shown as red. Skip if it feels redundant after Phase 1 tuning.

---

## 4. Controls

### 4.1 Keyboard

| Key | Action |
|---|---|
| Arrows / WASD | Move one tile (cost per 3.2) |
| `` ` `` or Caps | Toggle run mode. Holding Shift inverts it temporarily |
| `D` | Arm dash. The 4 possible landing lines highlight. Press an arrow to commit, Esc to cancel |
| Ctrl + arrow | One-chord dash |
| Tab | Cycle target. Facing auto-snaps to the selected target |
| Space / F | Light / heavy pattern |
| G | Brace / parry. Hold for block, tap for parry window |
| Q | Shove |
| Z | Cycle body part. Also number keys 1 to 5 as direct picks |
| `.` | Wait |
| I | Help overlay |
| Enter | Confirm previewed action. Esc cancels |

Weapon switch, reload, doors, smoke, and bottle move into a **radial/context menu** (right-click or `C`), leaving the main bindings small.

### 4.2 Mouse
- **Hover a tile:** shows the path, total time cost, stamina cost, and a red marker if an enemy acts before arrival.
- **Left-click tile:** walk there (or run if run mode is on).
- **Shift + click** (or double-click) on a straight line of up to 3 tiles: dash.
- **Click enemy:** select. **Click again:** attack with the active pattern.
- **Click a body-part segment** on the enemy body diagram: choose target part. Wheel cycles patterns.
- **Right-click:** context menu (inspect, door, smoke, switch weapon).

### 4.3 Preview and commit
Any action first shows a ghost marker at the destination plus warnings. Confirm with Enter or a second click. A setting `confirm_actions` (default on) allows skipping the preview for fast play. The preview is computed read-only by a pure function (see 6.2).

---

## 5. UI plan

### 5.1 Remove or demote
- Overhead sentence labels (`CALM · CUTLASS THRUST 2`) become a small icon.
- Right sidebar control list moves to the Help overlay.
- Top-left panel merges into the status strip.
- Countdown numbers on threat tiles removed (kept in debug).
- Utility scores stay debug only.

### 5.2 Keep four layers
1. **Map layer:** threat tiles, yellow while tracking, red once locked, with an impact flash. Dash/run landing ghosts. Aim lines stay as dots.
2. **Status strip (bottom):** blood, shock, stamina, balance, plus a small body glyph that highlights injured parts only. Foe summary beside it for the selected enemy.
3. **Timeline strip:** horizontal bar of upcoming events: "You: attack ends T+110", "Foe: lunge hits T+90, HE HITS FIRST". This is the key addition for a tick-based system.
4. **Log line:** one line with the reason ("Retreated too slowly, he lunged").

### 5.3 Enemy icons
Above each enemy: attacking, guarding, recovering, **open** (punishable). Open should be unmistakable (distinct color and shape, not only color, for monochrome mode).

### 5.4 Feedback
- Distinct effects for hit, parry, whiff, graze, and knockout.
- Short hit-stop (frame hold) and optional screen nudge, with a settings toggle.
- Dash leaves a trail. Run shows a different cursor and footstep marks.

---

## 6. Implementation phases

### Phase 0: Baseline and exploit proof (1 to 2 days)
Goal: measure the problem before changing anything.

Tasks:
- [ ] Add `tools/combat_headless.py --bot kiter` (attack once, backstep, repeat) and `--bot runner` (move away only).
- [ ] Run kiter against every archetype and record win rate and damage taken.
- [ ] Save baseline numbers to `docs/combat_baseline.json`.
- [ ] Add the fairness and kiter checks to the test suite as expected-failing (xfail) tests.

Exit criteria: documented baseline showing the kiter wins.

### Phase 1: Movement economy, lunge, stamina (3 to 5 days)
Files: `combat_core.py`, `combat_arena.json`, `combat.py`, tests.

Tasks:
- [ ] `step_cost(actor, direction, mode)` in `combat_core.py` covering forward/side/back/flee/run/dash (table 3.2). Replace the movement part of `movement_speed()` callers.
- [ ] `Actor`: add `balance`, `move_mode` (`walk|run`), `facing_locked_to` (target id).
- [ ] Movement events apply position on arrival; verify the old tile stays occupied until then.
- [ ] `lunge` field per pattern in `combat_arena.json`. Extend `attack-shape tile generation` (line ~352) to add lunge tiles with correct LOS and occupancy checks.
- [ ] Run toggle and stopping recovery. Dash as a command with a free cardinal direction (not eight-way facing).
- [ ] Stamina thresholds (30% penalty, 0% lockout) in action legality (`~386`).
- [ ] Auto-facing to the selected target. Flee detection (movement away without facing change = flee).
- [ ] Adapter: new operations `toggle_run`, `dash(dir)`, `preview(action)`.
- [ ] Update `telegraph_audit()` to the counter rule (3.6). Remove the "walk escape" extension in `telegraph_windup()` for damaging intents. Keep a minimum windup above one decision interval so quick attacks stay parryable.

Tests:
- [ ] Backstep from an adjacent ready enemy takes a lunge hit.
- [ ] Kiter bot does not beat equal-speed enemies (flip the xfail).
- [ ] Runner gets exhausted or caught within N tiles.
- [ ] Position-on-arrival ordering regression.
- [ ] Determinism: same seed and commands produce identical snapshots.

Exit criteria: kiter loses or breaks even vs all archetypes, and replay determinism holds.

### Phase 2: Wounds, contact quality, balance, morale (5 to 8 days)
Files: `combat_core.py`, `combat_arena.json`, `combat.py`.

Tasks:
- [ ] New wound model: blood, shock, per-part structure in `Actor`. Keep legacy HP fields as derived read-only values for save compatibility.
- [ ] `contact_quality(intent, attacker, defender)` pure function using geometry, defender state, facing, and balance.
- [ ] `severity(...)` lookup table in JSON (weapon type x quality x part x armor).
- [ ] Rewrite `apply_damage()` and `resolve()` branches: parry, shield, back hit, shove interactions retained.
- [ ] Aim drift using the seeded RNG. Expose `drift_chance` for the UI.
- [ ] Unconscious and death transitions, and fight-end conditions.
- [ ] Morale and break logic hooked into AI (flee/surrender thresholds).
- [ ] Bleeding as blood drain at tick boundaries (`advance_time()`), bandage still stops it.
- [ ] Arm hit drops weapon (pickup optional). Leg deep hit forces a fall.

Tests:
- [ ] Table-driven severity tests for each quality x part.
- [ ] Statistical test over N=1000 seeded clean heavy swings: decisive rate in 15 to 25%.
- [ ] Armor tier reduction.
- [ ] Unconscious captain triggers defeat path in the adapter.
- [ ] Morale break leads to flee or surrender and `finished` handles both.

Exit criteria: average landed hits per fight between 2 and 4 in AI-vs-AI runs.

### Phase 3: UI/UX rebuild and controls (5 to 7 days, can overlap with Phase 2)
Files: `play.py`, `combat.py` (HUD builders), and the key map.

Tasks:
- [ ] New key map (4.1) at `_pirates_world_action_for_key()`. Dash-arm mode with a highlight state.
- [ ] Mouse handling (4.2) and the hover preview using read-only `CombatArena.preview(action)`.
- [ ] Preview ghost rendering in `_frame_cell_extras()`. Confirm/cancel flow.
- [ ] Timeline strip renderer from the event queue (read-only).
- [ ] New status strip, enemy icons, log line. Remove or demote old layers (5.1).
- [ ] Help overlay with all controls and a short glossary.
- [ ] Monochrome-safe shapes for every state.
- [ ] Settings: `confirm_actions`, `hit_stop`, `show_timeline`, `monochrome`.

Tests:
- [ ] Rendering purity: snapshot identical before and after drawing (extend `tools/preview_combat.py`).
- [ ] Preview does not mutate state or the replay.
- [ ] Key map tests for each binding and the dash-arm flow.
- [ ] Visual review checklist: nothing clipped, no labels crossing into the sidebar, readable in monochrome.

Exit criteria: a new tester can dash, run, and parry within 5 minutes without reading the help.

### Phase 4: AI and tracking telegraph (4 to 6 days)
Files: `combat_core.py` (`ai()`), `combat_arena.json` (personalities).

Tasks:
- [ ] AI scores use `step_cost` and the safe-disengage formula. Refuse to chase when winded.
- [ ] Seeded randomness in action choice (weighted choice among top scores) so patterns are not memorizable.
- [ ] Habit reading: track the player's last N actions. If the player retreats after attacking, raise lunge/pressure score.
- [ ] Feints that bait dodges and guards. Pressure behavior that cuts off retreat paths using pillars and walls.
- [ ] Optional: tracking telegraph (3.8) behind a config flag.
- [ ] Personality tuning pass with the new morale system.

Tests:
- [ ] `--ai-vs-ai` matchup matrix stays balanced (no archetype above a set win-rate band).
- [ ] Enemy habit adaptation test against the kiter bot.
- [ ] Determinism with the seeded weighted choice.

### Phase 5: Tuning, content, polish (ongoing)
- [ ] Tune with `--ai-vs-ai` and human playtests. Keep numbers in JSON only.
- [ ] Arena layouts with pillars and walls that limit straight retreats.
- [ ] Defeat outcomes (3.7) wired into capture/robbery.
- [ ] Add weapon-type variety: blunt (high shock, low bleed), point (deep, narrow), edge (bleed).
- [ ] Update `tests/test_combat.py`, the combat doc, and `tools/preview_combat.py`.

---

## 7. Data schema changes (`content/combat_arena.json`)

Pattern additions:
```json
{
  "cutlass_thrust": {
    "damage_type": "point",
    "windup": 80,
    "cost": 90,
    "recovery": 70,
    "lunge": 1,
    "sweet_spot": [1, 2],
    "swing_weight": 1.0,
    "parryable": true,
    "counters": ["parry", "block", "sidestep"]
  }
}
```

New top-level sections:
```json
{
  "movement": {
    "walk_forward": {"time": 90, "stamina": 0},
    "sidestep": {"time": 110, "stamina": 0},
    "backstep": {"time": 150, "stamina": 0},
    "flee": {"time": 100, "stamina": 0},
    "run": {"time": 60, "stamina": 8, "stop_recovery": 40},
    "dash": {"time": 80, "stamina": 35, "max_tiles": 3, "recovery": 120}
  },
  "stamina_penalties": {"low_threshold": 0.3, "step_time_mult": 1.25, "windup_mult": 1.15},
  "severity": { "...": "weapon_type x quality x part x armor_tier -> tier" },
  "wounds": {
    "shock_unconscious": 100,
    "bleed_per_tick": {"graze": 0.2, "cut": 0.6, "deep": 1.6},
    "decisive_chance": {"clean_heavy_head": 0.4, "clean_heavy_torso": 0.25}
  },
  "morale": {"ally_down": -20, "deep_wound": -25, "flanked": -10, "back_hit": -15}
}
```
Personalities gain `break_threshold` and `break_behavior` (`flee|surrender|fight`).

---

## 8. Core code sketches

### 8.1 Step cost
```python
def step_cost(actor, direction, mode, target):
    kind = classify(actor, direction, target)   # forward|side|back|flee
    base = MOVEMENT[kind]["time"]
    stamina = 0
    if mode == "run":
        base = MOVEMENT["run"]["time"]
        stamina = MOVEMENT["run"]["stamina"]
    if actor.stamina_ratio() < STAMINA["low_threshold"]:
        base *= STAMINA["step_time_mult"]
    if actor.status("crippled_leg"):
        base *= 1.5
    return ceil(base * 100 / actor.speed), stamina
```

### 8.2 Safe-disengage check (read-only, used by UI and AI)
```python
def is_safe_move(combat, actor, dest):
    arrive = combat.tick + path_time(actor, dest)
    for foe in combat.threats_to(actor):
        resolve_tick = foe.intent.resolve_tick if foe.intent else foe.ready_tick + foe_windup(foe)
        reach = foe.reach_tiles(lunge=True)
        if resolve_tick < arrive and dest_or_current_tile_in(reach, actor, dest, arrive, resolve_tick):
            return False
    return True
```

### 8.3 Contact quality
```python
def contact_quality(intent, attacker, defender):
    if behind(attacker, defender):
        return "back"
    if defender.state in ("parrying", "bracing") and not intent.pattern.unparryable:
        return "glancing"
    d = tile_distance(attacker.pos, defender.pos)
    lo, hi = intent.pattern.sweet_spot
    if lo <= d <= hi and not defender.moved_away_since(intent.start_tick):
        return "clean"
    return "graze"
```

### 8.4 Preview (pure)
```python
def preview(combat, action):
    clone = combat.snapshot_light()      # no queue mutation, no replay write
    ghost = simulate_commit(clone, action)
    return {
        "dest": ghost.dest, "time": ghost.time, "stamina": ghost.stamina,
        "enemy_acts_first": ghost.enemy_first, "will_be_hit": ghost.hit_risk,
        "drift_chance": ghost.drift,
    }
```

---

## 9. Save and compatibility
- Arena state is not saved mid-fight, so the new wound model has no save impact.
- Legacy HP and combat progression fields remain as derived or inert fields.
- Boarding adapter: map the new outcomes (dead, unconscious, fled, surrendered) onto the existing victory/retreat/capture logic. Add the new "captain unconscious" branch.
- Bump a `combat_ruleset_version` in the replay data so old replays are not mixed with new rules.

---

## 10. Test and validation matrix

| Area | Test | Pass condition |
|---|---|---|
| Exploit | Kiter bot vs all archetypes | Kiter win rate below 40%, never dominant |
| Exploit | Runner bot | Exhausted or caught within 12 tiles on arena size |
| Fairness | `telegraph_audit()` counter rule | Every damaging pattern has at least one achievable counter |
| Lethality | 1000 seeded clean heavy swings | Decisive 15 to 25% |
| Lethality | AI-vs-AI fights | 2 to 4 landed hits on average |
| Determinism | Same seed + commands | Byte-identical snapshots |
| Purity | Render and preview | State and replay unchanged |
| UX | Fresh-player test | Dash and run discovered in under 5 minutes |
| Boarding | Existing boarding tests | Pass with new outcomes mapped |

Commands to keep green:
```powershell
python -m pytest -q tests/test_combat.py
python tools/combat_headless.py --fairness-audit
python tools/combat_headless.py --bot kiter --all-archetypes --seed 2026
python tools/combat_headless.py --ai-vs-ai --matches 90 --seed 2026
python tools/preview_combat.py --output <dir>
```

---

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Too lethal, frustrating | Tune `decisive_chance` in JSON. Add a "forgiving" difficulty setting that downgrades the first severe hit |
| Backstep penalty makes retreat useless | Keep dash and run as real escapes. Allow guard-retreat (backstep while guarding) with reduced penalty |
| Timeline strip adds clutter | Collapsible, default on in preview mode only. Playtest before default |
| Hover preview cost each frame | Cache by (tile, tick, state hash). Recompute only on change |
| Determinism broken by drift RNG | Use per-combat seeded RNG, advance it only inside resolution, never in preview |
| Scope creep | Ship Phase 1 + Phase 3 core controls first. Phases 2 and 4 are gated by Phase 0 metrics |

---

## 12. Suggested order if time is short

1. Phase 0 kiter bot (proves the problem).
2. `step_cost` with backstep penalty and position-on-arrival.
3. `lunge` on patterns.
4. Remove walk-escape extension in `telegraph_windup()`, adopt the counter rule.
5. Run toggle and dash-with-direction with ghost preview.
6. Hide countdown numbers, add the enemy "open" icon, add the one-line log.
7. Phase 2 lethality.

Steps 1 to 6 change how the game feels in about a week. Step 7 delivers the "one slash can end it" promise.

---

## 13. Definition of done
- Hit-and-run loses against every archetype under the kiter test.
- Dash, run toggle, and click-to-move work with previews, in keyboard and mouse.
- A clean hit can kill, disable, or knock out, and the cause is visible in the log.
- The UI shows four layers only, and a new player can play without the help screen.
- All tests and headless audits above pass, with determinism intact.
