# Combat UI/UX Redesign Plan

Status: superseded for the default sword-duel route by `COMBAT SPEC.md`.
`pirates/duel_system/` and `pirates/duel_ui.py` now implement that route.
The record below describes the older boarding/group arena and its follow-ups;
see the current section of `COMBAT-SYSTEM-GUIDE.md` for shipped sword-duel controls.

Historical status: implemented with documented follow-ups; see `COMBAT-SYSTEM-GUIDE.md`
for shipped behavior and the verification commands. This document remains the
acceptance/design record; unchecked items below are follow-ups, not delivered claims.
Scope: tactical arena combat, its controls, feedback, inspection and enemy setup.

## 1. Desired player experience

Within a glance, the player should understand:

1. Is combat waiting for my decision or resolving an action?
2. What is the enemy visibly doing, and when will the committed action land?
3. Which map tiles are dangerous, and can my proposed movement escape in time?
4. Who am I targeting, and which body region am I aiming at?
5. What happened in the last exchange, and what consequences remain?

The interface should teach these answers through layout and plain language, not
require memorizing abbreviations, debug numbers or a long controls document.

### Non-negotiable constraints

- Movement and attacks execute without a confirmation dialog on every action.
- Shift toggles sprint; its active state must always be visible.
- Dash supports diagonal directions; it does not vault over NPCs.
- Enemy action captions remain visible above visible enemies.
- Preserve frozen attack footprints and the existing counter windows.
- Do not expose uncommitted future AI decisions as guaranteed predictions.
- Rendering, hovering, inspecting and configuring a draft must not advance the
  simulation, consume RNG or alter a replay.
- Do not introduce HP bars or disguise a numeric HP bar as a condition bar.
- Preserve unrelated ship, crew, trading, society and simulation development.
- Do not modify the vendored renderer merely to add combat presentation.

## 2. Current-state assessment

The current implementation provides immediate actions, sprint toggle, diagonal
dash, frozen threat tiles, action captions, body-part labels, a next-event list,
brief outcome labels and a shared enemy customization profile.

These are foundations, not a finished UX:

| Problem | Impact | Required direction |
|---|---|---|
| Compressed body abbreviations in one line | Not recognizable as a human body | Dedicated spatial body diagram |
| Ticks and raw speed/tactics identifiers | Players must translate implementation details | Plain-language labels and simulated seconds |
| Long overhead captions | Obscure combatants and collide in groups | Short persistent captions plus selected-target detail |
| Persistent captions and outcomes share space | Difficult to separate intent from consequence | Separate visual lanes and lifetimes |
| Outcome age follows combat ticks | Feedback may freeze while awaiting input or vanish across a long action | Wall-clock presentation animation independent of simulation |
| Large flat customization menu | Hard to discover and compare settings | Categories, presets and effective-value previews |
| Repeated 'archetype default' values | Actual opponent setup is unclear | Display resolved value and source |
| Setup edits affect shared settings immediately | Cancel semantics and restart behavior are unclear | Draft, review, apply/restart or discard |
| Some injury concepts are coarsely represented | UI can imply more anatomical detail than exists | Honest capability labels and explicit modeling boundaries |

### Important model limitations to avoid hiding

The current core still has an internal blood/survival compatibility pool. Removing
its readout is not the same as removing the HP-like model. Likewise, the current
hand target resolves through an arm region; it is not an independent hand model.
The redesign must not claim medically realistic dismemberment or detailed hand
anatomy until the simulation actually supports it.

## 3. Information architecture and layout

### Main combat screen

Use the existing screen regions deliberately rather than stacking every fact in
the map overlay:

- **Top-left compact status:** decision/resolution state, sprint state, stamina,
  urgent player conditions and current weapon.
- **Central map:** combatants, frozen danger footprint, selected-target marker,
  hover path/landing marker and short enemy action captions.
- **Right inspector:** selected enemy, current action, equipment, readable speed,
  technique explanation and a recognizable body diagram with current aim.
- **Bottom action strip:** contextual controls, proposed action timing/risk and
  the latest concise exchange.
- **Expandable combat history:** complete exchange details without flooding the
  map or permanently enlarging the HUD.

Conceptual layout, not exact pixel coordinates:

```text
+------------------------------------------------+---------------------------+
| YOUR TURN - time waits for your action          | TARGET: Patient Duelist   |
| Sprint OFF | Stamina 82/100 | Cutlass            | Preparing sweep           |
|                                                | Lands in 1.2 simulated s  |
|                COMBAT MAP                      | Threatens your tile       |
|                                                |                           |
|          [Preparing sweep - 1.2s]               |       O   Head: intact    |
|                 enemy                          |      /|\  Arms: see below |
|             ! ! ! ! !                          |       |   Torso: intact   |
|                 @                              |      / \  Legs: intact    |
|                                                |                           |
|                                                | Aim: left arm [selected]  |
|                                                | Fast movement             |
|                                                | Flanker: seeks your side  |
+------------------------------------------------+---------------------------+
| Dash: 3 tiles | 0.8s movement + recovery | Leaves marked threat before hit |
| Shift: sprint | D: dash | Space: attack | G: brace | I: help | Esc: leave   |
+----------------------------------------------------------------------------+
```

### Responsive layout

1. Define panel measurements from font metrics, not hardcoded character counts.
2. Keep the map center and both active combatants unobstructed where possible.
3. At small sizes, collapse secondary details before hiding critical action/aim
   information. Make the inspector scrollable or expandable.
4. Do not silently clip enemy names, warnings or apply/discard controls.
5. Support window resizing, UI font scaling and map zoom independently.
6. Body art must use a verified monospace font even if other UI text does not.

## 4. Time, turn state and threat readability

### Wording

Replace raw normal-play tick text with simulated duration:

- `YOUR TURN — time waits for your action`.
- `Resolving dash` or `Recovering — ready in 0.6s`.
- `Preparing sweep — lands in 1.2s`.
- `Reloading — ready in 1.5s`.

Use the combat tick conversion, not the strategic voyage clock. Label help text
clearly: seconds describe simulated action time, not a real-time reaction timer.
Exact ticks remain available in an advanced/debug detail view.

### Distinguish known facts from anticipation

- **Committed:** exact attack, frozen threatened tiles, remaining simulated time.
- **Observed:** walking, aiming, guarding, reloading, recovering or retreating.
- **Inferred:** brief tendency such as 'often seeks a flank'; never 'will attack
  next' unless the action is already committed.
- **Unknown:** no hidden AI utility scores or future intent in the normal HUD.

### Map threat grammar

Use shape as well as color:

| Marker | Meaning |
|---|---|
| `!` with patterned fill | Enemy's locked attack footprint |
| Distinct outline/marker | Selected target |
| Dotted path | Proposed movement, not a committed action |
| Landing outline | Proposed final movement tile |
| Safe/danger symbol plus text | Timing result at the proposed landing |
| Aim line | Locked firearm aim, visually distinct from a resolved hit |

Warnings must compare actual arrival and attack-resolution events. Merely saying
'enemy acts first' is insufficient if that action does not threaten the path.
When there is uncertainty due to future movement/blockers, state the assumption.

## 5. Body-condition and targeting panel

### Diagram behavior

- Render a stable, recognizable front-facing human outline.
- Give head, torso, left/right arms and left/right legs distinct positions.
- Label left/right as the target's anatomical sides; show this convention in help.
- Highlight the aimed region using brackets/outline and `AIM`, not color alone.
- Represent damaged, disabled and missing regions differently.
- Selecting a region changes aim for the next action only; it never retroactively
  changes an existing immutable attack intent.
- If an attack is committed, show both `Selected aim` and `Committed aim` when
  they differ, or disable free changes until the action allows them.
- Mouse selection and keyboard selection must produce identical target state.

### Condition vocabulary

Prefer `Intact`, `Wounded`, `Disabled`, `Missing` and explicit effects, for example
`Left leg wounded — movement slowed`. Derive each label from canonical body
state and active restrictions. Do not infer a disabled limb solely from an
unrelated generic injury key.

Show urgent bleeding/shock conditions separately from structural limb condition.
Do not present absence of a status as proof of full health if body damage remains.

### Anatomical fidelity gate

Before offering independently selectable left/right hands or feet, define those
regions in the simulation and test injury propagation, weapon restrictions,
bleeding and replay compatibility. Until then, label coarse targeting honestly
and do not display a missing hand as though only the hand was removed when the
core marks the entire arm missing.

## 6. Enemy action captions and exchange feedback

### Persistent intent lane

Every visible enemy receives a short caption near their sprite:

- `Preparing sweep · 1.2s`
- `Aiming pistol`
- `Guarding`
- `Closing distance`
- `Recovering · 0.5s`
- `Retreating`

Use actual current state before personality heuristics. A recovering actor must
not be labeled 'charging' because its previous movement mode was dash. Keep
speed/technique detail in the inspector instead of repeating a long sentence
above every actor. Selected and immediately threatening enemies get priority.

### Temporary outcome lane

- Use structured core outcome events, not parsing English log lines.
- Each event carries a stable ID, combat tick, source, recipient, event type,
  actual body region, outcome and relevant consequences.
- Renderer records a presentation start time when an unseen event arrives.
- Animate using monotonic wall time; idle redraws must continue while the
  simulation is paused for input, without advancing combat time.
- Suggested default lifetime: 1.5–2.5 real seconds, adjustable; reduced-motion
  mode displays static fading labels or uses the history strip.
- Offset persistent captions and rising outcomes into separate lanes.
- Stack/merge a bounded number per actor; prioritize severing, unconsciousness,
  death and parry over routine repeated minor feedback.
- Anchor outcomes to the event location, not blindly to an actor that has since
  moved. Never show an offscreen/hidden attacker through fog-of-war feedback.

Examples:

| Attacker feedback | Recipient feedback | Persistent consequence |
|---|---|---|
| `Hit landed` | `Left arm wounded` | Arm restriction in inspector |
| `Blocked` | `Shield block` | No invented wound |
| `Parried` | `Parry!` | Attacker staggered/recovering |
| `Miss` | Only show evasion if established by the event | No wound |
| `Clean cut` | `Arm severed` if actually supported | Missing region and equipment impact |

History remains readable after labels expire. Re-rendering a frame must not replay
the same popups; reset/new fight must clear old presentation events.

## 7. Controls and discoverability

### Consistent action rules

- Shift toggles sprint only once per physical key press; ignore OS repeat.
- Display `SPRINT ON/OFF` persistently, including after stamina lockout or forced
  stop. Explain when the core automatically ends sprint.
- D enters explicit dash targeting; show valid eight-way landing paths.
- Esc while dash targeting cancels that targeting mode without leaving combat;
  otherwise retain the documented arena-leave behavior.
- Hover and target selection are free; attacking, moving, waiting and equipment
  actions spend simulation time. Label the distinction in the inspector/help.
- No extra confirmation for normal movement or attacks.
- Expose unsupported inputs with a concise reason, never a silent failure.
- Keep keyboard-only and mouse-only paths usable. Diagonal dash must not require
  a numeric keypad: mouse targeting provides the fallback.
- Correct stale pending-action flags so a completed immediate action cannot cause
  Esc or clicks to behave as though confirmation is pending.

### Contextual help

Show at most a compact primary control row, with `I: all controls`. While dash is
armed, replace generic help with landing/cancel guidance. While a gun is empty,
explain reload rather than recommending an unavailable attack. Disabled actions
remain discoverable and show why they are unavailable.

### Contextual combat action bar (delivered)

The bottom bar changes with the action model instead of leaving duel buttons
disabled during other encounters:

| Encounter context | Quick actions |
|---|---|
| Ready one-on-one melee duel | Light, Heavy, Parry, Dash, contextual Dash-strike |
| Group/legacy melee | Attack, Heavy, Guard, Dash, More |
| Ranged weapon equipped | Aim, Fire, Reload, Guard, More |

The bar names the active rules context (guided duel vs legacy group/ranged), and
click targets use the same commands as the displayed keys. Disabled attacks
reflect current legality/ammunition; ranged Fire stays unavailable until a line
is aimed, and Reload is unavailable while loaded or without spare shots. Shared
Space/F/G inputs fall back to legacy Attack/Heavy/Guard when guided combat is not
actionable. R/T are explicit Aim/Reload bindings. `C`/More opens the full context
menu; movement, target selection and other legacy options remain available
there. `I` help changes its primary action explanation by mode.

## 8. Enemy customization redesign

### Main flow

`F12 → Enemy setup → Choose preset → Customize categories → Review → Apply & restart`

Use a draft separate from the live arena configuration. Back navigates without
applying; closing a modified draft offers `Discard changes` or `Keep editing`.
Only applying setup resets the fight. This confirmation protects a destructive
restart; it is not per-action confirmation during combat.

### Categories

| Category | Fields and behavior |
|---|---|
| Encounter | Archetype, enemy count, shared profile vs future individual profiles, squad mix, environment |
| Equipment | Primary weapon, secondary weapon or explicitly none, shield, armor, ammo, spare shots, smoke, supported throwables |
| Abilities | Base speed, movement skill, strength, perception, stamina capacity, carry capacity |
| Behavior | Personality preset, supported tactics, short plain-language explanation |
| Starting condition | Current stamina, fatigue, balance, morale, per-region wounds/missing limbs; shock/bleeding if supported by schema |
| Appearance | Display name/glyph where supported; no claim that appearance changes combat power |
| Advanced | Exact numeric values and resolved profile; no scheduler tokens, raw intents or internal actor IDs |

Personality axes can be shown as resolved read-only values initially. Editing
aggression/caution/etc. independently requires actor-local validated overrides;
never mutate shared global personality dictionaries to customize one enemy.

### Field interaction and meaning

- Show effective values, for example `Speed: 100 (from Duelist preset)`.
- Each field has a one-sentence effect description and allowed range.
- Numeric fields support bounded increments and exact numeric entry, rather than
  only a handful of presets. Reject NaN/infinity and invalid types/ranges.
- Distinguish `Inherit default`, `None equipped` and an explicit value.
- Preserve explicit `False` and `0`; never interpret them as 'use default'.
- Changing archetype updates inherited values and retains explicit overrides,
  with an obvious `Reset overrides` action and changed-field indicators.
- Starting stamina must not exceed maximum stamina; show a validation error or
  ask to adjust the dependent value, rather than silently producing an invalid actor.
- Magazine capacity must follow weapon mechanics. Do not offer impossible loaded
  round counts when firearm behavior only supports a single loaded shot.
- Wounds must produce the actual supported restrictions, not cosmetic labels.
- Warning examples: missing arm with two-handed weapon; empty gun with no spare
  shots; severe leg injury with high load. Permit valid deliberate challenge
  setups but explain their consequences.
- Shared profile scope must be explicit: `Applies to all 3 enemies`. Individual
  enemy editing is a later extension, not implied by the current shared editor.

### Presets and review

Begin with existing archetypes as authored presets. Offer a small beginner set
such as basic swordsman, aggressive brawler and armored defender; each summary
must describe actual configured equipment/behavior.

Review shows effective weapons, defensive equipment, behavior, movement and
starting limitations, plus a diff against the currently applied setup. Add
optional named local presets only with versioned validated storage separate from
the captain save; imported files must never execute code.

## 9. Architecture and implementation boundaries

### Existing integration points

- `pirates/combat_core.py`: canonical actors, immutable intents, scheduler,
  wounds, action outcomes and AI state. Owns facts, not screen coordinates.
- `pirates/combat.py`: arena lifecycle, spawn configuration, target selection,
  previews and UI-facing combat data.
- `play.py`: input adapter, F12 setup and native SDL rendering integration.
- `content/combat_arena.json`: authored archetypes, weapons, patterns and tuning.
- `tests/test_combat.py`: mechanics, control and projection regressions.
- `tools/preview_combat.py`: save-free native screenshots and rendering purity.
- `COMBAT-SYSTEM-GUIDE.md`, `START-HERE.txt`: architecture and player controls.

### Proposed separations

1. **Validated enemy profile schema:** allowed fields/ranges, inheritance,
   resolved defaults, cross-field checks and canonical serialization.
2. **Pure combat presentation model:** phase, threats, action captions, body
   conditions, aim, readable labels and disabled-action explanations.
3. **UI presentation state:** popup wall-clock ages, layout/cache, focus/scroll
   and draft edits. Excluded from deterministic combat snapshots/replay hashes.
4. **Structured outcome stream:** core simulation events with stable identities;
   bounded retention/cursor handling so slow consumers do not duplicate events.

New helper modules may be introduced when they reduce the size of `play.py`;
names should follow surrounding project conventions. Do not split modules just
to move tightly coupled code without improving testability.

## 10. Implementation phases and acceptance gates

Delivery summary (Nov 2026): the required player-facing vocabulary, action/body
HUD, contextual guided/group/ranged quick-action bar, sprint state, committed
intent captions, structured outcome events, immediate controls, and categorized
shared-profile draft/review/apply flow are implemented.
Exact numeric profile entry and common presets are supported. Full HP-model
replacement, independent hands/feet, persistent/individual enemy profiles,
reduced-motion setting, expandable event history, and all-window-size/accessibility
matrix coverage remain follow-ups. See sections 2, 10 and 12 for those boundaries.

### Phase 0 — Baseline and truthful capability audit

- [x] Record current native screenshots and focused tests.
- [x] Inventory real NPC-configurable fields, ranges and behavior consumers.
- [x] Document coarse anatomy, blood-model compatibility and unsupported settings.
- [x] Identify stale control help, pending-action flags and profile inconsistencies.

Gate: every planned label/setting has an identified source of truth; no new
combat balance changes are bundled into the UI baseline.

### Phase 1 — Presentation model and readable vocabulary

- [x] Implement read-only, testable presentation projection and simulated-second formatting.
- [x] Prioritize current state over stale behavior/movement-mode labels.
- [x] Separate committed facts and observed actions from future AI decisions.
- [x] Add readable speed/technique labels with exact details in inspection.

Gate: identical snapshots yield identical view data; hovering/inspection consumes
no ticks or RNG; future AI choices are not leaked as committed actions.

### Phase 2 — Main HUD, body diagram and target interaction

- [x] Show selected-target details and a spatial body outline in the compact HUD.
- [x] Add enemy-relative anatomical-side labels, aim selection and persistent consequences.
- [x] Show critical player state/sprint indicator in a stable compact region.
- [x] Unify threat/path/landing grammar and bounded contextual help.

Gate: player can identify target, aim, threat and current action without opening
a menu; no actor or important warning is covered by the default panel layout.

### Phase 3 — Outcome events and feedback animation

- [x] Emit structured events for hit/block/parry/miss/cover/sever/down outcomes.
- [x] Animate through renderer-local monotonic time and idle redraws.
- [x] Add bounded stacking, deduplication, event-position anchoring and visibility-safe presentation.
- [ ] Add a persistent expandable event history and reduced-motion setting.

Gate: feedback animates and expires while combat waits, without simulation
mutation; repeated renders never spawn duplicate labels; major outcomes remain
readable in a multi-enemy exchange.

### Phase 4 — Enemy setup usability and validation

- [x] Create grouped draft editor with effective values and descriptions.
- [x] Add exact bounded numeric editing, explicit inheritance and validation.
- [x] Show presets, compatibility warnings and effective-value review.
- [x] Apply/restart after review; cancel/discard/reset leave the running setup unchanged.
- [x] Keep overrides runtime-local, deterministic and independent from campaign player equipment.

Gate: keyboard and mouse can configure, review, apply and reset an enemy; invalid
combinations cannot produce a half-applied fight or overwrite a captain save.

### Phase 5 — Onboarding, polish and release verification

- [ ] Add optional short first-arena tips; dismiss without advancing time.
- [x] Audit combat help/menu/status strings against actual bindings.
- [ ] Complete the small-window, zoom, font-scale, monochrome and reduced-motion matrix.
- [x] Update architecture guide and player instructions.
- [x] Run focused/related tests and inspect native screenshots manually; full-suite run pending.

Gate: all required checks below pass or an explicit unrelated blocker is recorded.
Do not report overall completion based only on focused tests.

## 11. Verification plan

### Automated contracts

- Pure rendering/projection leaves actor state, queue, RNG, replay and metrics
  byte-for-byte equivalent where serialized equivalence is applicable.
- Seconds formatting is correct at 0, subsecond, exact-second and large durations.
- Sprint key repeat cannot toggle repeatedly from one held Shift press.
- Immediate actions leave no stale confirmation state.
- Aim changes cannot mutate a frozen attack intent.
- Body conditions reflect actual side-specific damage/missing state.
- Diagonal hover preview and committed movement agree on path/timing/collision.
- Feedback renders once per event, expires by wall time and survives multiple
  events resolving in one simulation advance.
- Hidden enemies do not leak through captions, inspector or outcome positions.
- Profiles preserve explicit zero/false/none, resolve defaults consistently, and
  reject invalid values before any arena/world replacement.
- Profile cancel/reset/apply and same-seed reset behave deterministically.
- Starting wounds enforce the relevant core restrictions.
- Ordinary captain save/map/deck/time remain safe through arena start/exit.

### Native screenshot matrix

Capture: ready/sprint on; melee windup; firearm aim/reload; selected wounded body;
missing limb; safe/unsafe diagonal dash preview; parry/block/hit/whiff feedback;
multiple nearby enemies; monochrome; reduced motion; small window; enlarged font;
setup categories; invalid setting warning; review/apply screen; ended fight.

Verify foreground labels, event text, threat tiles and body outline are not
clipped or obscured. Include at least one frame sequence during idle waiting to
prove popup motion/expiry is not tied to combat tick advancement.

### Commands for implementation verification

Run from `E:/Pirates of Miska`:

```powershell
python -m pytest -q tests/test_combat.py
python -m pytest -q tests/test_combat.py tests/test_ship_spaces.py tests/test_ship_life.py
python -m compileall -q play.py pirates tests tools
python tools/preview_combat.py --output 'C:/Users/Warp/AppData/Local/Temp/opencode/pirates-combat-ui-ux'
python tools/combat_headless.py --fairness-audit
git diff --check
python -m pytest -q
```

Extend the preview tool for the new matrix instead of treating its existing five
frames as proof of every new UI state. Record full-suite timeouts/failures honestly;
do not change unrelated systems merely to obtain a green result.

### Player comprehension checks

Ask a fresh tester to perform these without coaching:

1. Identify the current enemy action and dangerous tiles within five seconds.
2. Select a body region and correctly state where the next attack is aimed.
3. Toggle sprint and identify whether it is active.
4. Preview and execute a diagonal dash without confirming every step.
5. Explain whether the last attack hit, missed, blocked or was parried.
6. Configure an armored swordsman, review its effective values and start the fight.
7. Cancel a customization draft without losing the running encounter.

Targets: at least 4/5 fresh testers complete core combat-reading tasks on the
first attempt; all can complete setup after reading one-line field descriptions.
These are proposed acceptance targets, not claimed measured results.

## 12. Out of scope and follow-up decisions

- Full replacement of the blood/HP-compatible injury model is separate mechanics
  work; the UI must expose its limitations honestly until that work is approved.
- Anatomically independent hands/feet and realistic severing thresholds require
  a dedicated injury-model design and tests, not just new labels.
- Do not retune AI, widen frozen telegraphs or remove counter windows here.
- No tactical-to-strategic NPC identity or crew wound persistence migration.
- Individual opponent profiles and import/export are follow-ups after the shared
  editor is reliable; do not imply they already exist.
- Display speed bands should derive from effective movement, with documented
  thresholds and load/injury modifiers, not invent additional combat statistics.

## 13. Definition of done

The redesign is complete only when the player can reliably read intent, danger,
aim, condition and outcomes; controls are consistent and discoverable; setup is
validated and reversible; native visual checks cover supported display modes;
determinism/save safety remain intact; documentation matches shipped behavior;
and outstanding mechanics limitations are explicit rather than cosmetically hidden.
