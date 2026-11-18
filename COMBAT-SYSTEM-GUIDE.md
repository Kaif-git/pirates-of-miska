# Pirates of Miska Combat System: Code, Simulation, and HUD

## Current sword duel: COMBAT SPEC reform

`COMBAT SPEC.md` is authoritative for the default **C/F12** sword-duel route.
The independent engine lives in `pirates/duel_system/`, with all numerical rules
in `content/duel_system.json`. `DuelSession` controls confirmed commands, NPC
decisions, learning, automatic busy-time advancement and saved sessions. The UI
in `pirates/duel_ui.py` reads only the engine's immutable `RenderModel`.

- **Space / F:** choose Light / Heavy, then **H/T/A/X/L** selects head/torso/arm/hand/leg.
- **Arrows:** preview an ordinary Walk. **D** then arrows/numpad: Dash; **J**: Dash + Light.
  In direction selection **1/2/3** selects distance. Walk remains available after attacks.
- **Enter / Confirm:** commit the preview. Repeating the same movement selection also confirms.
- **G:** preview Guard; while held, G releases (with lower recovery), Enter holds one turn.
- **C / More:** Run, bandage, food, water, weapon pickup and information screens.
- **K / L / H:** notebook / full log and last-five why exchanges / help.
- **I:** guide OFF → HINTS → FULL. **Tab:** timeline. **N:** step one tick; **A:** automatic time.
- **Esc:** cancel a preview/menu, or pause the duel from the main screen. C aboard resumes it.
  Walk to a `>` exit to actually flee. **Backspace:** fresh retry; **V:** next duel with wounds,
  blood, stamina, fatigue and remaining consumables carried over (only if conscious).
- Hover the enemy/header or long-press the enemy for a redacted sheet; exact stats unlock
  after three observed exchanges. `~` is a learned prediction, never a committed threat.

The screen persistently distinguishes alive/decision, resolving, guard held, unconscious,
dead and victory. Blood is life; there is no second HP pool. Red defeat text and disabled
combat actions remain visible with retry instructions. NPC choices use the pre-player-choice
snapshot. Previews/menus do not spend ticks or record learning. Saved `duel_journal` contains
the resumable engine snapshot and per-NPC/archetype notebook without changing the ship map/time.

Native verification: `python tools/preview_duel_system.py --output C:/Users/Warp/AppData/Local/Temp/opencode/pirates-spec-duel`.
The walkthrough covers 40-column desktop/360px portrait views, zones, previews, commitments,
guard hold/release, notebook/log/help, guide modes, death/unconscious/victory, native KeyDown
dispatch and pause/resume. M10 XP/balance work is still pending; do not infer balanced combat
from UI or unit-test gates. `COMBAT-REFORM-CHANGELOG.txt` records specification inconsistencies.

## Legacy boarding / historical arena implementation

The remainder describes the retained older boarding/group engine, not the C/F12 sword UI.
Its implementation is in `pirates/combat_core.py`, `pirates/combat.py`,
`content/combat_arena.json` and the legacy presentation adapter in `play.py`.

## 1. Architecture at a glance

Combat is a deterministic, integer-tick simulation that temporarily takes over the normal world map. The core is headless: it knows about actors, walkable coordinates, doors, hazards, and scheduled events, but not SDL, menus, or the ship simulation.

```text
Player input / boarding encounter
             |
             v
       play.py (key dispatch, renderer, menus)
             |
             v
       CombatArena (pirates/combat.py)
   projects World <-> temporary Combat state
             |
             v
       Combat (pirates/combat_core.py)
   event queue, actions, telegraphs, resolution, AI
             |
             v
  content/combat_arena.json (patterns, weapons,
       personalities, archetypes)
```

### Responsibilities

- **`pirates/combat_core.py` — rules and deterministic simulation.** Defines `Actor`, immutable `Intent`, `Combat`, tick scheduling, movement, line of sight, action validation, damage/injuries, AI, snapshots, reports, and replay information. It can be tested without opening a window.
- **`pirates/combat.py` — world/game adapter.** `CombatArena` creates a practice arena or projects a built ship deck for boarding; builds the captain actor from world state; executes player operations immediately; exposes compact HUD rows, threat overlays, and save-safe enter/exit behavior.
- **`content/combat_arena.json` — data catalog.** Supplies named patterns, weapon-to-pattern mappings, reload behavior, personalities, and enemy archetypes. Combat calculations and validation consume these records rather than scattering individual weapon rules through the UI.
- **`play.py` — controls and presentation.** Dispatches combat keys/mouse input, executes actions immediately, draws environment/threat/landing previews into the map buffer, renders the combat strip and rising action feedback, and chooses combat sidebar/status prompts.
- **`tests/test_combat.py` and `tools/combat_headless.py` — validation.** The tests exercise specific rules and save/adapter contracts; the headless tool supports deterministic replay/fairness and AI matchup checks. `tools/preview_combat.py` is the native SDL visual/purity walkthrough.

## 2. Starting and leaving combat

### Practice arena

`CombatArena.start(world, seed=1)` (`pirates/combat.py`) saves the current map object, active deck, and captain `(x, y, z)`. It then builds a temporary arena map around world coordinates x=73..88, y=20..33, places optional pillars, and installs a new core `Combat` instance with the selected doors and hazards. The captain starts near the middle; enemies spawn according to the configured archetype, count, and uniform/mixed squad mode. Target selection initially chooses the first enemy. World visibility is refreshed, but normal world time is not advanced by arena actions.

The available setup scenarios are:

- **Skirmish:** one configured opponent by default.
- **Outnumbered:** three opponents by default.
- **Legend:** one sniper archetype by default.

The F12 setup menu configures archetype, number, cover, tactical doors/hazards, and uniform versus mixed composition. Its **Customize enemy stats / equipment / condition** editor groups weapon/tactics, movement/fitness, protection/condition, and supplies/display. Three presets seed the editable draft. Each value shows its resolved source; **Use inherited value** removes the override. Numeric fields support exact bounded text entry as well as common values. Review validates stamina dependencies and warns about combinations such as an unusable two-handed weapon, empty firearm, or severe leg condition with an over-capacity load; it then offers apply/restart or discard. Reset-to-inherited is also a draft change. The shared runtime profile applies to every enemy spawned on start/reset; it does not edit persistent campaign NPCs or save a profile to the campaign.

### Boarding arena

`CombatArena.start_boarding(world, entry, count, seed)` does not replace the ship with the rectangular practice layout. It projects the captain's active built deck into the tactical map using ship floor, wall, door, and current door-open state. The entry point must be a walkable tile on the current deck. Raiders are placed on available deck positions, and the captain is moved to a valid deck tile if their current position is not in the projection.

On exit, the original map/deck/captain position are restored. A successful defense can pass surviving surrendering boarders into the ship capture flow. The boarding adapter also resolves the voyage's encounter continuation/retreat behavior. Practice arena actors and state are temporary; boarding outcomes are handled by the adapter at the end.

### Exit, reset, and world isolation

`CombatArena.exit()` restores the exact saved world-map reference, active deck, and captain position, then refreshes visibility. The core arena is not serialized as a mid-fight save. `reset()` preserves the core seed, exits, and starts a fresh arena. The normal game clock remains frozen during combat; the core combat tick is independent.

## 3. Core time model and event scheduler

Combat time is integer ticks. The global combat clock starts at tick 0 for a new arena. A nominal player decision is 100 ticks; action costs are converted to time using speed. Movement uses per-tile costs, so arrival events can occur before a multi-tile command completes:

```text
next ready tick = current tick + ceil(action cost * 100 / actor speed)
```

Movement speed is further derived from base speed, movement skill, fatigue, carried load/capacity, and active leg-cripple status (`movement_speed()` in `combat_core.py`). The result is bounded to 25–200. This rate changes action and step timing; telegraph windup is not accelerated by actor speed. Run and dash spend stamina and add fatigue; low stamina slows movement and can lock out heavy attacks.

The priority queue stores `(tick, event kind, actor id, token)` entries. Resolve events are ordered before ready events at the same tick, with stable actor IDs as a deterministic tiebreak. Actor tokens invalidate stale queued events after interruption, rescheduling, or death. `run_until_player()` processes scheduled events until the captain is alive, ready, and has no due same-tick event left to resolve. This tie-draining rule prevents an NPC action at the same tick from resolving after the player has already received input.

`Combat.command()` validates an operation, records successful player actions into replay data, and normally advances the scheduler until the next player decision. Combat actions execute immediately; hover/inspection previews are separate and read-only. Enter waits. Debug stepping can instead process bounded events/ticks (`step_events`, `advance_debug_ticks`). Rendering is expected to be observational only: drawing a frame must not mutate the actor state, event queue, or replay.

## 4. Actions, commitments, and telegraphs

An attack is not an immediate hit roll. The actor commits to a pattern and a fixed set of target tiles. `Intent` is frozen and records the actor, pattern, target tiles, start tick, resolution tick, interruptibility, and targeted body part.

1. Action validation checks readiness, status restrictions, range, weapon compatibility, ammo, doors, and target geometry.
2. The selected action creates an intent and marks its target tiles for the HUD.
3. The scheduler continues to advance while the attack winds up.
4. The attack resolves against actors occupying those committed tiles at that time, subject to line of sight, parry, shields, and other resolution rules.
5. The attacker remains in recovery until its scheduled next-ready time.

Moving after an attack is committed does not make the attack track the target. Leaving its marked tiles can cause a whiff. Cover/LOS is checked again at resolution, so a projectile lane can be blocked after commitment. A shield can stop a frontal hit; a shove can break the shield stance and push the victim. A correctly faced brace can parry a parryable attack and interrupt/stagger its attacker.

### Telegraph fairness

`telegraph_audit()` in `combat_core.py` verifies that every damaging pattern declares an achievable counter and that zero-damage patterns are explicit feints. NPC damaging windups leave a response interval; a committed intent's footprint is frozen and never tracks the captain. Optional two-phase tracking was evaluated and omitted: the fixed red threat footprint plus visible movement/dash landing previews communicates the locked attack without changing that model.

Threatened tiles use a shape-distinct `!` marker (and `x` for a player-committed hit); aiming lanes use a dot. The event timeline lists the next scheduled action/readiness and calls out a threat to the captain. Enemy captions show short observed or committed actions, with simulated-seconds countdowns only for current commitments/recovery. Raw ticks remain in advanced/debug data.

## 5. Movement and tactical geometry

All standard movement is cardinal, and movement resolves one tile at a time through scheduled events. The path is truncated at the first blocked, closed, or occupied tile; every step rechecks occupancy and doors and applies hazards on entry.

- **Walk:** one tile. Normal walk slightly restores stamina and reduces fatigue. A crippled leg increases the step cost.
- **Sprint:** up to two cardinal tiles, spends stamina and adds fatigue; Shift toggles sprint mode, and grave/Caps are alternate toggles.
- **Dash:** up to three tiles in any of eight directions, costs stamina and adds fatigue. D arms directional dash, Ctrl+arrow commits directly, numpad 1/3/7/9 selects diagonal dash, and Shift-click can select a landing distance.
- **Safe sidestep (B):** selects a free adjacent cardinal tile not currently covered by an enemy committed intent; if there is no safe tile, it spends no time.
- **Wait/rest (`.`):** costs 50, restores stamina, reduces fatigue, and changes movement mode to rest.

For multi-tile run/dash, the actor is not teleported to the endpoint. Each tile arrival gets its own scheduler event and revalidates the path. This allows an interruption, hazard, or new blocker to stop a stride before completion.

`facing()` is eight-way for attacks and parry direction. Distance used by many tactical systems is Manhattan distance. Pattern geometry can be line, arc, cone, ray, or point; LOS uses a ray through walkable cells, doors, and active smoke. Closed doors and smoke therefore affect visibility/shot lanes as well as movement.

## 6. Damage, body parts, and statuses

Actors have a legacy-compatible internal blood/survival pool plus stamina, fatigue, base speed, movement skill, carry load, armor, weapon, facing, selected target part, injuries, statuses, ammunition, and tactical state. The HUD does not display an HP number or bar, but the underlying pool remains part of the model; hiding its readout is not an HP-system replacement. Combat feedback emphasizes wounds, shock, bleeding and body condition. The captain's arena actor uses a projection of persistent inventory weight and the average configured combat skill. Combat injuries/statuses are temporary to the arena; they do not become persistent ship wounds.

An intent captures the attacker's selected body part at commitment, so changing target part later cannot rewrite an attack already underway. The core's resolution derives raw damage from pattern damage plus strength, with a head bonus and a cap for arm/leg attacks; armor reduces damage by tier. A successful part hit records an injury tier (wound/maim/critical, as surfaced by the HUD). Head targeting is the high-damage/vital option; arm and leg targeting trades damage for functional consequences. The menu's hand target currently resolves to the left-arm region; it is not an independent hand anatomy model.

- Arm hits can apply `crippled_arm`, restricting two-handed attacks, reload, aim, and fire.
- Leg hits can apply `crippled_leg`, lowering movement speed and increasing the cost of a walk/escape step.
- Bleeding deals damage over time at tick boundaries until it expires or is bandaged; the source is recorded for metrics.
- Stagger/paralysis/stun can interrupt active intent and impose a readiness delay.
- Smoke has a finite expiry and obscures LOS. Spikes and fire damage an actor entering their tile; spikes can also cripple a leg.
- A shield blocks frontal attacks while intact; shove breaks the stance for a bounded period.

The exact behavior and values for each pattern live in `content/combat_arena.json`; shared resolution/status logic lives in `Combat.resolve()`, `apply_damage()`, `interrupt()`, and `advance_time()`.

## 7. Weapons and action mapping

The adapter translates player intent into core action IDs. It does not implement attack outcomes itself.

| Player action | Core behavior |
|---|---|
| Space | First/light pattern for the current weapon; for reloadable guns, fires the already locked aim line. |
| F | Last/heavy pattern; for reloadable guns, also fires the locked line. |
| C / right-click | Open the context menu for aim, reload, weapon switch, door, smoke, bottle, target part, bandage, and safe sidestep. |
| Context > weapon | Switch among cutlass, fists, and pistol; switching is a timed action. |
| Z / 1–5 | Cycle target part or directly select head, torso, arms, legs, or cycle. This is target selection, not a hit roll. |
| G | Brace facing the selected foe; a frontal parryable attack can be interrupted. |
| Q | Shove. Also useful against shield stance. |
| E | Bandage and remove bleeding; costs 100. |
| Context > aim/reload/door/smoke/bottle | Perform the selected timed action immediately; unavailable actions spend no time. |
| X / click foe | Inspect/select an enemy; a second click immediately attacks. Information remains free. |
| Tab | Cycle the selected target. |

The available weapon/pattern records currently include cutlass thrust/feint/sweep, fists jab/slam, spear thrust, pistol/rifle shots, shotgun cone, charge, shove, and thrown bottle. Each pattern has its own shape, reach, windup, cost, recovery, damage, status, and parryability. The sidebar shows the current weapon's relevant first/last patterns and action blockers; a blocked action does not advance time.

## 8. NPC AI and tactical state

`Combat.ai()` is a utility scorer with stable lexical/action-coordinate tie-breaking; no random draws choose between near-equal candidates. It gathers visible opponents within perception/LOS, retains last-seen data for a bounded interval, calculates candidate actions, records their scores on the actor for debugging, and uses the same action legality rules as the player. Recent player retreating/bracing behavior affects pursuit cutoffs and feint selection. Chase run/dash decisions account for time, stamina, fatigue, terrain, and safe-disengage timing.

Scored choices can include attacks, feints, aim/fire, reload, weapon switching, brace, movement, run/dash, door interaction, bandage, and wait. Scores consider personality axes (aggression, patience, caution, recklessness, cunning, discipline, nerve), enemy recovery/readiness, whether a target can evade, incoming marked threats, shield/brace state, relative numbers, low HP/suppression, distance, fatigue/stamina, injury, terrain hazards, weapon range, and whether a flank/usable attack lane can be reached.

Tactical policies include:

- **Patient/duelist:** more willing to hold range and punish recovery; less reckless.
- **Berserker/brawler:** favors aggressive closing and commits more readily.
- **Coward:** high caution/low nerve; can retreat under danger and flee after enough retreat steps.
- **Sniper:** patient ranged behavior; seeks/uses aim lanes and may reposition away from close pressure.
- **Pack hunter/flanker:** values flanking and coordinated positioning around a target.
- **Shield bearer:** blocks frontal attacks, making shove or flank useful counterplay.

AI can flee at a boundary when badly injured and cautious, or withdraw after repeated retreat steps. Exact ties use the stable sorted action and destination ordering, independent of seed.

## 9. What the player sees: screen regions

The combat screen uses four information layers: tactical map, compact combat status/timeline, sidebar context prompt, and bottom status strip. The status HUD states whether time is waiting for the player or resolving, shows persistent sprint ON/OFF, stamina/balance/shock, selected aim and condition, current kit, selected foe action/speed band/technique, an enemy-relative spatial body diagram, and the next committed events. The diagram marks the selected aim with brackets and labels enemy left/right from the enemy's perspective.

### A. Combat status strip (map viewport)

`CombatArena.combat_overlay_lines()` builds the status/timeline/log strip. `PiratesRenderer._frame_cell_extras()` captures those rows, while `_draw_combat_text_overlays()` renders them as a cached font texture near the bottom of the map viewport. Threats and route previews are painted in the world buffer.

It contains decision/resolution state; sprint, stamina, balance, shock, aim/body state and current weapon/ammunition; selected foe action, readable speed band/technique and a spatial body diagram; and upcoming events. Enemy overhead captions show only their short current action, not repeated stat strings. Recent hits, blocks, whiffs and outcomes rise in a separate, wall-clock-timed lane above the involved combatants.

### B. Center playfield / threat tiles

The combat map remains the center playfield. Tactical doors, active hazards, and smoke receive their own cell overlays. The renderer then paints exact committed threat tiles and aim lanes over the environment:

- A damaging intent marks the exact committed tiles with `!`; the captain's own committed hit uses `x`.
- If no damaging intent covers a tile but a fighter has an active aim line through it, the lane is marked with a dot.
- The display honors the arena's intent-overlay toggle and monochrome mode. In monochrome, glyph shape still distinguishes threat, aim, recovery, and normal states.
- Threat geometry comes from the immutable intent's `target_tiles`, not from a fresh re-aim each frame. Do not infer safety from the actor's current facing alone.

The fixed threat footprint and aim lane are the authoritative indicators; they do not change targeting or rewrite the core intent.

### C. Enemy state glyphs

Enemies use shape-distinct map glyphs rather than overhead text labels.

State glyph precedence is:

1. **Committed intent:** `!` over exact frozen threat tiles.
2. **Aim active:** `?` enemy glyph and dot-marked aim lane.
3. **Recovery/stagger:** `o`; otherwise the normal actor glyph.

Markers are only drawn for visible actors/tiles and do not reveal hidden enemies.

### D. Right side / combat sidebar

In combat, `PiratesRenderer._sidebar_lines()` replaces the usual ship/crew sidebar with a compact target/context prompt. Combat context is opened with C or right-click.

Hover previews show path, per-step/total time, stamina, and whether an enemy acts first; previews are informational and actions execute immediately without an extra confirm. The context menu exposes less-frequent actions: aim, reload, weapon switch, door, smoke, bottle, target part, bandage, and safe sidestep. Escape cancels an armed dash; otherwise it exits combat.

### E. Bottom status strip

`PiratesRenderer._status_lines()` provides the latest combat message and a concise movement/attack/help prompt along the bottom of the window. While dash targeting is armed, this row changes to eight-way landing/click guidance and explains that Esc cancels the targeting mode.

### Renderer ordering and text cache

The inherited renderer (`engine/omnisim/ui/ascii_renderer.py`) builds the world cell buffer and calls the app adapter's `_frame_cell_extras()`. The combat adapter paints environment/threat cells there and stores the current text records. During `_present_layers()`, the inherited renderer copies the world texture, invokes `_draw_world_floats()`, and then draws dialogue/UI layers. `PiratesRenderer` extends `_draw_world_floats()` to add the combat text overlays after the world texture is copied.

PIL renders the compact HUD into an RGBA image; SDL uploads it with alpha blending. The world map, threat markers and route previews use the existing cell renderer. Keep this adaptation in `play.py`; the vendored engine snapshot has provenance/hash checks and should not be edited for game-specific HUD behavior.

## 10. Controls and dispatch

`play.py` binds the combat key map at the application boundary through `_pirates_world_action_for_key()` and handles the resulting actions in the main loop. `COMBAT_INPUT_MODE` is updated before rendering/reading the next action so the temporary arena uses glyph fighters rather than normal ship entity sprites.

When exactly one melee opponent is in guided-duel range and the exchange is actionable, the live adapter overrides legacy actions with five actual kernel actions:

| Guided input | Effect |
|---|---|
| Space | Light attack. |
| F | Heavy attack. |
| G | Parry; outcome depends on timing, facing, force and capacity. |
| D, then direction | Ordinary directional Dash. |
| J | Contextual Dash-strike; high stamina/fatigue cost and exposed recovery on a miss. |

The action bar and mouse hit targets match these commands. Enemy overhead text comes from its committed action and ETA or an honest waiting/recovery state. The player cue checks parry-window and Dash first-tile timing, but explicitly does not guarantee force or success. Feedback carries control/evasion and force/capacity causes, first-contact order, counter-window/speed eligibility, resulting stamina/fatigue and attack-control penalties. The guided kernel, observation-limited NPC policy, legacy combat resolution, legacy AI tie-breaking, and arena spawn placement now make no RNG calls. Group/ranged encounters retain their legacy rules and injury model, but now get an action bar that describes those actions instead of showing disabled duel-only buttons.

Outside actionable guided contact, the quick bar is selected from the captain's current weapon:

| Context | Quick action bar |
|---|---|
| Group/melee | Attack (Space), Heavy (F), Guard (G), Dash (D+direction), More (C). |
| Ranged | Aim (R), Fire (Space/F), Reload (T), Guard (G), More (C). |

Disabled buttons reflect readiness, target legality and ammunition. Fire is disabled until a line has been aimed and ammunition is loaded; Reload is disabled when already loaded or when no spare shot remains. `Space/F/G` fall back to the matching legacy Attack/Heavy/Guard actions whenever a guided duel is not actionable. `More` opens the existing full context menu, and `I` help describes the active mode. Group/ranged movement, target selection, body targeting, doors and inventory actions remain available through their existing keys/context menu.

| Input | Effect |
|---|---|
| C | Enter the practice arena when not already in combat. |
| Esc / F11 | Cancel an armed dash with Esc; otherwise exit the arena and restore the world. |
| Arrows | Walk one tile, or sprint if sprint mode is active. |
| Shift | Toggle sprint mode; the combat HUD shows ON/OFF (` / Caps are alternate toggles). |
| D, then arrow | Arm and commit a dash; Ctrl+arrow is the one-chord form. |
| Numpad 1/3/7/9 | Dash diagonally. |
| Shift+click or D+click | Dash to a selected landing tile (up to 3 in any direction). |
| B | Safe sidestep. |
| R / T | Aim / reload when using a firearm. |
| C / right-click | Open context actions for aim, reload, weapon, door, smoke, bottle, body part, bandage, and sidestep. |
| Z, 1–6 / E | Cycle or directly select body target (6 requests the coarse hand/left-arm target) / bandage. |
| Click foe twice | Inspect/select, then attack. |
| Click tile | Walk/run to adjacent cardinal tiles; D/Shift-click dash also supports diagonal. |
| X / Tab | Inspect / cycle target. |
| I | Open combat controls/help; informational action. |
| Enter / . | Wait and recover. |
| F12 / F10 | Arena configuration / debug menu. |
| N / Shift+N | In debug-step mode, advance one / up to 100 ticks. |
| Backspace | Reset the arena with the same core seed/configuration. |

Enter / . wait and recover, advancing combat time. Menu/help/target operations and previews are free and read-only. Rejected core actions do not consume a turn. `CombatArena.act()` updates the world player's projected coordinates and refreshes visibility after core actions. Pressing 6 requests the hand target, which currently maps to the left arm; the diagram and help should not be read as independent hand anatomy.

## 11. Metrics, replay, and debugging

The core tracks aggregate and per-actor actions, hits, misses/whiffs, parries, punishes during recovery, damage dealt/taken, kills, and related events. Each combat-feedback record has a stable event ID, tick, presentation anchor, source/recipient IDs, event type, outcome, optional body part and consequence details. The renderer timestamps unseen IDs with monotonic wall time; idle redraws animate/fade them without advancing ticks or replay. Recent events are bounded in the core snapshot. `snapshot()` captures deterministic state; `report()` summarizes outcome/timing/metrics. The audit list records resolved intents, including their fixed targets, for retrospective fairness checks.

`CombatArena.dump(path)` writes core ruleset version 3, state, report, initial actor state, walkable map, doors, hazards, guided-state schema version, guided actor IDs, and replay rows. Version 3 changes legacy hit-part drift/decisive resolution and stable AI tie behavior; older replay dumps are rejected rather than replayed under changed rules. `duel_state_from_data()` validates nested guided state records; `restore_guided_duel_snapshot()` restores the duel slice only onto a matching already-active arena after version, actor, occupancy, clock and replay-tail checks. It is not a general campaign save loader. Guided actions store their deterministic post-action state with each replay entry; the ordinary legacy replay command remains version-gated. `--ai-vs-ai --output PATH` saves matchup summaries including resolved/timeouts and mean landed hits. The debug sidebar can expose actor utility scores and exact intent details. `show_intents` controls tile highlights; `monochrome` switches their presentation; `debug_step` enables event-by-event stepping. Debug tools may heal/kill/spawn or provide a bottle and are not normal combat rules.

Useful commands from the repository root (`E:\Pirates of Miska`):

```powershell
python -m pytest tests/test_combat_beliefs.py tests/test_combat_npc.py tests/test_combat_duel_rules.py tests/test_combat.py -q
python tools/preview_combat.py --output 'C:/Users/Warp/AppData/Local/Temp/opencode/pirates-combat-guided'
python -m pytest -q
python tools/combat_headless.py --fairness-audit
python tools/combat_headless.py --ai-vs-ai --matches 90 --seed 2026
python tools/combat_headless.py --bot all --all-archetypes --matches 10 --seed 2026 --output 'C:/Users/Warp/AppData/Local/Temp/opencode/combat-ai-bots.json'
python tools/combat_headless.py --prove-counter --archetype duelist --seed 17
python tools/preview_combat.py --output 'C:/Users/Warp/AppData/Local/Temp/opencode/pirates-combat'
```

Latest ruleset-3 verification: the focused four-file suite passed 202 tests; native SDL preview passed 21 captures and restored the original map without touching saves; compileall, scoped diff-check, and `python tools/combat_headless.py --fairness-audit` passed. Fresh repository-wide `python -m pytest -q`: **837 passed, 11 xfailed**. Source scans found no RNG calls in the combat runtime modules; simulation-level RNG in village/weather and `sim/` systems is outside combat.

The native preview verifies that rendering does not alter the combat snapshot, captures ready/threat/monochrome/dash-preview/whiff and stacked wall-clock outcome frames, previews the categorized profile editor without applying the draft, confirms the clock is frozen, and checks that the original map and captain position are restored with no save touched. Visual review should check threat footprints, landing ghosts, body diagram/aim, stacked labels, profile review readability, bottom-strip clipping, and shape-distinct markers in color and monochrome.

## 12. Important implementation constraints

- Keep combat simulation independent of renderer state. HUD generation must be read-only and derived from core actor/intent fields.
- Preserve immutable, locked-target semantics of `Intent`; map markers must never silently retarget it.
- Keep event ordering deterministic (resolve before ready at equal ticks; stable actor IDs and tokens).
- A damaged actor, a dead actor, and a fled actor are distinct outcomes. `finished` treats dead/fled enemies as resolved; boarding adapter decides whether that means victory, retreat, or capture.
- The practice arena is not integrated into naval cannon combat. Boarding is a separate adapter entry point on a projected built deck.
- Legacy combat progression fields are kept for save compatibility. The current arena projects skill/load into temporary movement state; combat does not grant persistent weapon levels or permanent wounds in the described combat loop.
- Do not edit copied `engine/` files for game-specific presentation without intentionally addressing the source-hash/provenance contract.

## 13. Primary code map

| File / symbol | Purpose |
|---|---|
| `pirates/combat_core.py:18` | Load catalog constants from `content/combat_arena.json`. |
| `pirates/combat_core.py:19` | Current replay ruleset version (2). |
| `pirates/combat_core.py:33` | Static damaging-telegraph fairness audit. |
| `pirates/combat_core.py:76` | Movement speed from skill, fatigue, load, and leg wounds. |
| `pirates/combat_core.py:192` | Immutable committed `Intent` shape. |
| `pirates/combat_core.py:302` | Headless deterministic `Combat` simulation. |
| `pirates/combat_core.py:348` | Read-only action/path preview. |
| `pirates/combat_core.py:599` | NPC telegraph timing. |
| `pirates/combat_core.py:1172` | Player command validation/replay recording. |
| `pirates/combat_core.py:1334` | Utility-scored enemy AI. |
| `pirates/combat_core.py:1598` | Snapshot/report/replay metadata. |
| `pirates/combat.py:17` | `CombatArena` adapter imports and ruleset. |
| `pirates/combat.py:347` | Seed-preserving arena reset; action requests execute immediately. |
| `pirates/combat.py:379` | Adapter preview. |
| `pirates/combat.py:477` | Four-layer HUD strip rows. |
| `pirates/combat.py:532` | Ordered next-event timeline. |
| `pirates/combat.py:567` | Versioned replay dump. |
| `play.py:31` | App-boundary combat key map. |
| `play.py:197` | Compact combat text overlay rendering. |
| `play.py:495` | Combat threat, dash, and hover path previews. |
| `content/combat_arena.json:2` | Patterns, weapons, personalities, archetypes and their numeric tuning. |
| `tests/test_combat.py:1` | Combat simulation, adapter, and UI-state regressions. |
| `tools/preview_combat.py:13` | Native SDL preview and render-purity checks. |
