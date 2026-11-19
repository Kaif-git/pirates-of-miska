# Combat Expansion Plan

Date: 2026-11-18
Status: proposed implementation plan; no expansion mechanics implemented by this document.

## 1. Objective

Build on the current readable, one-action-per-fighter duel foundation to create combat about control, commitment, injury, positioning, personality, and morale—not only repeated Guard → Light exchanges.

The player should understand why an exchange happened, anticipate opponents through evidence, exploit advantages, and recognize when withdrawal is wiser than another trade.

Long-term scenario: a fast, skilled fighter can defeat a much larger group by choosing engagements, disabling opponents, moving through openings, and breaking morale. This is an aspirational scenario, not a guarantee that Speed makes twenty coordinated enemies harmless.

## 2. Scope and non-negotiable constraints

- Preserve one voluntarily committed action per fighter per exchange unless the user explicitly approves a different scheduling model.
- Internal timing remains meaningful. Do not restore extra attacks merely because a fighter has higher Speed.
- Reflexes and forced displacement are consequences of committed actions, not free extra decisions or attacks.
- Preserve deterministic, seedable simulation, cloneability, save/load continuation, and observation-only UI/coach boundaries.
- Centralize tunable rules in validated configuration; do not scatter balancing constants through UI or AI code.
- Preserve normal renderer integration, smooth movement/camera, fixed dashboard anchors, and concise default recaps.
- Do not treat a screenshot or passing unit test as sufficient evidence of good combat feel.
- New mechanics must apply consistently to player and NPC bodies. AI decision preferences may differ; physical rules should not secretly favor either side.
- No guaranteed counter to every matchup. Every intended counter needs explicit execution requirements and failure modes.
- Existing COMBAT SPEC.md remains the baseline. Record approved expansion deviations explicitly rather than silently claiming strict compatibility.
- This task writes the plan only. Implementation, balancing, and completion claims belong to later work.

## 3. Current foundation and known gaps

### Existing mechanics to extend, not duplicate

- Internal phase timing and effective Speed; rounded durations can produce equal arrival ticks despite unequal stats.
- Stamina, fatigue, pain, blood, anger, nervousness, morale-derived fear, wounds, guard/parry, riposte, and some localized injury effects.
- Fear-break/yield behavior exists; emotions are not wholly absent. Their behavioral consequences and presentation need expansion.
- One independent commitment per fighter, automatic resolution, recorded events, and read-only playback.
- Coach [I], Strategy explanations, current/fresh stat comparisons, target uncertainty, and comparative trade recaps.

### Known issues and limitations

- User reports Run/Dash sometimes spend an exchange without visibly moving. Cause is not established; movement must be reproduced and fixed first.
- Guard/parry/light dominates perceived close-quarters choices.
- Speed advantages can disappear at timing-rounding boundaries and are less expressive in an exchange-based interface.
- Injury and emotion do not yet produce enough visible changes in intent, posture, and positioning.
- Current duel architecture is two-fighter-specific. Multi-opponent combat is not a small AI toggle.
- No universal winnability, stat-gap cutoff, or one-versus-twenty win rate has been proven.

## 4. Design pillars

1. **Readable causality:** every important outcome has an understandable cause and practical consequence.
2. **Control before attrition:** wounds, stability, position, and composure create openings.
3. **Commitment has risk:** powerful actions expose recovery, footing, or defense.
4. **People behave differently:** personality creates tendencies; condition changes them.
5. **Evidence supports prediction:** players infer likely choices without seeing hidden queued actions.
6. **Lethality is earned:** anatomy, weapon, angle, protection, and severity determine disabling outcomes.
7. **Withdrawal is gameplay:** retreat, surrender, and escape remain legitimate outcomes.

## 5. P0 — Movement reliability and diagnostic feedback

### Investigation

- Reproduce Run, Dash, Walk, and Dash+Light through the native UI, not only direct engine calls.
- Trace chosen direction/distance, legality, committed path, scheduled movement deadlines, displacement events, interruptions, and playback positions.
- Check all eight directions, distances 1–3, adjacent enemies, walls, occupied destinations, arena edges, wounded legs, exhaustion, and pause/resume.
- Distinguish simulation failure from successful movement hidden by camera/playback.

### Required behavior

- Preview intended path and destination before movement commits, without revealing the enemy's future choice.
- Explain rejection before commitment where already knowable.
- If a valid path becomes blocked during resolution, show where and why.
- Never silently consume an exchange with no movement explanation.
- Preserve the difference between blocked movement, attack interruption, and successful movement followed by enemy pursuit.
- Specify partial-path behavior and resource accounting explicitly; test it rather than assuming a refund policy.

### Acceptance

- Native tests exercise real buttons/keys and verify resulting world positions.
- A blocked result produces a readable event/recap, not an empty result.
- Movement remains one commitment, cannot cross occupied tiles illegally, and survives save/load consistently.
- No expansion phase relies on movement until this gate passes.

## 6. P1 — Speed, initiative, and useful positioning

### Intended role

Speed improves when a committed action reaches its important moments: entry, strike, departure, and recovery. It does not grant multiple attacks in one exchange.

### Work

- Audit current timing curves and rounding breakpoints across representative gear and condition ranges.
- Measure fresh and degraded windup, movement arrival, guard readiness, recovery, and riposte timings.
- Evaluate finer internal timing resolution before adding new Speed bonuses; do not assume both are needed.
- Make footwork and recovery advantages visible and tactically useful.
- Specify how earlier recovery interacts with the exchange boundary. Do not claim an initiative benefit that the scheduler immediately discards.
- Explicitly define same-time strikes, moving out of reach before impact, and already-committed actions after injury.
- Preserve a deliberate distinction between a first hit and a disabling first hit.

### Candidate rule direction

- A superficial hit normally does not cancel a committed attack.
- A sufficiently disruptive injury, stagger, or fall may interrupt it under explicit rules.
- Faster movement can avoid an impact only if the fighter actually leaves its valid reach in time.
- Speed alone does not provide invulnerability frames.

### Acceptance

- Controlled timing tests demonstrate meaningful advantages at several Speed gaps without extra voluntary actions.
- Same-time trade behavior is stable and documented.
- UI explains actual timing differences, including ties and threshold crossings.
- Compare alternatives through scenario tests before selecting final formulas or caps.

## 7. P2 — NPC individuality, emotions, and readable prediction

This phase is a core mechanic, not cosmetic flavor. Build the behavioral foundation before expanding the full action roster; connect it to injury reactions in P3.

### Stable personality dimensions

- Aggression: willingness to initiate and pursue.
- Discipline: ability to preserve a plan under pressure.
- Confidence: willingness to accept uncertain engagements.
- Patience: willingness to wait for a favorable opening.
- Adaptability: willingness to abandon repeatedly punished tactics.
- Courage: resistance to intimidation and morale collapse.

Use bounded dimensions and authored archetype presets rather than a proliferation of unrelated special-case flags. Exact ranges and weights require later tuning.

### Dynamic state

- Fear, anger, pain, morale, and nervousness extend existing state rather than competing with duplicate systems.
- Composure represents current decision control; define its relationship to discipline and fear explicitly.
- Overconfidence is a biased assessment of advantage, not necessarily another independent resource bar.
- Adrenaline is deferred to P5; do not silently approximate it as free stamina.

### Decision pipeline

1. Form a public-information observation of position, condition, and recorded opponent behavior.
2. Generate legal candidate actions using existing mechanics.
3. Score tactical suitability: reach, exposure, resources, injury, and escape options.
4. Modify preferences through personality and dynamic emotion.
5. Apply bounded seeded variation among plausible choices.
6. Commit once; resolve without reading subsequent player input.
7. Update personal experience from observed outcomes.

Maintain private debug traces for score provenance, but never expose them as certain enemy intent through the normal UI or coach.

### Behavioral effects

| State/tendency | Observable possibilities | Important exception |
| --- | --- | --- |
| Anger | Pursuit, forceful retaliation, less patient defense | Disciplined fighters can restrain it |
| Fear | Hesitation, withdrawal, defensive approach, surrender | Cornered fighters may rush desperately |
| Pain | Protecting a limb, avoiding trades, interrupted confidence | Training can preserve decision control |
| Overconfidence | Careless entry, ambitious attacks, excessive pursuit | Punishment can correct the assessment |
| Patience | Holding distance, inviting commitment | Cannot become endless passive deadlock |
| Discipline | Measured approach and fewer panic commitments | Does not negate physical wounds |

### Opening variety

- Impulsive aggressor: more likely to rush into range.
- Cautious veteran: measured approach, guarding when threatened.
- Counterfighter: holds useful distance and invites an attack.
- Nervous recruit: hesitates or approaches indirectly.
- Overconfident bully: advances too far or opens ambitiously.

These are distributions, not guaranteed scripts. Opening behavior must obey the actual action system; do not simulate simultaneous walking and guarding unless a combined mechanic is explicitly introduced.

### Adaptation and memory

- Remember a bounded number of observed commitments and consequences.
- Penalize repeatedly punished habits gradually, based on actual experience.
- Do not counter a player action before observing it.
- Keep some habits persistent so every NPC does not converge on the same optimal policy.
- Distinguish own experience from later group-level witnessed morale information.

### Presentation

- Player emotions may have exact values and practical consequence explanations.
- Enemy emotions use visible estimates and uncertainty unless an explicit reveal rule permits exact information.
- Behavioral descriptions must be grounded in events: “pursued after two missed swings,” not “will heavy next.”
- Separate commitment cues shown after both choices lock from predictive cues shown before choices.
- Coach output: observed evidence → likely tendency/confidence → conditional response → alternative risk.
- Calm appearance is evidence of controlled behavior, not proof of low pain or absence of injury.

### Acceptance

- Same geometry and resources produce meaningfully different action distributions across personalities over multiple seeds.
- Paired tests show emotion changes behavior while holding personality and tactical circumstances fixed.
- Different personalities respond differently to comparable fear/pain without ignoring legality.
- Hidden queued actions, RNG state, and private AI weights cannot change public advice for identical public evidence.
- Adaptation occurs only after qualifying observations.
- Tests include aggressive rush, cautious approach, panic, composure recovery, and anti-deadlock behavior.

## 8. P3 — Localized injury, shock, and reflex response

### Injury representation

Separate wound depth, affected structure, ongoing physiological consequences, and immediate response.

- Depth: superficial, muscle-level, penetrating; exact labels and thresholds to be finalized.
- Structures: skin, muscle, tendon, bone, major vessel, organ, eye, and limb integrity where relevant.
- Consequences: bleeding, pain, impaired function, stability loss, incapacitation, and death.
- Protection: armor coverage and penetration influence plausible structures injured.
- Weapon/contact: cutting, thrusting, and blunt effects must not share identical outcomes blindly.

Do not add an independent lethal lottery after every hit. Severe outcomes should be tied to contact, anatomy, penetration, and bounded seeded uncertainty with recorded provenance.

### Examples, not guaranteed outcomes

| Contact | Possible result |
| --- | --- |
| Superficial arm cut | Bleeding and pain with limited immediate disability |
| Deep forearm injury | Impaired grip/parry; possible dropped weapon |
| Tendon injury | Severe loss of specific limb function |
| Thigh injury | Movement/stability loss and potentially serious bleeding |
| Head impact | Daze, impaired vision, concussion, unconsciousness, or severe structural injury |
| Penetrating torso injury | Serious bleeding or organ damage; possible collapse |

Head targeting is dangerous but not automatically decapitation. Deep torso injury is not automatically organ destruction.

### Immediate shock response

- Evaluate wound disruption separately from ongoing pain.
- Possible responses: flinch, recoil, freeze, protect wound, panic retreat, rage response, or maintained discipline.
- A severe landed wound may undermine a weak opponent's plan immediately.
- Veteran composure can preserve behavior but cannot make a severed tendon function normally.
- Define whether response interrupts, delays, or leaves the current commitment intact.
- Forced recoil may move 1–2 tiles, including diagonals when valid; direction must follow a clear rule.
- Respect walls, actors, footing, and available escape space. Being cornered changes the response rather than permitting teleportation.
- Prevent infinite recoil/stun chains through explicit bounded reaction rules, not hidden immunity.

### Acceptance

- Tests separate graze, disabling injury, and lethal injury.
- Identical injuries can produce different mental reactions but consistent structural impairment.
- Recoil does not overlap actors, cross walls, grant free attacks, or create unbounded reaction loops.
- Already-committed action behavior is tested before, during, and after its active phase.
- Every major wound produces a concrete readable consequence and an event trail.

## 9. P4 — Balance, stability, falls, and ground survival

### Model

- Balance: relatively stable capability, modified by condition and equipment.
- Stability: current physical control, affected by movement and contact.
- States: stable → off-balance → stumbling → grounded; exact transitions to be tuned.
- Keep physical stability distinct from mental composure.

### Causes

- Overcommitted missed attacks, sharp turns during fast movement, leg injuries, shoves, collisions, weapon binds, and explicitly modeled terrain hazards.
- Ordinary safe movement should not cause unexplained random falls.
- Heavy gear can influence stability and recovery through documented tradeoffs rather than universal penalties.

### Grounded play

- Falling exposes defense, movement, and weapon control; it is dangerous but not automatic death.
- Limited commitments: cover, roll/crawl away, or attempt to rise.
- Standing requires time and space; hostile pressure can punish it.
- Define occupied destination, edge, collision, and interrupted-rise cases.
- Grounded status must have unmistakable rendering and coach explanation.

### Acceptance

- Falls have inspectable causes and bounded transitions.
- Ground recovery works through real UI input and has fair, explicit exposure.
- No instant infinite stand/fall cycle or zero-cost escape.
- Injuries and fatigue affect stability without double-applying the same penalty accidentally.

## 10. P5 — Tactical actions and adrenaline

Introduce actions incrementally, with counterplay and AI support for each.

| Action | Purpose | Cost/risk to define |
| --- | --- | --- |
| Feint | Bait premature defense or commitment | Time/stamina; opponent may not react |
| Shove | Attack stability and create space | Reach, commitment, strength/balance contest |
| Disengaging step | Trade offense for separation | Pursuit risk, space requirement |
| Committed thrust | Threaten a narrow penetrating opening | Aim, recovery, exposure, weapon suitability |
| Weapon bind | Contest control at close range | Both fighters constrained; escape rules |
| Disarm | Exploit compromised grip/control | Setup required; not a universal cheap attack |

### Guard counterplay

- Guard remains useful against its existing covered threats.
- Feints, bypass targets, instability, distance, and later flank pressure create different problems.
- Do not make every new action a hard counter that always beats Guard.
- Do not introduce an omnipotent “guard while moving/attacking” choice that erases commitment costs.

### Adrenaline

- Proposed short-lived state triggered by meaningful danger/exertion, with explicit activation and recovery rules.
- May temporarily suppress felt pain and support exertion; exact mechanical benefits require measurement.
- Does not heal injuries, replace lost blood, repair structures, or grant unlimited stamina.
- Distinguish pain suppression from improved composure: a panicked fighter can still be ineffective.
- Define post-surge debt/crash and prevent repeated toggling or trigger farming.

### Acceptance

- Every action has legal-state, resource, timing, interaction, UI, and AI tests.
- At least one plausible failure response exists for each new tactic.
- Feint behavior remains compatible with locked commitments; explicitly specify baiting/response timing before implementing it.
- No action silently requires reactive mid-exchange input under the current scheduling model.
- Adrenaline benefits and debt are visible and persist correctly through save/load.

## 11. P6 — Severe outcomes and equipment integrity

- Separate dropped weapon, deliberate disarm, broken weapon, and severed hand.
- Tie weapon failure to material, existing condition, contact, and exceptional stress.
- Make shattered weapons rare, explainable outcomes rather than arbitrary punishment.
- Limb severance and decapitation require suitable anatomy/contact/weapon/severity rules.
- Eye injury, concussion, and unconsciousness need distinct consequences and recovery rules.
- Define pickup and replacement behavior, including occupied ground items and injured hands.
- Persist permanent injuries separately from temporary combat statuses.
- Decide how severe injuries transfer into the wider game before enabling them in ordinary encounters.

### Acceptance

- Severe outcomes are reachable in controlled tests but not universal for ordinary aimed hits.
- No contradictory state such as a severed hand retaining normal grip or an unconscious fighter choosing actions.
- Defeat, death, surrender, escape, and incapacitation remain distinct results with clear next steps.
- Save migration and postfight carry preserve each outcome correctly.

## 12. P7 — Multi-opponent combat and group morale

Do not extend the two-fighter engine by repeatedly calling duel resolution for pairs; that would distort action counts, timing, and visibility.

### Architecture

- Replace two-fighter assumptions with explicit actor lookup, target selection, and encounter state.
- Define one commitment per participating actor and common timing resolution.
- Specify exchange boundaries when actors have unequal action durations, enter late, flee, or become incapacitated.
- Define attack reach, body occupancy, obstructed paths, collisions, and simultaneous movement conflicts.
- Avoid stable-ID movement ordering creating systematic tactical advantage; specify deterministic contention handling.
- Introduce facing/flank rules only with clear visual presentation and explicit target arcs.
- Ensure UI and coach operate on relevant nearby threats rather than an unreadable full-group ledger.

### Group behavior

- Coordination varies: recruits crowd and obstruct; trained groups maintain spacing and exploit exposed approaches.
- Enemies do not share perfect knowledge automatically.
- Witnessed injuries, deaths, failed rushes, and leadership loss can influence morale.
- Restrict propagation through visibility/hearing rules and bounded response timing.
- Fear can cause withdrawal, hesitation, fragmentation, or surrender—not necessarily a universal simultaneous rout.
- Prevent attacks through allies unless an explicit weapon/trajectory rule permits them.

### One-versus-many target experience

- Fast entry → meaningful wound → opponent disruption → departure through available space.
- Terrain and choke points let the player limit active threats.
- Removing a leader or visibly disabling an attacker can destabilize weak groups.
- Surrounding disciplined opponents remains extremely dangerous; no artificial queue that forces nineteen enemies to wait their turn.
- Performance and readability must be measured at 2, 5, 10, and 20 opponents.

### Acceptance

- Conservation of action counts and deterministic replay under simultaneous conflicts.
- No offscreen or hidden-state advice leakage.
- No actor overlap, attacks through invalid obstructions, or actions by incapacitated actors.
- Group morale traces to witnessed evidence.
- Controlled scenarios demonstrate both successful isolation and lethal encirclement; neither universal victory nor inevitable defeat is the balancing target.

## 13. Shared UI and responsiveness requirements

For every major mechanic, show **cause → changed capability → useful implication**.

Examples:
- “Deep forearm injury → grip weakened → parries unreliable.”
- “Overcommitted miss → off-balance → vulnerable to shove.”
- “Fear rising after ally fell → giving ground → possible escape opening.”
- “Speed advantage → earlier movement arrival → can leave reach before impact.”

### Layers

1. In-scene cue: recoil, fall, dropped weapon, direction of movement, or clear posture/state indicator.
2. Concise recap: decisive event and consequence, including both impacts when trading.
3. Persistent state: wounds, physical condition, emotion estimates, stability, and control restrictions.
4. Details: exact recorded numbers and causal ledger, only on request.
5. Coach: public evidence, confidence, conditional suggestion, and failure risk.

Keep arena/cards/controls anchored. Avoid overlapping popups, silent status changes, generic damage numbers that conceal anatomy, or long default calculation dumps.

## 14. Data, saves, and technical boundaries

### Existing integration points

- `content/duel_system.json` — centralized tuning and validated rule data.
- `pirates/duel_system/model.py` — combat state, actions, events, injuries, and serialization.
- `pirates/duel_system/engine.py` — legality, timing, movement, and resolution.
- `pirates/duel_system/stats.py` — effective capabilities and timing/cost formulas.
- `pirates/duel_system/wounds.py` — injury consequences.
- `pirates/duel_system/mental.py` — emotion consequences and fear decisions.
- `pirates/duel_system/ai.py` — action selection and observation boundaries.
- `pirates/duel_system/session.py` — independent commitments and exchange lifecycle.
- `pirates/duel_system/render_model.py` — immutable public presentation contract.
- `pirates/duel_system/exchange_log.py` — concise and detailed explanations.
- `pirates/duel_ui.py`, `pirates/duel_layout.py`, `pirates/duel_scene.py` — input, dashboard, and native playback.
- `pirates/duel_coach.py` — inexpensive observation-only guidance.
- `pirates/game.py` — world save/load and postfight carry integration.

Verify current symbols and ownership before editing; this list is an integration map, not instructions to place every new feature in a large existing module.

### Requirements

- Version schema changes and define migration defaults for older saves.
- Record strike-time evidence rather than reconstructing it from later mutated stats.
- Bounded personality memory, reaction chains, and group propagation.
- UI rendering and coach queries must not mutate simulation, RNG, emotion, or learning.
- Introduce focused modules where necessary to avoid circular dependencies and oversized resolution functions.
- Preserve existing save/pause behavior and preference isolation.

## 15. Verification and balance protocol

### For every phase

1. Confirm current behavior with regression or native reproduction.
2. Write explicit mechanics, interactions, and event schema before implementation.
3. Add deterministic unit/scenario tests, then implement the smallest complete vertical slice.
4. Validate public-information isolation and rendering purity.
5. Test save/load and older-save migration.
6. Run the relevant existing duel suites and compile/whitespace checks, including untracked files.
7. Run native playback/input audits separately from CPU-heavy tests.
8. Inspect representative screenshots and play the scenario; collect user feedback.
9. Measure distributional behavior across seeds before making balance claims.
10. Record verified results and unresolved issues; update durable workflow knowledge outside the project.

### Scenario matrix

- Fast/weak versus slow/strong; equal Speed with unequal armor; high fatigue; wounded legs; low blood.
- Aggressive recruit versus cautious veteran; angry disciplined fighter versus angry impulsive fighter.
- First disabling hit, superficial trade, blocked retreat, cornered recoil, interrupted rise.
- Guard-heavy behavior versus feint/shove/positioning alternatives.
- Low-condition weapon, disarmed fighter, permanent injury continuation.
- Later: choke point, open surround, fleeing group, leader loss, mixed discipline.

### Metrics

- Movement completion/block/interruption reasons.
- Action distributions, repeated-action frequency, passive deadlocks, and adaptation latency.
- Timing advantage and actual avoided impacts across Speed ranges.
- Injury severity, interruption, falls, severe outcomes, and recovery frequency.
- Resource expenditure, encounter length, escape/yield rates, and scenario-specific win rates.
- Native input latency, simulation cost, playback frame time, and group scaling.

Do not optimize one overall win rate at the expense of diverse plausible outcomes. Controlled examples explain mechanics; they do not prove universal balance.

## 16. Decisions to settle during implementation

- Finer internal timing versus revised curves: choose after P1 measurements.
- Whether recovery needs a visible next-exchange initiative carry; this requires an explicit scheduling design.
- Whether cautious guarded movement deserves a dedicated action and what it sacrifices.
- Exact anatomy abstraction and severity thresholds; avoid needless medical simulation complexity.
- Composure representation and its relationship to existing morale/fear/nervousness.
- Forced-recoil direction selection, distances, and anti-chain rules.
- Ground combat scope: limited survival actions first, not a full wrestling simulator.
- Feint response timing under independent locked commitments.
- Permanent injury recovery, replacement equipment, and wider-world consequences.
- Multi-actor exchange boundaries and simultaneous path contention.

Do not block P0 on these later decisions. Resolve each before the phase that needs it.

## 17. Delivery checklist

- [x] P0: Run/Dash reliability reproduced, fixed, and explained in native play.
- [x] P1: Speed creates measured timing/position advantages without extra actions.
- [x] P2: NPC personality and emotion visibly influence behavior without revealing future choices.
- [x] P3: Localized injuries create legible physical and psychological consequences.
- [x] P4: Stability, falls, and dangerous but playable ground recovery.
- [x] P5: New tactical choices and bounded adrenaline, with AI and UI support.
- [x] P6: Severe injuries and equipment outcomes integrated with saves/world carry.
- [x] P7: Multi-opponent architecture, coordination, morale, performance, and readability.
- [x] Final: regression, save migration, native walkthroughs, measured scenarios, and player feedback audited.

First implementation slice: **movement reliability**, then a **Speed/timing audit**, followed by **distinct NPC openings and the first injury → shock → repositioning loop**. Do not begin by adding decapitation or twenty actors to the current duel loop.

## 18. P1 decision log (Nov 18, 2026)

- User selected **movement timing tuning** after reviewing the measured ceiling-rounding ties. Keep one committed action per fighter per exchange and preserve same-tick strikes trading before interrupts.
- Keep the current `ceil(base * speed_reference / effective_speed)` algorithm and do not introduce sub-tick initiative in P1. `COMBAT SPEC.md` remains the baseline; the expansion will explicitly document any changed action-table values rather than edit or silently replace that source.
- Candidate action-table tuning: `DASH.per_tile` and `DASH_LIGHT.per_tile` from 2 to 3; `RUN.per_tile` from 4 to 5. Accepted for P1 after the regression and seeded comparison below. At reference effective SPD 19.4 versus 13.58 this yields Dash/Dash+Light steps of 2 vs 3 ticks and Run steps of 3 vs 4; three-tile Dash+Light strike ticks become 9 vs 12 while ordinary Light remains 3 vs 3. This is a measured timing effect, not a universal balance claim.
- Movement still resolves after strikes on the same integer tick. Therefore a move scheduled exactly on an incoming strike tick does not evade that strike; only an earlier completed step changes reach at impact. Explain this rule and test it. Faster recovery does not create an extra action or a faster next exchange while the opponent's committed action is still resolving.
- Verification evidence: candidate movement timings plus fatigue/raw-Speed breakpoints are in `C:/Users/Warp/AppData/Local/Temp/opencode/duel-speed-audit.json`. At player fatigue 35/60/85, the tuned Dash step is 2/3/4 ticks and Run is 4/5/7 ticks. A same-tick strike/Dash regression confirms the hit resolves from the pre-move position and the non-staggered Dash then moves. Direction-panel timing explains per-tile/total travel and strikes-before-movement ordering.
- Seeded before/after comparison: `python tools/audit_duel_movement_tuning.py --fights 100 --seed 2300 --output C:/Users/Warp/AppData/Local/Temp/opencode/duel-movement-balance-100.json`. Across 100 same-seed fights each for ScriptedPunisher, SpamLight, SpamHeavy, Kite and Random versus Ronin, there were no timeouts. First-side win rates were unchanged in all five comparisons (100%, 100%, 0%, 0%, and 2%, respectively); median ticks changed +3, 0, 0, −31, and −2.5. These are narrow existing 1v1 bots/fixtures only; they do not establish broad matchup balance or 1v20 viability. The action-table values are an explicit expansion deviation from `COMBAT SPEC.md` §5, which remains the baseline; the ceil formula and same-tick trade order remain unchanged.
- P1 regression/native evidence: 276 duel-system, coach, recap, UI, round, layout, playback, exchange-log and inspection tests passed; the native dashboard passed 108 captures across 1280×800, 960×700 and 640×600. Save format is unchanged; in-flight scheduled action paths continue to be serialized as before.

## 19. P2 model decisions (Nov 18, 2026)

- Keep the existing one-action-per-fighter exchange contract and legal candidate generator. `base`/`weights` remain private tactical score provenance; add explicit bounded traits to authored profiles instead of special-case archetype code.
- Stable traits `aggression`, `discipline`, `confidence`, `patience`, and `adaptability` use normalized `[0,1]` values. Keep existing `courage` as its authored starting-morale offset for backward continuity; `composure` remains a `[0,1]` baseline. Validation rejects missing, extra, non-finite, or out-of-range trait values.
- Current emotion pools remain the source of truth: anger continues its stat and preference effects; fear remains derived from morale and anger; pain/morale/nervousness are not duplicated. Compute decision control, not a new stored bar, as `clamp(composure * (1 - pressure * (1 - discipline)), 0, 1)`, with `pressure` a configured normalized combination of fear, pain, and nervousness. Discipline damps emotion-driven preference changes, never physical wound/stat consequences. Confidence is risk tolerance/possible overconfidence, not a claim about the true advantage or another resource bar.
- Add authored behavior profiles for impulsive aggressor, cautious veteran, counterfighter, nervous recruit, and overconfident bully alongside the existing brute. `DuelSession.new(archetype=...)` permits explicit deterministic scenarios; default profile selection is a deterministic seed/roster mapping with seed 1 retaining the brute. The game uses its current world tick as the new-fight seed without advancing the world clock; carrying to the next duel advances the deterministic roster. Profile names/weights are never shown as certain tells.
- Keep action selection deterministic and read-only. Among legal candidates within a configured small score margin, use bounded hash-derived seeded variation keyed by combat seed, actor, decision index, and candidate; do not draw/advance combat RNG during previews, guide, rendering, or repeated scoring. Non-plausible low-score actions remain excluded; fixed tie order handles equal perturbations.
- Add a per-fighter bounded experience ring (12 resolved outcomes). Record only public resolved own-action outcomes (`HIT`, `WHIFF`, `PARRIED`, `BLOCKED`, `INTERRUPTED`), never a queued opponent choice. Adaptability applies a configured capped soft score penalty to a repeatedly countered category after at least two qualifying outcomes; successful outcomes decay it. Adaptation cannot hard-ban legal actions and survives save/reload only within the current fight.
- Add a configured anti-deadlock bias after three consecutive decisions without approach or an attack; it only changes scoring among currently legal actions and never grants an action. Distinct openings remain probability/tendency distributions, not fixed scripts.
- Player emotions are shown exactly with derived practical effects. Enemy emotional descriptions are uncertainty-qualified and derived only from public event/wound/behavior evidence; do not display raw enemy fear, morale, pain, personality coefficients, private score traces, queued choices, or future predictions. Coach uses named-NPC observed evidence, reports evidence → qualified tendency → conditional option → risk, and does not fall back to a hidden archetype prior.
- Config/profile and bounded memory additions use combat/session schema v2. Explicitly migrate v1 sessions/config/notebook data: preserve existing profile values and active action phases/path/deadlines, default new traits from the legacy brute and memory to empty, and emit v2 on next save. Verify an old world `duel_journal` resumes and reproduces deterministic continuation.
- P2 acceptance adds seeded paired personality distributions, emotion-held-constant/personality-held-constant comparisons, aggression/caution/panic/composure/anti-deadlock cases, adaptation only after qualifying resolved events, old-save migration, immutable-render/coach purity, and hidden-profile/seed/queued-choice advice-isolation tests. Thresholds/noise/profile tuning remains subject to those measurements; no authored profile is claimed to be balanced from its label alone.

## 20. P2 implementation and verification (Nov 18, 2026)

- `content/duel_system.json` is now schema v2 with six authored profiles and centralized trait, emotion, variation, adaptation, anti-deadlock, and confidence settings. Traits/profile/config values are validated; legacy tuning values are retained by the v1→v2 upgrader. The world starts a new duel from its current tick as the deterministic seed; seed 1 remains Brute. Explicit profile selection remains available in `DuelSession.new` for controlled tests.
- `pirates/duel_system/ai.py` adds derived decision control, bounded emotion/trait score terms, seeded hash variation only among near-top legal choices, resolved-outcome adaptation, and an anti-deadlock score bias. It does not advance combat RNG or grant/ban an action. The 12-entry experience ring records only a fighter's resolved public HIT/WHIFF/PARRY/BLOCK/INTERRUPT outcome; a queued commitment alone is not experience. The balance harness records the same decision/outcome semantics.
- `NotebookView` now exposes only named-NPC observations, never the aggregate archetype history. Guide predictions, ghosts, enemy confidence and coach tendencies require the same named-NPC/public-condition observations; no archetype-prior fallback remains. Player emotion rows are exact; enemy tells are qualified descriptions, and raw enemy morale/pain are not included in the enemy sheet. Strategy Coach displays a conditional option, evidence source, explicit named-observation confidence and risks.
- Migration evidence: regression builds a v1 world `duel_journal` with an active Dash path, reloads it through the world save, migrates config/session/notebook to v2, and verifies identical deterministic continuation. The active path/deadline and legacy tactical values survive; new traits default from Brute and adaptation memory starts empty.
- Paired opening audit: `python tools/audit_duel_personality.py --fights 200 --seed 1 --output C:/Users/Warp/AppData/Local/Temp/opencode/duel-personality-openings-200-final.json`. Under the controlled calm condition, Brute/Aggressor/Bully selected DASH_LIGHT 200/200 each; Veteran WALK_IN 200/200; Counterfighter GUARD 189 and WALK_IN 11; Recruit GUARD 119 and WALK_IN 81. Under the same pressured condition, all six selected DASH_OUT 200/200. These are opening decisions only, not full-fight win rates or broad balance evidence. A 40-seed paired regression verifies repeatability, legal actions, profile separation and pressure behavior.
- Verification: 292 scoped duel-system/coach/recap/UI/round/layout/playback/exchange-log/inspection tests passed. `python -m compileall -q pirates/duel_system pirates/duel_coach.py pirates/duel_layout.py pirates/duel_ui.py` and scoped `git diff --check` passed. `python tools/preview_duel_dashboard.py --output C:/Users/Warp/AppData/Local/Temp/opencode/combat-expansion-p2-final` passed 108 native captures at 1280×800, 960×700 and 640×600; inspected 640px ready/strategy and 1280px strategy frames show the emotion readout, uncertain tell, and explicit evidence/confidence/risk sections. This scripted native verification is not unaided playtesting.

## 21. P3 design decisions (Nov 18, 2026)

- Keep the existing five aimed zones as the coarse anatomy; each persisted wound additionally records `depth`, `structure`, and `damage_type`. Map visible severity to superficial / muscle / deep / structural depth, with a zone- and contact-specific structure label (e.g. arm tendon, leg tendon/bone, head concussion, torso wall). Do not pretend a coarse torso/head aim identifies a specific organ or eye.
- Preserve existing after-armor net thresholds and zone status effects as the mechanical backbone: armor is subtracted before severity/depth, and the current anatomy/function table determines bleeding, grip, movement, aim, stagger and incapacitation. Add weapon contact types `CUTTING` (default for old/current weapons), `PIERCING`, and `BLUNT`; central config gives contact-specific bleed and shock multipliers. Contact type changes structure/bleed/shock, not the accuracy result. Only a clean heavy cutting hand hit may sever. Do not add an independent critical-hit/death roll or new organ-destruction rule in P3; death remains blood depletion and existing head/leg incapacitation thresholds. Detailed armor penetration and permanent organ/eye/limb outcomes remain later P6 work.
- Compute immediate shock separately from persistent pain: configured severity base × zone multiplier × contact multiplier, scaled by `(1 + susceptibility × (1 - decision_control))` using P2's derived control after the physical wound is applied. This lets identical anatomy impair function identically while composure/emotion can change the reflex response. No extra stored shock bar.
- Response bands are `HOLD`, `FLINCH`, and `RECOIL`, with central numeric thresholds. A recoil is at most one tile, directly away from the attacker using the sign of the position delta (diagonal when both axes differ); it is a forced consequence, never an attack or action. Resolve it only after every strike due that tick, preserving same-tick trades. Check arena floor and occupancy; if blocked/cornered, emit `BRACED` with the blocker and do not teleport or overlap. At most one successful recoil per fighter per configured 10-tick window; further shock still applies normal wound consequences and is reported as refractory/brace, preventing recoil chains without hidden immunity.
- Existing wound interrupts remain. A shock recoil cancels a still-unresolved movement/stance/utility commitment after same-tick strikes have resolved; it does not cancel recovery after the strike has already landed. Recoil never refunds stamina. A flinch has no movement or attack; status/function penalties remain regardless of composure. Store the last successful recoil tick for deterministic save/load.
- Record wound depth/structure/contact and post-armor power/armor/net, shock score/control/response, recoil destination or blocking cause in causal events. UI must explain injury → concrete function change → reflex result in the scene, exchange details, wound/state panel, target-effect details and coach; enemy emotional certainty stays within P2's public-information rules.
- Bump combat config schema to v3. Migrate v1/v2 config while preserving authored tuning; old Weapon and Wound rows default to `CUTTING` and derived zone/severity anatomy, old recoil history defaults empty, and any active action/path/deadline remains byte-for-byte semantically intact. Verify old world `duel_journal` continuation.
- P3 gates: parameterized graze / disabling / existing incapacitation examples; armor and all three contact types; same injury with different composure but identical persistent impairment; hits before/during/after strike and recovery; same-tick trades; recoil in open/corner/wall/occupied tile; 10-tick anti-chain; save/replay determinism; readable native feedback and coach purity.

## 22. P3 implementation and verification (Nov 18, 2026)

- `content/duel_system.json` is schema v3. It centralizes cutting/piercing/blunt bleed and shock multipliers, severity/zone shock values, susceptibility, response thresholds and the 10-tick recoil cooldown. Config validation rejects incomplete contact tables or invalid thresholds. Existing action timing, severity thresholds and zone-function consequences remain the baseline; contact type does not affect aim/accuracy, and only an undrifted heavy cutting hand hit can sever.
- `pirates/duel_system/model.py` persists contact type on weapons/wounds, coarse wound depth/structure, and the last successful recoil tick. Old weapon/wound rows default deterministically; combat config/session/notebook snapshots migrate v1/v2 to v3. Active action phase/path/deadline remains intact. `tests/test_duel_system_injury_response.py` covers v2 config/session restoration and deterministic replay; `tests/test_duel_system_personality.py` covers v1 migration through a saved world's `duel_journal`, preserving active Dash path/deadline and identical continuation.
- `pirates/duel_system/wounds.py` derives anatomy/function from post-armor severity and coarse zone/contact, then computes shock using P2 decision control. Physical status, bleed and pain remain identical across personality profiles; only `HOLD`/`FLINCH`/`RECOIL` varies. `pirates/duel_system/engine.py` resolves all same-tick strikes before reactions. Recoil uses the strike-time positions, moves at most one tile after collision/floor checks, braces at obstructions, cannot repeat inside the configured window, never refunds stamina, and cancels only a still-unresolved commitment—not an action whose strike already landed. No extra attack/action slot is granted.
- Causal HIT/WOUND/INJURY_RESPONSE/MOVE/INTERRUPT events record post-armor power/armor/net, contact, structure/depth, function, reflex result and recoil/blocker. `render_model.py`, `duel_layout.py`, `exchange_log.py` and `duel_coach.py` expose anatomical injury, concrete function consequence and reflex/tactical implication without showing internal shock score or decision-control values. Coach advice remains conditional and observation-bound.
- Focused P3 cases cover graze after armor, all contact types and their different tissue/bleed outputs, equal wound function with veteran/recruit reflex difference, existing head incapacitation and blood-loss death, open/wall/occupied recoil, unresolved movement interruption/no refund, no cancellation after a landed strike, same-tick trades, cooldown with subsequent wound retained, and save/replay determinism. The complete scoped duel-system/coach/recap/UI/round/layout/playback/exchange-log/inspection suite passed **311 tests**. `python -m compileall -q pirates/duel_system pirates/duel_coach.py pirates/duel_layout.py pirates/duel_ui.py tools/preview_duel_dashboard.py` passed; explicit trailing-whitespace audit across P3-touched files passed.
- Native `python tools/preview_duel_dashboard.py --output C:/Users/Warp/AppData/Local/Temp/opencode/combat-expansion-p3-injury-final` passed **108 captures** at 1280×800, 960×700 and 640×600. Inspected 1280px impact/details show a deep torso-wall wound, winded function consequence, and flinch without an extra action; post-wound Strategy Coach shows localized injury/bleeding and conditional advice. The compact ready frame, real-modal input/commit behavior, and movement playback were inspected; modal verification recorded 135 movement-playback frames and retained the one-action-per-exchange/save-resume checks. This is scripted native verification, not unaided player testing.
- P3 is functionally verified, not broadly balance-calibrated: current shock thresholds/contact multipliers are authored defaults with focused boundary/counterexample tests. No universal win-rate, broad matchup-balance, or 1v20 claim follows from this phase. P6 still owns detailed armor penetration and permanent structural outcomes.

## 23. P4 design decisions (Nov 18, 2026)

- Keep stable capability (`balance_rating`) separate from current control (`stability`). Derive balance deterministically from configured strength/speed/endurance weights, gear load, current fatigue/pain, and one per-active-leg-wound penalty. Use raw values in this formula, not `effective_stats` plus duplicate fatigue/pain terms. Clamp rating and stability to `[0,100]`; persist current stability/grounded state, but derive rating and presentation bands. Initial bands: `STABLE` 70–100, `OFF_BALANCE` 35–69, `STUMBLING` 1–34; zero latches `GROUNDED` until a successful rise. These are tuning defaults, not diagnoses or hidden random checks.
- V1 P4 adds only causes expressible by the current two-fighter engine: an out-of-reach `HEAVY` whiff loses balance as an overextension; a successful `DASH`/`RUN` tile incurs a configured loss when its direction turns at least 90° from the previous fast-movement direction within the configured short window; existing leg wounds/fatigue/gear modify balance rating, not direct random fall odds. Ordinary `WALK`/straight fast movement never slips randomly. `STAGGER` remains temporary action denial, not a synonym for imbalance. Do not fabricate shoves, weapon binds, body collisions, or terrain hazards before their actions/typed terrain exist; P5 sources get explicit hooks then.
- Each stability loss is a causal event tagged with one source and applied once. A transition to zero emits `FALL`, latches grounded, and cancels only unresolved commitments after all due same-tick strikes; a strike that already resolved is not undone. Compute loss as configured source base × a bounded modifier from derived balance rating, then clamp once. Stability recovers only through configured recovery during committed `WAIT` while standing; do not passively regenerate during attacks, guard, or while grounded. A blocked/out-of-bounds crawl yields a readable blocked event with no displacement.
- Grounded fighters remain participants and have three choices: `COVER` (the existing committed `GUARD` behavior; no movement/attack), one-tile `CRAWL` (slow, collision-checked, no attack), or `RISE` (a committed, timed, exposed action). Successful rise clears grounded only on completion and restores a partial configured stability value in the `STUMBLING` band; any qualifying hit/interruption leaves the fighter grounded and does not refund the action. The AI must choose only among those legal grounded options. No action is granted by falling or rising.
- Add explicit `stability`, grounded latch/timestamp, and last successful fast-movement direction/tick fields; schema v4 migrates v1/v2/v3 state/config/session/notebook while preserving active action schedules and defaults old actors to upright/stable. Centralize balance coefficients, thresholds, source losses, wait recovery, crawl/rise timing/cost, and rise stability in `content/duel_system.json`.
- P4 gates: no fall from ordinary walk/straight run; deterministic overextension and sharp-turn causes; rating changes once for fatigue, load and leg wounds; boundary behavior and repeated losses; grounded attack illegality; cover/crawl/rise costs and timing; occupied/edge crawl rejection; rise interruption under pressure; no passive grounded recovery or instant stand/fall loop; AI/UI/coach event explanation; v1–v3 world-journal migration and replay. Measure distributions before tuning; do not conflate these prototype rules with realistic biomechanics.

## 24. P4 implementation and verification (Nov 18, 2026)

- `content/duel_system.json` is schema v4. It centralizes the bounded balance formula, stability thresholds/loss modifiers, the two current deterministic fall sources, WAIT recovery, Crawl/Rise costs/timing, partial rise stability, and grounded AI preferences. `config.py`, `model.py`, `session.py`, and `notebook.py` migrate v1–v3 config/snapshot/session/notebook data; old fighters default upright at full stability, and active action schedules/paths/deadlines are preserved. v3 combat snapshots, mid-action sessions, and a saved world's v3 `duel_journal` were loaded and deterministically continued.
- `stability.py` derives balance once from raw stats, load, fatigue, pain, and leg wounds; current stability is separate and persisted. A reachable heavy whiff and a successful ≥90° fast turn within the configured window emit source-tagged stability loss. Loss at zero latches GROUNDED and interrupts only unresolved action work; ordinary Walk and straight fast movement do not cause random slips. Only committed standing WAIT restores stability. `engine.py` enforces Cover/Guard, one-tile collision-checked Crawl, and timed exposed Rise; Rise restores the configured partial value only on completion, while a qualifying hit can interrupt it without a refund or extra action. Grounded Cover remains available when disarmed.
- Ronin's grounded candidate set contains only legal Rise/Cover/Crawl options. The immutable render projection shows stability and derived balance, source/fall events, grounded state, and only grounded controls; compact and wide dashboard panels explain exposure, collision-checked crawl, and interrupted recovery. The coach recommends only an enabled grounded choice, describes visible evidence/uncertainty, and does not suggest an attack or standing movement while grounded.
- Verification: **330** duel/UI/coach/layout/recap/playback/inspection tests passed. They cover safe movement, each loss cause, fall-at-zero latching and unresolved-action cancellation, no undo of a resolved heavy whiff, balance inputs without double-counting status penalties, grounded action legality (including disarmed Cover), crawl edge/occupancy, exposed Rise and interruption, WAIT-only recovery, AI candidate legality, render/coach purity and causal feedback, and v3 world-journal/session/snapshot migration with deterministic continuation. Compileall and a trailing-whitespace scan of all P4-touched files passed.
- Native `python tools/preview_duel_dashboard.py --output C:/Users/Warp/AppData/Local/Temp/opencode/combat-expansion-p4-grounded-final` passed **120 captures**: 1280×800, 960×700, and 640×600, including a real Heavy whiff at a controlled 1-point stability, the resulting grounded controls, Rise completion, prior movement/target workflows, and the native pause/resume check. Inspected compact and wide grounded/recovery captures show the fall popup/cause, stability/balance meter, available Rise/Crawl/Cover controls, exposed Rise timing, and the post-rise partial stability. This is scripted native verification, not unaided playtesting.
- Baseline distribution only (no tuning after this sample): `python tools/audit_duel_stability.py --fights 100 --seed 4100 --output C:/Users/Warp/AppData/Local/Temp/opencode/duel-stability-100.json`. Across 100 seeded fights per 1v1 fixture, ScriptedPunisher/Ronin, SpamHeavy/Ronin, and Kite/Ronin had no stability losses/falls; RandomBot/Ronin had 253 sharp-turn losses, 34 heavy-overextension losses, and 2 falls in 100 fights, with no Rise completions. The latter is a single bot/seed/fixture slice; it does not establish broad balance or realistic fall rates. The configured loss values remain prototype defaults. Falls and Rise remain rare in this sample, while the targeted tests/native walkthrough separately exercise recovery under controlled pressure.

## 25. P5 tactical-action and adrenaline design (Nov 18, 2026)

- All new mechanics remain one committed action per fighter per exchange. The engine gathers choices from the same pre-commit snapshot; there are no mid-exchange player reactions, free attacks, extra action slots, or hidden enemy-choice reads. Same-tick attacks resolve before forced movement/interruption. All costs, timing, contest modifiers, and trigger thresholds live in schema-v5 config; v1–v4 saves migrate without changing active action deadlines.
- Selected initial tactics are **FEINT, SHOVE, DISENGAGE, THRUST, and RIPOSTE-SETUP DISARM**. **BIND is explicitly deferred**: a bilateral weapon lock needs ongoing two-actor constraints, expiry/escape actions, and unequal-duration exchange rules; implementing it as a simple hit would violate the plan's own escape/counterplay requirement. Revisit during P7's common multi-actor/exchange-boundary design rather than silently approximating it.
- **Feint:** committed, non-damaging, short action. On completion, if still in reach and the opponent has an active committed Guard, drain a configured bounded amount of Guard stamina and consume any stored riposte; otherwise it has no effect. It does not bypass Guard or force a new response. Counterplay: do not Guard into a predictable feint; if Feint is ignored, it only costs the actor time/stamina. Record a distinct resolved event.
- **Shove:** committed close-range body contest; no wound/damage roll. Compare effective Strength plus configured fraction of derived balance, with a Guard brace bonus; ties favor the defender. Success pushes exactly one tile directly away only when floor/occupancy permit, then applies one configured P4 stability loss. A wall, occupied tile, Guard, range miss, or better defense is a readable failure; no overlap or teleport. Resolve shove movement after all same-tick strikes so it cannot erase a simultaneous hit.
- **Disengage:** one-tile movement that must increase current separation. It costs stamina and has its own configured timing, but is slower than Dash; it grants no invulnerability, and a same-tick strike still resolves before movement. Runtime obstruction remains collision-checked.
- **Thrust:** a slower, committed aimed strike using the existing wound/armor pipeline and no new armor-penetration rule. Cutting and piercing weapons are suitable; blunt weapons cannot thrust. Configure power/accuracy/recovery tradeoffs so it is not a strictly better Light/Heavy. Existing guard, reach, jitter, interruptions, and post-armor severity remain authoritative.
- **Disarm:** legal only while the actor has a riposte earned by a successful parry (explicit setup); a committed hand-target action with slower timing, stamina cost, and additional accuracy difficulty. A clean, non-drifted hand contact must win a configured Strength/grip contest to set the existing `disarmed` state. Failure may still cause only the normal hand wound consequences; it never grants a free follow-up. Guard/parry, hand exposure, aim drift, range, and the setup requirement are counterplay. Dropped-item identity/condition remains P6.
- **Adrenaline:** an explicit committed action, available once per duel only after meaningful danger/exertion (configured blood, pain, fatigue, or stability threshold). It costs time/stamina, activates for a short configured duration, and suppresses only the effective-stat pain penalty by a bounded fraction; it does not heal, restore blood/stamina/stability, or improve composure. Expiry applies configured fatigue debt once and emits a cause event. Persist activation/expiry/used/debt state; carry the once-used gate and unapplied debt through `restart(carry=True)` so successive carried duels cannot farm the surge. A fresh independent duel resets it.
- AI candidates/score provenance and Strategy Coach may use only legal actions plus visible/recorded evidence. UI/readout/recap must distinguish selected tactic, cost/timing, failure/counterplay, and adrenaline active/expiry/debt. P5 measurements compare action distributions, fight length, falls, hand wounds/disarms, guard stamina and fatigue debt before changing candidate tuning; no universal-winnability claim follows.

## 26. P5 implementation and verification (Nov 18, 2026)

- Implemented FEINT, SHOVE, DISENGAGE, THRUST, and parry-setup DISARM as committed actions using the existing two-fighter scheduler. Resolution preserves strikes-before-forced-movement, collision checks, the normal wound pipeline, and no free follow-up. BIND remains deliberately deferred to P7's exchange/escape design.
- Schema-v5 `content/duel_system.json` centralizes the P5 timings/costs, action scores, contest values, and adrenaline parameters. Adrenaline is gated by blood ≤55, pain ≥30, fatigue ≥55, or stability ≤30; it costs the configured action time/stamina, activates only on completion, lasts 30 ticks, suppresses 55% of the pain-derived stat penalty only, and incurs 18 fatigue at expiry. Interrupted use remains spent but activates neither effect nor debt. Debt applies once; carry restarts retain the once-used gate and remaining duration/debt, while a fresh duel resets them. Legacy v1–v4 config/session data receives safe defaults without rewriting active action deadlines.
- AI and guide enumerate only legal actions; candidate reasons and outcome learning are event-grounded. Tactics controls expose cost/timing/requirements, and the Tactics panel plus exchange details show adrenaline trigger, active deadline, suppression, interruption, expiry and debt. The disarm recap names the grip contest.
- Verification: `python -B -m pytest -q (Get-ChildItem -Path tests -Filter 'test_duel*.py' | ForEach-Object { $_.FullName }) tests/test_play_inspection.py` — **350 passed**. Coverage includes action legality/costs/setup, feint guard disruption/no-effect, shove contest/blockage and strike ordering, thrust resolution, adrenaline trigger/one-use/interrupt/expiry idempotence, save/load determinism, v4 migration, carry/fresh restart, recap and read-only previews. `python -B -m compileall -q ...` and scoped `git diff --check` passed.
- Native `tools/preview_duel_dashboard.py` passed **159 captures** at 1280×800, 960×700 and 640×600. Inspected the compact tactics panel, scrolled adrenaline preview, active timer/suppression/debt, and post-expiry status; all hitboxes remained on-screen and rendering remained read-only.
- Seeded descriptive audit (`tools/audit_duel_tactics.py`, 50 fights each, seed 6200; artifact `C:/Users/Warp/AppData/Local/Temp/opencode/duel-tactics-p5-final-50.json`; config SHA-256 `3f8a643447243b2d9939759c83f837cb496ba103f1c782fd852ac0e8e4cf8982`) found no timeouts or falls in Punisher, SpamLight, Random, NaiveGuard or mirrored Punisher samples. Ronin used adrenaline 12/50 vs Punisher (9 activated, 3 interrupted; 162 fatigue applied) and 9/50 vs SpamLight (all activated/expired; 162 fatigue applied), and used only 2 Shoves, 1 Disengage and 7 Adrenaline against Random. Against NaiveGuard, Ronin feinted 150 times/50 fights (all disrupted Guard; 1,272 guard stamina drained); this same-seed 50-fight zero-tactical-score baseline also had Ronin win all 50, with median total decisions 3 instead of 6, so Feint is not credited with causing those wins and can extend this intentionally repetitive matchup. The mirrored sample remained 25–25, as did its zero-score baseline. RandomBot itself used multiple tactics; counts are actor-separated in the audit.
- These are narrow policy samples, not broad balance or universal-winnability evidence. Disarm was covered in constructed setup/contact/recap regressions but was not selected in these five seeded matchups; rare AI use and wider player-facing balance remain open for later measurement. No score retuning was justified by these slices alone.

## 27. P6 severe outcomes and equipment design (Nov 18, 2026)

- Scope boundary: the sword-duel screen is a persisted combat drill in `World.duel_journal`, not the ship crew's legacy `CombatArena`. P6 persists consequences in the duel session, save/load, and `Next` carry only. Do not apply them to the map avatar, crew inventory, or legacy boarding combat without a separately specified stat/ownership adapter; do not expose them in ordinary campaign encounters in this phase.
- Schema v6 adds stable weapon item identity, name, material, and condition (0–100); a fighter's held weapon becomes optional, and dropped weapons live as identified ground items with position. Ground items do not block movement and may stack beneath occupied fighters. A disarm, severe hand wound, or break moves the exact item to the ground rather than cloning it. Pickup is a committed 20-tick action, explicitly selects an in-range item, and can replace a held weapon by dropping the old one at the actor's tile. Broken items remain visible but cannot be equipped; a missing weapon uses the existing unarmed-light rule. A severed weapon hand prevents pickup/replacement. Preserve ground items on save/load and carried-next-duel state.
- Condition wear is deterministic and explainable: only configured hard contacts (successful parry and heavy guard contact) stress a weapon; the wear formula uses action/contact type, material resistance, and current condition. Ordinary flesh hits do not degrade weapons. Condition affects weapon edge; reaching zero emits a cause-rich break event and drops the broken item. No independent break RNG or universal break-on-hit rule.
- Extend anatomy with a distinct `EYE` aim point and high configured accuracy requirement; missed eye aim falls back to `HEAD`, and head aim never implies eye damage. A clean, non-drifted eye contact meeting the configured after-armor threshold records bounded permanent eye impairment, with explicit accuracy consequences and visible state. Temporary head concussion remains a timed status; unconsciousness remains a terminal incapacity, not the same injury. Only a clean, non-drifted heavy CUTTING hit to HEAD meeting a high configured after-armor threshold can cause deterministic decapitation/death. No critical roll or organ inference. Existing clean heavy cutting hand severance remains; hand loss prevents weapon use/recovery.
- Store lasting hand/eye injury in a distinct persistent injury record, not the temporary status list. `postfight_state` and `restart(carry=True)` preserve permanent injury, weapon condition/identity and ground items; active temporary concussion status carries only its remaining duration. Dead or unconscious fighters cannot carry into another duel; death, unconscious defeat, yield, and flee remain distinct outcomes. `Retry` is a fresh independent drill. The default duel is explicitly not a medical-care simulation; no recovery from permanent injury is implied.
- P6 acceptance includes controlled reachability and negative cases for break/sever/eye/decapitation thresholds; hand-severed fighters cannot grip or pick up, unconscious/dead actors cannot choose, guard/armor/aim drift can prevent severe outcomes, and no occupied ground item causes overlap or duplication. Migrate v1–v5 config/snapshot/session/world journals to schema v6 while preserving active deadlines and deterministic continuation.

### P6 implementation and verification (Nov 18, 2026)

- Carry and save integration stays within the duel journal. `postfight_state` retains blood, fatigue, stamina, wounds, nervousness, and permanent injuries; `restart(carry=True)` preserves weapon identity/condition, hand loss, ground items, and only the remaining duration of active temporary statuses. Retry remains a fresh drill. Tests reject carry after death/unconsciousness and confirm a conscious winner can continue after an unconscious opponent; yield/flee/death/unconscious remain distinct terminal reasons. No changes bridge these outcomes into ship crew or legacy combat.
- Migration coverage: v1–v4 configuration/snapshot/session/world-journal paths remain covered by their phase tests; new v5 tests migrate missing weapon/injury fields and an active RUN deadline through `World.save`/`World.load`, then compare deterministic continuation from two restored copies. New-session and carried-session world-journal roundtrips preserve wounds, temporary concussion remainder, permanent eye impairment, weapon condition and ground-item identity.
- Focused P6 suite: `python -B -m pytest -q tests/test_duel_system_severe_outcomes.py` — **16 passed**. Full scoped duel/UI/inspection suite: `python -B -m pytest -q (Get-ChildItem -Path tests -Filter 'test_duel*.py' | ForEach-Object { $_.FullName }) tests/test_play_inspection.py` — **366 passed**. Scoped `compileall` and an explicit trailing-whitespace scan across 19 P6-related files passed.
- Native `tools/preview_duel_dashboard.py` passed **189 captures** at 1280×800, 960×700, and 640×600. Reviewed ground-item/pickup selection, replacement recap, explicit eye target and permanent-injury recap, and single-popup fatal outcome. The dashboard verifies scripted controls/layout/read-only rendering, not unaided playability.
- Seeded descriptive audit: `tools/audit_duel_severe_outcomes.py --fights 100 --seed 7300` (artifact `C:/Users/Warp/AppData/Local/Temp/opencode/duel-p6-severe-100.json`; config SHA-256 `905eaf18a780adb282e52ecf06e08e9afc967c4e649610c54f6b23d888805d4b`). Punisher, SpamLight, and mirror each had zero P6 events in 100 fights. Random had 6 stress contacts in 3/100 fights (4 steel parries, 2 steel heavy blocks), 3 drops, and 1 incapacitation; there were no breaks, severances, eye injuries, or deaths in these ordinary samples. Controlled tests prove severe outcomes are reachable and negative gates work. This is descriptive policy-sample evidence, not broad balance or universal-winnability evidence.

## 28. P7 multi-actor design decisions (Nov 18, 2026)

- **One encounter state, not pairwise duel calls.** Generalize `CombatState` to explicit team membership and N unique actors. The first player-facing scenario is one controlled fighter versus a hostile group; the state and resolver support additional teams/allies. Preserve the existing 1v1 factory and all old action semantics as the two-actor special case. Schema v7 persists team identity, explicit committed target identity, perception memory, and group-event state; v1–v6 defaults retain the player-vs-opponent behavior and active schedules.
- **Exchange boundary.** At each exchange start, gather at most one voluntary commitment for every active participant from the same immutable pre-commit snapshot. Resolve all committed actions concurrently on the common integer-tick timeline. Faster actors wait after their one action until the exchange closes; interrupted actions remain spent. Guards auto-release by the configured exchange deadline so all-guard exchanges cannot stall. Actors entering after the commitment snapshot join next exchange; actors yielding, fleeing, dying, or becoming unconscious cannot commit again. No mid-exchange player reactions or free NPC follow-ups.
- **Explicit targets and strike obstruction.** Attack, feint, shove, and disarm choices lock a `target_id`; the existing two-fighter API may infer its sole hostile target for compatibility. No strike auto-retargets if its target moves or becomes invalid. Evaluate reach and a deterministic tile line at impact; an intervening actor (ally or hostile) or blocked arena tile prevents melee attacks through bodies/walls. Legal targets are active hostiles only. Keep strike-time defense snapshots and same-tick trades.
- **Occupancy and deterministic movement contention.** Every non-fled actor occupies its tile, including incapacitated/dead bodies. Gather all due movement intents before changing positions. Multiple actors targeting one destination all fail; unique moves into empty tiles succeed; a chain can enter tiles vacated by successful movers, but swaps/cycles fail to prevent pass-through. Resolve dependency graphs independent of actor ID; emit the same cause/blocked outcome for every participant in a conflict. Movement paths still reject known occupied intermediates at commitment, and runtime obstruction cancels remaining route steps.
- **Perception and group behavior.** NPCs select only currently visible hostiles; visibility uses configured range and tile-line obstruction. Hearing can update a bounded last-known contact but cannot expose current off-screen positions. Group coordination uses existing bounded personality traits: undisciplined recruits may crowd, while disciplined actors avoid occupied approach lanes and preserve spacing. No global sharing of live observations or private profile/choice data.
- **Witnessed morale.** Add explicit leader identity per group and cause-tagged group morale events. A bounded perception pass applies configured morale changes only to allies who saw/heard a qualifying wound, incapacitation/death, failed rush, or leader loss in the current/previous exchange. Apply at most one response per witness per exchange; morale changes do not recursively propagate. Retreat/yield remains individual and can fragment a group rather than forcing an all-at-once rout.
- **UI/performance acceptance.** Render all actors on the arena map, but show at most the three nearest visible hostile summaries plus an overflow count. When multiple hostile targets are available, insert a target-selection panel before zone selection; show identity, approximate range and public posture only. Benchmark deterministic tick/round time, action counts and allocations for 2/5/10/20 actors; inspect native group views at the supported dashboard sizes. Measure isolation and encirclement separately; do not promise 1v20 viability.
- **BIND revisit.** P5's deferral has been reviewed against this architecture. BIND remains outside P7's minimum acceptance: the plan's required P7 work is multi-actor exchange, obstruction, coordination, morale, UI, and scale, while a bilateral lock needs its own clear contest/escape/expiry specification. Do not implement a one-hit placeholder; retain the explicit deferral for a separately scoped lock/escape design.

## 29. P7 implementation and final verification (Nov 18, 2026)

> Follow-up impact expansion: see §30 below. Existing implementation evidence does not establish satisfying hit feel or integrated physical consequences.

- **Multi-actor exchange and terminal scheduling.** The encounter remains one `CombatState`; explicit team/target identity, shared-snapshot NPC plans, one commitment per active participant, deterministic movement contention, strike obstruction, local visibility/hearing, witness-limited morale, and the configured guard deadline are implemented. A regression uncovered that session-level fear counters were only advancing when a yield already happened; NPC decisions now advance/reset fear once each, and the configured three-decision break can yield in both 1v1 and group sessions. Another regression found terminal actors could retain stale action records, blocking the next group exchange; terminal commitments are now interrupted, and inactive actors cannot strike, move, or receive passive updates. Commitments are not refunded.
- **Compatibility and invariants.** Schema-v6→v7 state/session migration preserves an in-flight movement path/deadline and deterministic continuation while adding group defaults; current group-session roundtrips preserve exchange commitments and replay. Tests cover active-path contention, cycles, bodies, explicit target lock, obstruction, action counts, deadline release, witnessed events/morale, hidden-state rendering purity, and incapacitated actors' stale strike/movement commitments. P5 BIND remains deferred.
- **Group UI and launch.** The group sandbox is separate from the legacy one-on-one game route: `python tools/play_duel_group.py --actors 5` (also accepts 2/10/20 total actors). Group cards show at most three visible threats plus overflow and omit enemy profile/stat/action internals; target choices use visible glyph/name, approximate range and posture. The native audit `python -B tools/preview_duel_group.py --output C:/Users/Warp/AppData/Local/Temp/opencode/combat-expansion-p7-group` passed 12 actor-count/window combinations (2/5/10/20 at 1280×800, 960×700, 640×600), controls/hitbox bounds, target-lock → strike → recap, and the real SDL live loop's pause/save serialization. Five native frames confirmed one player commitment, five-actor restore, and unchanged ship-world state. Inspected the 20-actor compact ready, multi-target list, and resolved exchange captures. Rendering stayed read-only.
- **Visible actions and startup UI fix.** The six P5 tactics (Feint, Shove, Disengage, Thrust, Disarm, Adrenaline) are directly reachable from the main **Tactics** action button; `More` retains its Tactics shortcut. Disabled actions remain hoverable/clickable for their exact reason, and a disabled click does not advance combat. A live hover over dynamic `Stability (bal N)` previously raised `KeyError`; stat-help now normalizes the label and explains stability. The native audit at `C:/Users/Warp/AppData/Local/Temp/opencode/combat-expansion-p8-dashlight-ui` includes inspected action-row, tactical, locked-reason and Stability captures.
- **Dash+Light target flow.** `pirates/duel_ui.py:318-375` chooses the shortest legal clear straight dash ending within weapon reach. One reachable hostile is locked and opens only hit-zone selection; multiple reachable hostiles get a filtered target list; none gives a clear out-of-range/no-path message. The dashboard labels the hit-zone panel and explains the automatic approach, and the multi-target panel states that direction/distance are automatic. No engine rules changed. Regressions in `tests/test_duel_ui.py` cover single target, filtered multiple targets and out-of-range; the full scoped duel/UI/inspection suite passed **391 tests**. Native preview passed 12 actor-count/window combinations and added inspected `group-dash-light-auto-target.png` and `group-dash-light-multiple-targets.png` captures alongside action/locked-reason captures.
- **Controlled balance examples.** `python -B tools/audit_duel_group_balance.py --samples 10 --output C:/Users/Warp/AppData/Local/Temp/opencode/duel-group-balance.json` (config SHA-256 `05578058ef9e4e8d7f4d140021c45b35732587ff6c4fc20af512630905b828a7`) recorded 10/10 wins in an intentionally favorable choke-point fixture: a strong player, a one-tile corridor, low-morale opponents and an injured leader, with a fixed NPC policy that attacks only visible in-reach targets and otherwise waits. The leader was incapacitated, three allies yielded after witnessed leader-loss morale responses, player blood ended at 88.33, and at most one distinct attacker hit on a tick. This demonstrates a controlled isolation/morale path, not ordinary matchup odds. In the separate open four-sided surround with normal group AI and a depleted player, 10/10 seeded cases incapacitated the player in two exchanges/10 ticks: eight hits, up to four distinct attackers on a tick, 23.8 blood remaining at collapse. This verifies lethality, not universal inevitable defeat.
- **Scale sample.** `python -B tools/audit_duel_group_scale.py --rounds 5 --seed 5200 --output C:/Users/Warp/AppData/Local/Temp/opencode/duel-group-scale.json` separately measured fixed-WAIT scheduling and normal-AI decisions. Fixed-WAIT five-round median exchange time for 2/5/10/20 actors: 1.460/3.434/8.479/24.195 ms. Normal-AI one-exchange median: 3.883/20.780/55.039/119.271 ms; maxima were 221.722/24.170/85.183/124.940 ms, including a single noisy 2-actor outlier. Mean pure render-model time was 11.876/9.794/17.222/35.214 ms. Action counts and deterministic replay assertions passed; peak traced allocation for the normal-AI sample was 315,740/345,072/396,641/497,254 bytes. These small synthetic workloads are scaling evidence, not a frame-time guarantee or a 1v20 balance claim.
- **Regression and audit.** The full scoped duel/UI/inspection suite passed **387 tests** after the scheduler changes; the subsequently added v6 session-migration regression passed in the focused migration run (`2 passed`, including the adjacent snapshot migration test). This yields coverage of the current scoped cases without claiming a single combined post-addition run. Scoped compilation, plus an explicit touched-file trailing-whitespace audit, passed. `python -B -m pytest --collect-only -q` collected 1,309 repository tests; a repository-wide run was not started because separate `python -m pytest -q` and 10-year simulation processes were already active. No unrelated processes were stopped. The native walkthrough is scripted; unaided player feedback was not collected. These limits do not weaken the scoped combat acceptance evidence and remain explicit.
- **Latest-exchange verdict UI.** `pirates/duel_system/exchange_log.py:8` computes only the player's recorded after-armor HIT net damage: dealt minus taken, with bystander-only group hits excluded. The latest-exchange panel shows a green `GOOD FOR YOU`, red `BAD FOR YOU`, gold `EVEN`, or neutral `NO DAMAGE TRADE` verdict and the numeric edge plus dealt/taken values. It explicitly says this scores damage trade only, not position/defense. `tests/test_duel_recap.py` covers positive/negative/even/no-player-hit scoring and restore/reset behavior. Full scoped duel/UI/inspection suite: **392 passed**. Native preview passed and the inspected live recap capture is `C:/Users/Warp/AppData/Local/Temp/opencode/combat-expansion-p9-round-verdict/group-latest-exchange-score.png`; this is a damage comparison, not a comprehensive tactical grade.

## 30. Combat impact and consequence integration expansion

**Status: active — I0–I4 complete; I5 AI/release gates in progress.** Requested after hands-on feedback: shoves, head hits, flinches and bleeding do not feel consequential. This is a mechanics-and-presentation expansion, not a cosmetic patch or authorization to implement everything immediately.

### 30.1 Verified starting gaps
- `pirates/duel_system/engine.py:551-627`: shove compares attacker/defender strength plus balance, with guard bracing. Only a winning shove moves the target (one tile), and only those actually moved lose stability. Resisted and wall-blocked contact produces no shove stability loss. This is an actual integration gap, not just missing animation.
- `pirates/duel_system/wounds.py:68-104,107-192`: head wounds already cause configured dizziness, concussion/stagger and threshold-based unconsciousness; injury shock separately selects HOLD/FLINCH/RECOIL. Do not describe those existing effects as absent or fix them by duplicating their penalties.
- `pirates/duel_system/stability.py:46-72`: zero stability causes a fall. Balance is a derived resistance rating, while stability is the current reserve. Feedback must distinguish them rather than promising every shove lowers both.
- `pirates/duel_system/wounds.py:195-224`: recoil has a cooldown and unavailable-reflex gates. A flinch is not itself tile displacement. A stationary reaction needs a visible body response; actual forced steps must move simulation coordinates.
- `content/duel_system.json:18-29`: wounds already have zone/severity-dependent bleeding and contact-type multipliers. The follow-up audit must measure actual drain over exchange duration and how it appears in the live UI, not blindly increase bleed values.
- Latest-exchange grading currently measures after-armor hit power, not actual blood loss or tactical advantage. It cannot honestly rate a successful shove, parry or bandage as good/bad overall.

### 30.2 Design contract: one causal consequence pipeline
- Resolve contact quality and armor, then physical consequences (wound, impulse, stability, function), then permitted reflex/interrupt, then collision-safe displacement/fall, then recovery. Keep the existing same-tick strike fairness; never retroactively erase a landed strike.
- Physical resistance comes from strength, current stability, derived balance, footing, stance/bracing, movement and injuries. Composure changes psychological response, not immunity to physical force or structural injury.
- Distinguish wound severity, transferred impact and visible reaction. A resisted contact can tax stability/stamina without moving the target. A glancing armored head hit is not automatically a concussion or fall.
- One contact must not apply duplicate losses through shove, wound shock and recoil. Preserve cause/attacker attribution and record requested versus applied consequences and reasons for reductions.
- Both sides obey the same rules. Keep one voluntary commitment per fighter/exchange; forced reactions are consequences, not free player actions.

### 30.3 I0 — Reproduction and causal audit
- Create fixed-seed fixtures for weak/equal/strong shove, guard brace, moving target, low stability, wall/body obstruction; head graze/cut/deep hit with/without armor; superficial versus severe bleeding; flinch versus recoil versus fall.
- Record before/after position, stability/balance, stamina, wounds, blood, active impairment and remaining action timing, together with contact outcome and actual playback frames.
- Trace delayed/double-applied effects, reaction cooldowns, hidden status expiry and whether playback skips the impact. Preserve representative baseline fixtures for later comparison.
- Acceptance: every reported symptom is classified as absent mechanic, weak tuning, resisted effect, or missing communication. No blanket stat buffs as a substitute for diagnosis.
- **I0 baseline evidence (Nov 18, 2026).** The deterministic fixture tests already existed; `tests/test_duel_system_tactics.py:79-137`, `tests/test_duel_system_injury_response.py:72-183`, and `tests/test_duel_system_wounds.py:39-90` cover shove, reflex/collision, same-tick and bleed contracts. The focused tactics/injury/wound/stability/recap/playback run passed **87 tests**. A fixed probe measured equal shove = `RESISTED`, no move/no stability event; strong (40 vs 1) = `SUCCESS`, one tile, stability `100→82.036` (17.964 loss); same shove into wall = `BLOCKED`, no stability loss. Net-16 deep head hit: Veteran got `concussed` + `FLINCH`, position unchanged; Recruit got `NO_REFLEX` because the head stagger gate made `can_act` false. Veteran playback contained 8 frames but all target positions remained `(5,3)`, so no native in-place flinch motion exists. A 6/turn wound drained 4.2 blood over 10 idle ticks (`6×0.7`), matching configured exertion. Current UI exposes rate before exertion, not bleed-attributed actual exchange loss. Classification: shove effect is binary/missing on resistance/block; head functional injury exists but visible feedback/reflex integration is incomplete; bleeding arithmetic exists and is exertion-sensitive, while causal presentation is incomplete. No tuning was changed during I0.

### 30.4 I1 — Shoves, impact and forced movement
- Replace the success-or-nothing physical response with graded outcomes: fully braced, strained resistance, off-balance, forced step, strong push, knockdown. Out-of-range/missed contact still has no impact.
- A meaningful connected shove against a somewhat stronger unbraced target should normally cost that target some stability or stamina, even when it cannot displace them. An overwhelming strength/brace advantage may absorb it; show why. Do not guarantee chip effects from arbitrarily weak repeated shoves.
- Proposed displacement range: 0–2 tiles, determined by delivered impulse versus resistance, with one tile as an ordinary successful push and two reserved for a strong advantage. I0 contest margins anchor provisional thresholds; tiles are not literal meters and multiple-tile movement is not a universal realism rule.
- Consume residual impulse step by step against footing and available space. Walls/bodies stop travel; a bounded residual stability consequence can remain, without inventing contact damage or launching chain pushes by default.
- Use one deterministic forced-motion resolver for shove and recoil, with occupied-tile checks, stable simultaneous conflict handling and no teleportation through bodies/walls. Define fast-movement interruption after a forced displacement so stale paths cannot snap actors back.
- Acceptance: demonstrate a resisted-but-strained shove, one/two-step pushes, wall-stopped impact, repeated pressure into a fall, and a strongly braced defender resisting; no overlap, unlimited chain stun or free attacks.
- **I1 verification (Nov 18, 2026).** Schema v8 adds centralized `tactics.shove_move_margin=12`, `shove_two_step_margin=55`, stability impact range 3–18 across a capped 55-point positive margin, 0.65 braced impact multiplier and 1.0 blocked impact multiplier. Contest margin remains attacker effective strength + configured balance share − defender equivalent − active guard brace. Current labels are `RESISTED` (nonpositive), `OFF_BALANCE` (positive but below displacement threshold), `SUCCESS` (one/two requested steps), and `BLOCKED`/`PARTIAL_BLOCKED` (space truncates travel). At 10/10 the measured margin is −0.249 from the existing derived ratings, not mathematically zero; 20/10 gives +10.751 (off-balance), 40/1 +42.651 (one step), 70/10 +65.751 (two). The former 18-point successful-shove impact was an exact value; v7→v8 maps it to `shove_max_stability_loss`, while keeping active action deadlines and old config/session/notebook/world journal readable. New `pirates/duel_system/forced_movement.py` is shared by shove and injury recoil: each plans against one occupancy snapshot, rejects a shared destination for every contender, stops at walls/bodies and never pushes chains. Any unresolved target action is interrupted only after same-tick strikes and movement; regression proves an old RUN path cannot snap the target back after a diagonal two-step shove. Tests cover weak/equal, off-balance, one/two steps, wall/body blockers, simultaneous same-destination shoves, same-tick strike, moving-target, bracing, repeat/re-engaged pressure to fall, stale path interruption and v7 deadline/tuning migration. Full scoped duel/UI/playback/inspection suite after the shared-resolver refactor passed **527**. Seeded P5 audit (`C:/Users/Warp/AppData/Local/Temp/opencode/duel-i1-tactics-40.json`, 40 fights/policy) saw only 4 total SHOVE decisions across Random/Ronin, all `OUT_OF_RANGE` or `RESISTED`, and zero falls; it is too low-use to validate shove balance. Constants remain hypotheses, not accepted final balance.

### 30.5 I2 — Anatomical impact, stagger and knockdown
- Feed meaningful hit impact into stability through the shared pipeline, not only heavy misses/fast turns/successful shoves. Scale by zone, contact, armor, action commitment and current posture.
- Head: visibly separate graze/dizziness, concussion/stagger, knockdown and unconsciousness. Strong clean impacts can collapse a poorly balanced fighter; a weak glancing hit must not guarantee a fall. Keep existing accuracy/speed impairment and avoid double penalties.
- Torso: show breath/stamina loss and interrupted exertion where warranted. Leg: make loss of support affect movement/balance. Arm/hand: make impaired force/grip visible in the next action, not merely listed in a log.
- Separate structural leg disablement from unconsciousness in the design review: inability to stand should use grounded/impaired behavior unless a separately justified incapacitation condition is reached.
- Define which response delays, interrupts or leaves each action intact, with bounded recovery and an explicit escape from repetitive stun. Stabilization/rising/cover must provide counterplay rather than an unavoidable fall loop.
- Acceptance: low/high impact head fixtures yield distinct outcomes, consequences remain into the next exchange where intended, and the same rules work for player and NPC without hidden physical immunity.
- **I2 starting audit (Nov 18, 2026).** `resolve_strike` already computes post-armor `net`, zone/severity/depth/structure, damage type and `shock_load`, then queues at most one FLINCH/RECOIL per fighter/tick; existing strength-12/deep injury fixtures make appropriate persistent function/status changes. A fixed veteran probe by zone produced DEEP/net-16 hits with shock responses FLINCH, stability unchanged at 100 in all five tested zones (head CONCUSSION; torso TORSO_WALL; leg/arm/hand TENDON). `apply_stability_loss` in `wounds.py` currently covers only OVEREXTENDED_HEAVY_WHIFF; wound shock is a separate composure/response calculation. Thus a hit can impair function and visibly recoil, but there is no direct resolved-contact→stability transfer. Do not reuse composure-scaled shock as physical impact or double the same stability penalty through recoil; derive a bounded impact signal from after-armor contact/severity/anatomy and make its cue causal. I2 formula/tuning and tests remain pending.
- **I2 causality checkpoint (Nov 18, 2026).** Schema v9 adds centralized `injury.impact_*` tuning. Impact request is `max(0, post_armor_net−8) × 0.45 × severity × anatomy × contact × defense`, capped at 8 before the existing derived-balance loss multiplier; BRUISE has zero multiplier. Deep arm/hand impacts are deliberately smaller, torso/head/leg larger; cutting/piercing/blunt and OPEN/DEFENSELESS/GUARD_HOLD modifiers are separate from composure-scaled `shock_load`. Actual requested/applied stability change is recorded on the HIT event and a `STABILITY/HIT_IMPACT` event. Weak net-5 graze and armor-reduced net-6 hit produce no stability loss; tested net-16 deep hits produce anatomy/contact-scaled effects, with an uncovered leg hit under held guard attenuated. V8 active session migration fills the new tuning while preserving its movement path; prior schema config/session/notebook/world-journal migration regressions still pass. `tests/test_duel_system_injury_response.py` covers anatomy, post-armor armor gate, posture/contact modifiers, cap, reflex non-duplication and migration. Full scoped duel/UI/playback/inspection suite passed **533**. A 100-seed/policy 1v1 stability sample at seed 7100: with cap14/per-net .65, RandomBot/Ronin had 15 falls; a paired config with hit impact disabled had 2. Reduced tuning (current cap8/per-net .45) produced 5 falls, 3 RandomBot wins and median 58 ticks versus disabled impact's 2 falls, 3 wins and median 58; Punisher/SpamHeavy/Kite each had zero falls in the same 100-seed sample. This is a narrow tuning probe, not an ordinary-match balance claim. Reactions/UI and broader conditions remain to verify.
- **I2 verification checkpoint (Nov 19, 2026; phase complete, presentation remains I4).** Schema v10 adds `stagger_repeat_duration_mult=0`: the first qualifying head hit applies configured stagger; another qualifying hit while active does not extend expiry, while preserving per-hit wound/impact/shock. `test_repeated_head_hits_do_not_refresh_stagger_without_bound` confirms expiry and renewed action eligibility. FLINCH remains in-place, RECOIL is one collision-safe tile with a 10-tick refractory gate, zero stability latches grounded fall, and unconsciousness remains an explicit head threshold. `test_same_tick_strike_still_lands_after_hit_impact_fall_without_recoil_moving_grounded_target` confirms both committed strikes land before reflex displacement, and a fighter grounded by the first hit does not recoil-move. Head graze vs deep fixtures prove zero vs positive stability impact and dizziness vs concussion; side-swapped role-symmetry and identical seeded state replay tests pass. V8/v9 active session migration and older state/journal migrations are covered. Seed-7100 cap8/v10 audit: Random/Ronin 5 falls/100, 3 Random wins, median 58 ticks; Punisher/SpamHeavy/Kite had zero falls. Full scoped duel/UI/playback/inspection regression passed **539 in 87.40s**. Limits: only fixed synthetic 1v1 policies; in-place FLINCH remains visually unproven and needs I4 native/playtest. I2 implementation/verification is closed; bleeding, recap and visible feedback remain outstanding.

### 30.6 I3 — Bleeding that is understandable and persistent
- Show player total active bleed in blood units per turn (10 ticks), active wound count, bandaged versus untreated sources, and actual blood lost to bleeding during the latest exchange. Separate bleeding loss from other causes and after-armor hit power.
- Give visible enemies observable cues: blood trail/dripping, worsening posture and qualified heavy-bleeding labels. Group mode must not expose exact private pools or unseen injury updates.
- Use event-derived droplets/trails and blood-meter loss echoes where supported; cosmetic marks must not imply slippery terrain or extra gameplay effects unless explicitly implemented.
- Escalate persistent warnings by measured rate and current player blood condition; label any future-loss estimate as conditional on unchanged bleeding. Do not offer a confident death timer while wounds/treatment can change.
- Bandage shows the source treated and remaining aggregate rate. Keep drain proportional to simulation ticks, never render frames or arbitrarily sized rounds.
- Acceptance: compare light/severe/multiple wounds over equal ticks, treatment and pause/resume; the player can see why blood is falling and verify the reported amount.
- **I3 implementation checkpoint (Nov 19, 2026).** Passive drain remains simulation-tick based and existing exertion/cap/bandage modifiers are unchanged. `engine.apply_passive` now tags each pool event that includes actual bleeding with `BLEEDING`, positive `bleed_loss`, effective `bleed_rate_per_turn`, and exertion; this is blood actually removed after clamping at zero, so the exchange ledger does not confuse strike net with bleed loss. Bandage completion emits a cause-tagged status event naming the treated zone/severity and before/after capped aggregate rate. Player injury rows distinguish untreated from partially bandaged sources; aggregate warning is capped and states `/turn (10 ticks)` before exertion. `format_exchange` totals actual wound-driven loss across the exchange and reports treatment/rate changes. In group combat, recap uses only witnessed/player-authored events; enemy pools/rates remain private, witnessed WOUND gets a coarse “Bleeding wound” cue, and witnessed enemy bleeding/bandaging states are qualified with exact loss/rate withheld. Added tests compare ten WAIT ticks with six deep wounds (stack cap 20/turn × idle 0.7 gives exactly 14 blood lost, matched by ten source events), bandage attribution and 9→5.4 capped rate, player rate/treatment readout, visible coarse group cue and hidden-wound/pool/recap privacy. The scoped duel/UI/playback/inspection suite passed **543**. No death timer or terrain consequence is implied. Limitation: blood-loss recap is exact for the player/solo opponent only when events are available; group enemies show uncertainty, and visible wounds remain evidence from witnessed WOUND events rather than omniscient inspection. Pause is represented by no simulation ticks; no render-time drain was introduced.
- **I4 playback/recap/native checkpoint (Nov 19, 2026; phase complete).** Schema v11 adds `ui.reduced_motion=false`; v10 active-session/config migration fills it without changing combat state. Hit popups identify transferred impact; the existing short severe-hit frame accent is now tied to impact, the recipient gets a deterministic directional sub-tile sprite pulse (never a tile move), and flinch gets a bounded in-place pulse. `ExchangePlayback` adds a brief 0.08-second presentation-only impact hold; reduced motion disables the pulse/flash and shortens movement interpolation. Simulation state, ticks and RNG are untouched (playback-recording purity regression still passes). Arena popup priority now limits simultaneous labels to two per actor, favoring decisive impact/recoil/fall over lesser status noise. Latest exchange UI explicitly labels **DAMAGE TRADE**, separately reports damage amounts, and adds compact Effects with stability/control, forced travel, bleeding, incapacitation, treatment and stamina where present; full checks/cost details remain behind Details. Group recap only consumes witnessed/player-authored events and withholds exact NPC blood/stability/rates. `tests/test_duel_playback.py` verifies sub-tile flinch and reduced-motion suppression; recap tests verify category separation and group uncertainty. Native `tools/preview_duel_dashboard.py --output C:/Users/Warp/AppData/Local/Temp/opencode/duel-i4-dashboard-verified` passed **189 captures**, including solo impact playback/outcome at 1280×800, 960×700 and 640×600. `tools/preview_duel_group.py --output C:/Users/Warp/AppData/Local/Temp/opencode/duel-i4-group-verified` passed group size/count, target lock and now group recap/playback captures at all three sizes. Inspected 640 captures: damage trade, outcome/effects and next controls are visible without opening Details; high-priority impact/flinch cues remain attributed and bounded. Full scoped duel/UI/playback/inspection suite passed **548 in 83.17s**. Native captures are not human playtest evidence. No screen shake is currently used; reduced-motion is available through the centralized duel config.
- **I5 AI-observation checkpoint (Nov 19, 2026; AI response complete, balance next).** Schema v12 adds centralized `tactics.ai_scores` for witnessed bleeding patience, bandage urgency, WAIT stability recovery, shoving witnessed unsteady targets, and safe open-space shove opportunities. Group `observe()` no longer copies a visible hostile's exact blood, wound list, or strength into AI scoring: it uses only witnessed positive-bleed WOUND and coarse witnessed STABILITY/FALL/RISE bands; exact group enemy blood defaults to unknown/full and strength to a neutral estimate. Own active bleed rate remains exact for own treatment decisions. Added `I5_BANDAGE_BLEED`, `I5_WAIT_STABILITY`, `I5_SHOVE_UNSTEADY`, `I5_SHOVE_SPACE`, and witnessed-group patience score terms; the solo 1v1 low-blood patience rule remains unchanged. Regression mutates a visible group target's hidden blood, strength, exact wounds and stability while preserving witnessed history and proves observation and complete scored choices remain byte-for-byte equal. Further tests assert treatment, stability and shove-opening terms are present; v11→v12 migration fills new tuning and preserves an active movement action. Scoped duel/UI/inspection suite passed **552 in 90.40s**; `git diff --check` found no whitespace errors (only repository LF→CRLF advisories). Limitation: these are scoring contracts, not evidence of policy win rate, shove frequency or treatment balance; fixed-seed I5 matchup audit remains next. Wounds are conservatively treated as a witnessed bleeding cue even after seen bandaging because observers cannot know which unseen residual sources remain.

- **I5 balance/release update (Nov 19, 2026).** The AI implementation now includes unsafe-bandage penalties scaled by urgency and one-turn freshness for witnessed instability cues. Final 100-fight-per-case audit: `python -B tools/audit_duel_consequences.py --fights 100 --seed 8100 --output C:/Users/Warp/AppData/Local/Temp/opencode/duel-consequences-100-release2.json`; 700 current-config fights paired with 700 hit-impact-disabled controls. Sampled cases: Punisher, SpamHeavy, Random, Kite, +3-level Punisher, shared long/light Spear, and shared heavy/short Mace. Medians were 30–72 ticks and 14.94–124.8 combined blood loss. No falls/rises. SpamHeavy produced 100 deep head hits and one head-incapacitation per 100 fights; median stagger lock 6 ticks. Random/Ronin: 9 successful shoves (9 actual steps), 4 blocked, 3 resisted and 5 out of range; no repeated displacement loops. Long/light gear seat outcomes 35/65; Mace fixture ended in second-side morale flight 100/100. Impact applied to 418–1,115 hits/case and increased stability loss; the paired impact-off samples kept winner/end-reason counts and median duration unchanged. After urgency-scaled unsafe-treatment penalty, all cases had zero bandage starts/completions. This does not prove treatment is unnecessary: unit scoring proves safe-window selection, while these fixtures show pressured 1v1 fights provide no completed treatment. The impact-off control isolates hit impact, not all §30 work. These synthetic cases do not establish player win rates, general gear/group balance, or broad fairness. Full scoped suite after final AI refinements and native/release gates remain; hands-on user playtesting is still required.

- **I5 release verification (Nov 19, 2026).** After the unsafe-bandage urgency penalty and witnessed-instability freshness changes, the full scoped duel/UI/inspection suite passed **555 in 97.51s**; `python -B -m compileall -q pirates/duel_system tools/audit_duel_consequences.py` and `git diff --check` passed (Git emitted only repository LF→CRLF advisories). v11→v12 active session and world-journal deterministic-resume tests pass. I4 native solo/group captures were already inspected at 1280×800, 960×700 and 640×600; the final changes touch AI/config/audit only, not rendering. This does not substitute for hands-on player feedback, which remains the outstanding acceptance gate.

### 30.7 I4 — Visible impact, reactions and consequential recap
- Give real contact a brief directional impact accent, hit flash and bounded presentation pause without changing simulation timing. Allow reduced-motion/no-shake presentation.
- Animate flinch within the current tile (recoil/pose offset), actual stagger steps between real tiles, and falls as a persistent grounded pose. Do not move a flinch several tiles merely to make it noticeable.
- Keep decisive outcomes visible until acknowledged/next action: `OFF BALANCE`, `PUSHED 1 TILE`, `CONCUSSED`, `GROUNDED`, `BLEEDING`, with affected actor attribution. Priority prevents simultaneous popups obscuring all fighters.
- Latest exchange becomes a small consequence ledger: actual blood lost (including bleed), stability lost/gained, forced travel, important injury/interrupt and resource cost. Green/red are relative to the player; neutral positioning/recovery is not automatically called bad.
- Rename the current scalar verdict to **Damage trade**. Add separate `Control gained/lost`, `Injury`, and `Recovery` statements rather than pretending blood, permanent injury and one tile of space share a validated numeric score.
- Acceptance: native 1280×800, 960×700 and 640×600 captures/playback show who hit whom, physical response, persistent consequence and next-action limitation without opening Details. Human playtest feedback is required for hit-feel acceptance; screenshots alone are insufficient.

### 30.8 I5 — AI, tuning and release gate
- NPC decision scoring must respond to the same visible constraints: stagger risk, approaching fall, bleeding pressure, opportunity to shove into available space, and need to stabilize/treat/escape. No omniscient reading of unseen actors.
- Add deterministic replay, simultaneous impacts, obstruction, cooldown, multi-actor pressure, save migration and paused-active-reaction coverage. Existing journal/world isolation remains mandatory.
- Measure shove attempts/resistance/steps/falls, head-hit outcomes, wound/bleed rates, treatment use, time spent unable to act, and fight duration across representative stat/gear/AI matchups; compare baseline versus expansion at fixed seeds.
- Keep tuning centralized and document the intended ordinary, resistant and extreme fixtures. Reject spam-shove dominance, indefinite stun-lock, trivial universal head-shot wins and cosmetic reactions without matching state.
- Release order: I0 audit → I1 shared shove/impulse → I2 hit integration → I3 bleeding → I4 playback/recap → I5 AI and balance. Each mechanics phase ships with its own minimal causal feedback and regression tests; polished feedback is not deferred until the end.
- Completion requires mechanical before/after evidence, readable native playback, scoped regressions, migration proof and user playtesting. This plan does not promise medical realism or authorize unrelated campaign combat/crew integration.

## 31. User-playtest repair plan — readable, decisive novice sword fighting

### 31.1 Status, scope and authority
- **Planning only; implementation is not authorized by this section until the user reviews it.** The user rejected the current playability result on Nov 19, 2026. Earlier test/native-capture passes remain evidence of their specific contracts, not acceptance of combat feel, movement usefulness or decision clarity. Do not close §30 on those passes.
- Current balancing target is **two level-1 novices, no armor, swords, ordinary starting resources**. Establish and record their exact baseline attributes, weapon, arena, timing and personality before measuring changes. Stronger/weaker fixtures vary capability within this target, not progression levels. Armored fights, high-level stats, larger stamina pools, other weapon metas and group balance are deferred—not reasons to dilute this target.
- Preserve deterministic resolution, one voluntary action per fighter per exchange, collision safety, player/NPC mechanical symmetry and save/journal isolation. Retain old compatibility/regression coverage without treating old synthetic policy win rates as tuning targets.
- This section supersedes §30's player-facing **Damage trade/net-damage verdict** requirement. Replace that presentation with bodily consequences and both commitments. Internal contact/armor/severity calculations may remain where needed; do not merely rename an HP/damage scoreboard or discard causal calculations blindly.
- Goal: cautious spacing, reading a commitment and exploiting an opening matter. A clean serious hit can decisively impair or end a fight; a superficial nick is not an automatic knockout. Avoid both prolonged consequence-free trading and universal one-hit/head-target victory.

### 31.2 Reproduce the reported failures before choosing fixes
- Reports: an enemy attack highlights its entire surrounding ring; tactics are buried/unexplained; guarding still produces an unexplained large damage number; latest exchange sometimes omits the enemy choice; bleeding and anatomical hits feel minor; hard hits lack recoil; diagonal/lateral retreat still gets hit even when apparently early; NPCs stand and trade instead of maneuvering.
- Audit the **actual launch path the user plays**, primary action layout, guide-off mode and loaded tuning. The code has multiple duel/presentation paths; a tactic appearing in one preview does not prove it is accessible in the played screen.
- For each report capture initial state, both actions, committed target/direction, per-tick positions, movement deadlines, strike deadline, reach/geometry check, defense phase, wound, reaction eligibility and resulting state. Record what the screen implied versus what the resolver did.
- Classify movement failures separately: move not committed/blocked; movement completes too late; destination still in reach; target follows or attack retargets; collision/path failure; stale playback; attack geometry error. Do not dismiss every case as same-tick ordering or globally accelerate movement without evidence.
- Deliverable: compact reproducible fixture table and baseline traces for these novice sword conditions, including the failed Guard, Disengage, lateral/diagonal dodge and hard-head/torso-hit experiences. Record unresolved assumptions rather than fabricated reproduction claims.

### 31.3 R1 — Directional attacks and causally useful movement
- Separate **possible weapon reach** from **this committed attack's threatened space**. Do not paint a 360-degree ring as an active strike. Show attacker, target/line or bounded swing sector, and windup/impact/recovery clearly. A single-target strike must not look like an area attack.
- Specify the tracking contract before coding: when target/direction is acquired, how much turning/tracking is allowed during windup, when direction locks, and what happens if the target moves laterally, diagonally, behind or beyond reach. Choose readable geometry appropriate to a committed sword attack; no unexplained homing/instant 180-degree correction. Use the same geometry for telegraph, preview and actual hit test.
- Successful movement is not automatic invulnerability. A move that finishes **before** impact and leaves the committed strike's valid geometry must evade it; a move staying inside that geometry may still be hit. Exact same-tick ordering remains explicit and consistent for both sides. If a revised ordering is needed, document and test it rather than silently changing fairness.
- Disengage is a spacing action with a real first-step deadline, not an invulnerability button. Show whether that step precedes the observed strike and whether the destination actually escapes its threat. Explain failures plainly: “hit before your step,” “still in sword reach,” or “route blocked.”
- Cover left/right/diagonal/backward movement, movement behind the attacker, different approach angles, edge/body obstruction, late moves, equal-tick moves, and moving strikes. Verify actual tile positions and hit results, not animation alone. Moving outside the attack before impact must differ from standing still in a matching fixture.
- Acceptance: guide-off native play shows a directional threat, a visibly early sidestep can avoid an appropriately committed attack, a late/insufficient step can fail with a truthful reason, and no new overlap, teleportation or free extra action occurs.

### 31.4 R2 — Discoverable tactics and guide-independent action-risk colors
- Put Feint, Shove, Disengage, Thrust and Disarm directly in the ordinary action interface, alongside core attacks/defense/movement. Disabled contextual actions remain visible with their requirement. Do not require opening Tactics or reading a stat sheet to discover them; compact layouts must retain access without hiding them in an unexplained tab.
- Each action needs a short purpose and current-context reason: **Feint** draws/disrupts a committed defense but can be ignored/punished; **Shove** creates control/space through a resisted contest; **Disengage** creates distance if its step is early enough; **Thrust** offers a specified sword attack tradeoff/opening, not a mysterious duplicate Light; **Disarm** requires its actual opening/grip conditions, not a universal weapon-delete button. Audit whether the existing mechanics actually deliver each purpose before retaining/revising them.
- Add always-on action assessments independent of the optional guide: **green = favorable opportunity**, **amber = conditional/uncertain**, **red = exposed/poor fit**, **gray = unavailable**. Include a short text reason/icon so meaning does not depend on color vision. Green is not a guarantee and red is not an illegal action.
- Assess timing, distance/committed threat, route, own stamina/fatigue/injury, observed opponent capability and relevant tactical prerequisites. Use only player-observable evidence; do not inspect unchosen enemy actions, hidden exact stats, private personality weights or future RNG. Unknown opponent capability must remain qualified, not confidently green.
- Explicitly evaluate Guard against an observed stronger opponent: can it raise in time, withstand pressure, cover the aimed area and pay its cost? Warn when it is likely to break or be bypassed; guarding versus guarding is neutral, not automatically favorable. Movement advice must use R1's actual escape geometry/timing.
- Provide a short hover/focus explanation and a persistent selected-action reason. No heavy rollout search on every render. Define uncertainty handling and test purity: inspecting actions must not advance ticks, alter state or consume RNG.
- Acceptance: with guide off, a novice can find every tactic, tell why a weak guard or late retreat is risky, and identify a conditional attack/recovery opening without interpreting raw stat tables. Verify at 1280×800, 960×700 and 640×600.

### 31.5 R3 — Consequence recap instead of damage arithmetic
- Remove player-facing net-hit/net-trade numbers, damage verdicts and dealt/taken scoreboard from the latest exchange, popups and ordinary combat log. Audit other player-visible surfaces so old labels do not survive elsewhere. Detailed diagnostics may retain clearly internal impact calculations, not present them as health damage.
- Always reserve visible recap space for **You chose … / Opponent chose …**, including aim and movement where observed. Preserve the choice even when the action completes or is interrupted; use the observed commitment active at the exchange boundary if ACTION_START occurred earlier. In unseen/group situations state “unseen/unknown” rather than inventing a choice.
- Then state physical results for both fighters: wound location/severity, pain change, fatigue/exertion change, bleeding started/continuing/treated, impaired function, interrupted action, concussion/stagger/fall/recoil, and actual displacement. Separate effort costs from injury costs.
- Explain effective capability changes in plain language, e.g. “pain and blood loss slowed your next step / weakened your guard,” with actual before/after effective speed/strength/accuracy where known. No hardcoded example amounts presented as measured facts. Do not expose exact private NPC pools in group mode.
- Defense failures must say why: guard not raised, uncovered target, strength/stamina break, or timing. “31 net damage” is not an explanation. A torso injury should name the injury, pain/breathing/function and bleeding consequences actually implemented—not only an abstract amount.
- Acceptance: without Details, player sees both choices, what bodily harm occurred, why defense/movement failed and what is now harder to do. Effects must match state/events and stay readable without scrolling away the commitments.

### 31.6 R4 — Wound severity, sustained bleeding and progressive impairment
- Audit existing wound, blood, pain, fatigue and effective-stat formulas first. Blood already contributes to effective-stat penalties; verify when/how strongly this happens before adding a second penalty. No double-counting or arbitrary simultaneous subtraction from every resource.
- Superficial graze/nick: small **one-time fractional blood loss**, local pain as appropriate, no indefinite active bleeding. Serious open cut: source-attributed ongoing loss until effective treatment/healing; deeper injury may also immediately impair function. Wound severity/contact/location determine the distinction, not every hit applying the same bleed timer.
- Centralize severity thresholds, initial loss, ongoing rates, treatment effect and severe-blood-loss impairment. Significant cumulative blood loss progressively reduces effective strength, speed, accuracy and recovery capacity as specified; show the resulting weakness/slowness. Slight blood loss must not trigger severe systemic collapse. Retain sensible floors/collapse handling; do not replenish blood when merely bandaged.
- Bandaging controls bleeding rather than instantly restoring blood, concussion, severed tissue or full function. Make treatment time, interruption risk, source treated and residual bleeding explicit. Decide and document whether adequate bandaging stops an ordinary serious cut or leaves reduced residual loss; do not leave the user believing bleeding has stopped when it has not.
- Any spontaneous healing/clotting requires an explicit scoped rule and elapsed simulation time; no render-frame effects or unexplained endless graze drain. Preserve wound/source state and active bleed across saves with migration tests if the representation changes.
- Compare equal-tick no-wound/graze/moderate/deep/multiple-wound fixtures, exertion, bandaging and pause/resume. Demonstrate fractional one-off graze loss, persistent untreated severe bleed, reduced/stopped treated loss, and a measured capability decline as serious loss accumulates. Do not tune merely to force a long bleed-out animation; immediate serious injury should already alter the fight.

### 31.7 R5 — Anatomical consequences, meaningful impact and survival behavior
- Define a small severity-by-location/contact consequence table for this sword baseline. Distinguish superficial head/face contact from deeper head trauma: a nose/ear nick need not mean concussion; a qualifying hard head hit can cause disorientation, concussion, interruption, stagger, recoil, fall or incapacitation according to causal thresholds. Use deterministic descriptive variation only if supported; do not claim precise nose/ear simulation when the model only stores HEAD.
- Torso injuries must communicate relevant pain/functional limitation and sustained bleed where warranted; arm/hand injury affects force/grip and weapon use; leg injury affects movement/balance; eye injury affects vision/aim. Avoid identical generic blood/stat penalties across all targets and avoid duplicating existing impairment.
- Reproduce why a hard hit currently causes no physical response: inspect impact, shock, composure, stagger/can-act gates, cooldown, grounded state, free destination and existing reaction ordering. A disabling hit must not lose its reaction merely because it made the target unable to act. Separate involuntary reactions from voluntary action eligibility.
- Distinguish **flinch** (visible in-place motion), **recoil** (actual legal backward displacement), **stagger** (temporary loss of control), and **fall** (persistent grounded limitation). Strong qualifying contact should produce a meaningful response, but blocked recoil must become a visible braced/obstructed consequence—not teleport through a wall or disappear. Do not force backward travel on every cut or chain-lock the target indefinitely.
- NPCs should maneuver before injury when visible threat/capability makes standing trades dangerous: create distance, sidestep a committed attack, circle for an opening, disengage or reposition for treatment. A head-on personality can accept more risk; ordinary/cautious novices should not all stand still because attack scores dominate. Do not force movement that is late, blocked or stays inside the same threat simply to inflate movement counts.
- NPC survival response should rise with its own severe pain, dangerous blood loss, disabling injury and observed incoming threat. Prioritize a feasible escape/recovery/treatment plan over repeated attacks or repeated interrupted bandages. Hiding is only valid if arena geometry/visibility supports breaking contact; otherwise reposition/retreat/flee, not a fictitious hide action.
- **Player control:** no involuntary morale-driven fleeing or automatic tactical retreat for the controlled fighter. Physical interruption, collapse, unconsciousness or death remain possible. NPC flight/recovery decisions may vary by personality but cannot ignore catastrophic incapacity. Audit every fear-break/flight route for this distinction.
- Acceptance: compare aggressive versus cautious novice policies at identical capabilities/seeds; observe purposeful, successful maneuvering against a committed threat/stronger novice, not just movement attempts. Show serious-hit before/after behavior, actual recoil or obstruction response, survival decisions and one-action fairness. Record actor-separated choices, time-to-decisive-impairment, fight duration, movement success, serious-hit reactions and treatment outcomes. No universal optimal-action or medical-realism claim.

### 31.8 Execution checkpoints and final acceptance
1. **R0 audit/spec:** reproduce §31.2; settle attack tracking/lock geometry and novice wound/consequence table; obtain user approval of this plan before implementation. Record any remaining design decisions explicitly.
2. **R1 movement/telegraphs:** implement/test directional geometry and movement-before-impact contracts together. Native capture plus per-tick trace must agree; only then use these contracts in action advice.
3. **R2/R3 usability:** expose tactics, add public-information risk assessments, replace damage recap and preserve both choices. Verify guide-off mode in the actual launch path at all three sizes.
4. **R4/R5 consequences/AI:** integrate graze versus sustained bleed, progressive weakness, anatomical impact/recoil and NPC maneuver/survival choices; keep player flight manual. Ship each change with its causal feedback and tests, not mechanics first with UI indefinitely deferred.
5. **Release:** rerun scoped regression/purity, deterministic replay, save migrations and native playback. Measure the level-1 unarmored sword baseline with fixed seeds and role swaps; include early/late movement, weak guard, hard impact and treatment windows. Existing tests that encode superseded presentation/bleed behavior must be deliberately updated with new behavioral assertions, not deleted to turn the suite green.
6. **Renewed hands-on playtest:** user can explain both actions and consequences, find a tactic, evade a committed attack with sufficiently early correct movement, recognize an unsafe guard/retreat, and feel a clean serious hit change the fight. NPCs visibly maneuver and react. A new passing test count alone cannot satisfy this gate.
- Keep a checkpoint evidence table here during approved implementation: issue/fixture, before, change, after, test/native evidence, remaining limitation. Do not mark R0–R5 implemented in this planning pass.

### 31.9 User refinement — fixed Accuracy and anatomical injury catalogue
- **Supersedes references above to injury, blood loss, pain, fatigue or fear reducing Accuracy.** Accuracy is a permanent authored skill stat: during a fight its effective value equals its base value. No modifiers from anger/adrenaline, exhaustion, level scaling, wounds, eye loss, nervousness, blood, pain, fatigue or temporary statuses. This planning pass does not authorize progression changes; current target remains level 1.
- Separate immutable Accuracy from geometry, target size/exposure, visibility and legal actions. A small/covered target can still be harder to contact; an absent or unseen target cannot be aimed at merely because Accuracy is high. Do not secretly recreate temporary Accuracy penalties as an equivalent injury-driven aim-roll penalty. Audit target checks, previews, AI, status text and diagnostics for consistency. Speed, strength, grip, balance, perception and action availability can change where explicitly justified; Accuracy cannot.
- Eye injury may restrict sight/target acquisition, not lower Accuracy. Concussion can stagger, slow or prevent actions, not lower Accuracy. Blood loss weakens/slows, not lowers Accuracy. Audit existing level/fear/anger/pain/fatigue/status/eye multipliers and their tests before implementing this deliberate rule change.
- Injury model must distinguish **broad aimed zone → actual anatomical site → tissue/structure → severity → functional consequence**, with source, side where relevant, bleeding/treatment state and permanent loss. Preserve high-level targeting shortcuts; do not require the novice to navigate a huge body-part menu for every swing. An advanced precise target can require sufficient Accuracy/exposure. Record the actual site after resolution; do not randomly rename the same generic HEAD wound.
- Proposed minimum catalogue (design options, not claims of medical simulation or existing implementation):

| Region | Actual sites | Example injury range | Relevant consequences |
| --- | --- | --- | --- |
| Head/face | scalp, forehead, skull, cheek, jaw, nose, left/right ear | graze, open laceration, deep cut, fractured nose/jaw/skull, partial/complete external ear or nose loss | pain, local bleeding, disorientation/hearing limitation where supported; qualifying skull trauma can cause concussion, loss of control or unconsciousness |
| Eyes | left/right eye, eyelid/orbit | lid nick, orbital cut, damaged eye, destroyed/lost eye | bleeding, sight obstruction/loss and target-acquisition restrictions; no Accuracy modification |
| Neck | front/side/back neck; superficial tissue versus major vessel/airway/spine involvement | superficial cut, deep tissue wound, critical vessel/airway injury, catastrophic spinal injury or severance | severity-specific bleeding, breathing/control failure, collapse or fatal outcome; not every neck contact reaches a vital structure |
| Torso | shoulder girdle, chest/ribs, abdomen, flank, back | skin cut, muscle laceration, rib injury, deep cavity injury with explicitly represented lung/organ involvement | pain, weaker force, restricted breathing/exertion, severe external/internal bleeding if implemented; no abstract damage-only result |
| Arms | left/right shoulder, upper arm, elbow, forearm, wrist | graze, muscle/tendon cut, fracture, major-vessel cut, disabled joint, partial/complete arm/forearm loss | reduced usable force/control, compromised guard, bleeding, unusable limb when structurally disabled |
| Hands | left/right palm, fingers, thumb, gripping structures | nick, deep palm/tendon cut, finger/thumb loss, severed hand | grip failure, dropped weapon, inability to perform actions requiring that hand; no automatic regrowth or instant cure |
| Legs | left/right hip, thigh, knee, shin/calf, ankle | graze, muscle/tendon cut, fracture, major-vessel cut, disabled joint, partial/complete leg loss | slower or unavailable steps, unstable stance, fall, bleeding; movement constraints reflect actual usable limbs |
| Feet | left/right foot, heel, toes | cut, tendon damage, toe loss, crushed/fractured foot, partial/complete foot loss | painful/limited footing, impaired push-off/balance and possible inability to stand or move normally |

- Each catalogue entry must map to an implemented structure/severity rule and visible functional consequence, not flavor text alone. Derive outcome from contact type, delivered post-defense impact, angle/exposure, actual site and tissue thresholds. Seeded variation may choose among physically eligible nearby sites, not grant arbitrary decapitation on a weak graze.
- **Severance is exceptional structural failure**, gated by appropriate cutting contact, anatomical site and sufficient force/depth. Include digit/ear/nose/hand/limb loss and catastrophic neck severance where justified; no universal amputation chance on every attack. Partial versus complete loss must have defined meaning or remain explicitly deferred, not cosmetic claims. Serious non-severing wounds must still be consequential.
- Track missing/destroyed parts persistently. Cannot hit an already absent part, grip with a missing hand, or stand normally with mechanically unusable legs. Define dominant hand and usable supporting limbs; do not silently assume two-handed actions remain available. Bandage controls treatable bleeding, not missing parts, organ damage or fracture.
- Neck/internal-organ/vessel outcomes require a deliberate minimal physiological representation if included: do not display organ rupture/internal bleeding without ongoing state and consequences. Exact medical probabilities are out of scope; verify coherent gameplay thresholds and state/event truthfulness.
- Acceptance: deterministic superficial/serious/catastrophic fixtures across the regions, sided eye/hand/leg loss, treatment versus irreversible injury, invalid absent-part targeting, action restrictions, save migration/resume and equal player/NPC effects. Across pain/fatigue/blood/fear/anger/status/eye-injury fixtures, Accuracy must remain exactly unchanged while separately specified functional constraints change.

### 31.10 Conditional counterplay and resource contract
- The action system is **not a fixed win-cycle**. An action counters a particular commitment only if timing, geometry, capability and resource conditions allow it. Public action colors must use the same rules and say which condition is missing. No “Guard always beats Light” or “Disengage always beats Heavy.”
- Before coding, audit and record exact existing centralized base costs, scaled windup/first-step/impact/recovery deadlines, per-tick drains and fatigue debts for the novice sword fixture. Then propose any tuning delta with a reason and paired evidence. Do not invent numeric costs in this planning document; the implemented UI must show actual configured costs and deadlines before commitment.

| Action | Intended use / conditional counter | Loses value or is punished when | Depends on / costs |
| --- | --- | --- | --- |
| Light cut | interrupt an exposed slow windup; punish a small recovery window | outside locked geometry/reach, a ready adequate guard/parry, or too late to interrupt | fixed Accuracy for precise contact; speed sets timing; strength/contact sets injury; upfront stamina, exertion fatigue, short recovery |
| Heavy cut | exploit a long opening; overwhelm a guard only with sufficient force | early sidestep/retreat, faster interrupt, timed adequate parry, or whiff punish | longer commitment, more stamina/fatigue and recovery; balance/overextension risk; no guaranteed guard break |
| Thrust | a distinct direct-line attack into an exposed opening; settle sword reach/speed/precision tradeoff in R0 | lateral escape from its locked line, adequate deflection, or close pressure during recovery | sword/contact legality, narrow geometry, target exposure and fixed Accuracy; explicit windup/recovery and stamina/fatigue; must not be Light with a different name |
| Guard / timed parry | defend a covered line; a correctly timed capable parry creates a riposte window | guard too late, force/stamina insufficient, uncovered line, feint disruption, or unfavorable shove contest | raise deadline, coverage, strength/grip and stamina; holding/contact drain; parry window; no damage immunity |
| Feint | induce/disrupt committed defense and create an actionable opening | opponent ignores it, attacks before setup finishes, or leaves its effective range | real commitment and stamina/fatigue; specify disruption duration and follow-up opportunity without giving a free second action |
| Shove | interrupt/control at close distance, pressure balance or create escape space | out of range, early attack/escape, strong brace, poor footing or obstructed travel | strength/balance/stance contest, active guard brace, legal free tiles; stamina/fatigue and recovery; blocked movement can still transfer bounded impact |
| Disengage | deliberate backward spacing before impact; make a committed attack fall short | incoming strike lands first, destination remains threatened, route blocked, or follow-up chase closes space | first-step deadline versus impact, reach/lock geometry, clear route and own usable legs; stamina/fatigue; no invulnerability |
| Step / sidestep / circle | change angle, leave a locked strike, establish an opening | step too late, attack still tracks, destination stays in swing sector, or another obstruction/threat covers it | speed/first-step timing, route, footing, threat geometry; movement cost/fatigue; cannot add an attack to the same voluntary action |
| Dash / Run | cross space faster or escape a larger threatened area | path interception, extended exposure/recovery, obstruction or expensive overcommitment | explicit per-tile deadlines, route/collision, stamina/exertion/balance; distinguish from ordinary Step |
| Dash+Light | close and attack as one authored committed combination | defender escapes its actual endpoint/reach, interrupts approach, guards or punishes recovery | full path plus strike timing, reach and contact; configured combined cost and recovery; not an extra free action |
| Riposte / Disarm | exploit a parry-earned opening; attack exposed grip/hand to remove weapon | opening expires, hand unavailable/unexposed, failed contact/grip contest or target moves away | earned window, fixed Accuracy, reach and force/grip; stamina/fatigue and recovery; losing a hand can separately force a drop |
| Wait / recover | regain control/resources while safely separated or opponent remains committed | opponent can reach and strike during the wait | real elapsed ticks; specify actual stamina/stability recovery and fatigue effects; bleeding continues |
| Bandage | control an active treatable bleeding source in a sufficient safe window | enemy can interrupt before completion, route to safety not established, or source is not treatable | inventory, usable hands, duration, source selection, any actual stamina cost; no blood refill; serious injury persists |
| Rise / grounded cover / crawl | regain stance or survive/reposition after falling | rising into an arriving strike; crawling into threat/obstruction; inadequate cover | grounded state, usable limbs, timed exposure, clear path and actual resource costs |
| Adrenaline | once-per-fight emergency pain suppression if retained in the novice baseline | treated as healing, used without an opening, or debt leaves fighter exposed | trigger/use gate, activation time, duration and fatigue debt; never modifies Accuracy or repairs injuries |

- For each matchup in the table, specify first-action timing and the **next decision opportunity**. Feint→attack and parry→riposte need reachable windows under one-action-per-exchange scheduling; if the opponent resets freely before a follow-up, the claimed counter is not implemented.
- Make resource meaning explicit: stamina is immediate exertion capacity; fatigue is accumulating effort debt affecting physical capability; pain/injury affect defined physical functions; blood is circulating reserve with severity-dependent systemic impairment. Do not turn all four into duplicate HP bars. Accuracy remains fixed. No action consumes blood as an arbitrary fee—wounds/exertion drive any actual bleeding.
- Acceptance: a fixture matrix proves at least one working use, one valid counter and one failed-precondition explanation for every retained novice action. Include strong-opponent Guard, early/late Disengage, lateral evasion of a committed thrust, feint follow-up viability, resisted/blocked Shove, successful/failed Disarm and safe/interrupted treatment. Guide-off colors/reasons must agree with observed rules without guaranteeing hidden simultaneous choices.

### 31.11 User refinement — unified Defend and distinct offensive purposes
- **Supersedes separate primary Guard/Parry/Disengage controls in §31.4/31.10.** Present one **Defend** action. Guard, timed parry, sidestep and backward disengage become its underlying responses, not several buttons a novice must evaluate independently. Separate voluntary Move/Dash remains available for deliberate positioning.
- Defend chooses a feasible response from **currently observable** threat, own capability, timing, clear destinations and costs: parry/block a defendable line when timely and sustainable; evade/retreat from force likely to overwhelm defense if a step can escape before impact. It must consider secondary visible threats and arena edges, not blindly step backward into danger.
- “Does what is best” means the best supported defensive estimate, not guaranteed optimal play or immunity. Define response priorities, tie-breaks and uncertainty explicitly in R0; assess all feasible responses rather than hardcoding Heavy→retreat and Light→guard. An early well-timed block can beat an impossible late dodge; a strong light strike can still overwhelm weak defense. If no safe response exists, show **Defend: exposed — no safe step / too late / guard likely to break**, while allowing an explicitly described best-effort response.
- Preview **Defend → parry / brace / sidestep left / retreat**, reason, destination where relevant, actual stamina/fatigue and timing before committing. Selection cannot see an enemy's unchosen same-tick action or change retrospectively after reading it. Commit one response using the shared decision snapshot; later adaptive switching is deferred unless explicitly specified and budgeted as a single action with honest reaction delays. No free guard+parry+dodge combination.
- Here “flee a big attack” means a local evasive step/retreat, not automatically exit the encounter or surrender. Pressing Defend authorizes this local movement. Player encounter-level flight remains explicit; uncontrolled fear must not choose it. NPCs may use survival flight independently.
- Explain offense plainly: **Feint is deception/setup, Defend is protection.** Feint tries to draw/disrupt defensive commitment and create a real subsequent opening; it does not protect the feinter or damage the target by itself. If the scheduling cannot support a usable opening, redesign it before claiming it counters defense.
- **Light = quick cut:** small commitment to exploit a brief opening or interrupt a slower exposed attack; not guaranteed harmless. **Heavy = committed forceful cut:** larger exertion/windup/recovery and greater delivered-force potential, vulnerable to evasion/interrupt. **Thrust = direct point attack:** narrow line and distinct contact/geometry tradeoff, vulnerable to lateral escape/deflection. Specify actual sword-baseline reach/timing/contact differences before implementation; do not invent extra thrust reach or guarantee penetration. If no meaningful supported distinction survives, merge redundant attacks rather than leave cosmetic buttons.
- **Disarm = opportunistic weapon control**, useful when a parry creates an exposed grip/hand opportunity and the fighter can exploit it with sufficient precision, timing and force. It can neutralize an otherwise dangerous armed opponent without needing a torso/head finishing hit, but must not be an always-better attack. Show availability and reason; if follow-up opening is impossible under exchange scheduling, repair that contract. Structural hand injury may separately force weapon loss without a Disarm action.
- **Shove needs a functional rework, not just stronger popup text.** Reproduce the novice strength/stance fixture where no displacement/fall is observed; measure contest margins, movement thresholds, impact, space and NPC selection. Define ordinary close shove as a credible attempt to create space, not routinely a cosmetic strain because only extreme attributes pass the displacement threshold. Preserve a meaningful brace/resist counter and weaker-shover failure.
- Candidate shove outcomes: resisted/strained; one legal step with control loss; greater displacement only for sufficient impulse or an already moving/unbalanced target; fall when stability/footing fails. A novice's ordinary shove need not launch an equal opponent several tiles. Repeated pressure or an injured/unsteady target should make a fall materially achievable, not require contrived level/strength extremes. Obstructions stop travel but do not erase connected impact; no chain-pushing bodies, overlap or indefinite stun.
- Shove acceptance must include level-1 armorless equal/advantaged/disadvantaged novices, idle versus braced versus recovering/moving/unsteady targets, clear one/two-step space, wall/body blockage, pressure-induced fall and counterplay. Report actual displacement/falls by actor and opportunity, not aggregate attempt count. Demonstrate that gaining space creates a usable movement/treatment/tactical opportunity without granting a free extra action or dominant shove loop.
- Revise the counter matrix and native controls around **Light / Heavy / Thrust / Feint / Shove / contextual Disarm / Defend / Move / recovery**. R0 must finalize Defend's observable-information selector, response commitment and cost rules before code. Add selector tests for defendable hit, overwhelming hit with viable escape, blocked escape, late input, unknown commitment, multiple visible threats, deterministic ties, state/RNG purity and no extra action.

### 31.12 User refinement — readable opponent state and conditional tactical intent
- **Action colors alone are not strategic guidance.** They assess an option under stated conditions; they cannot tell the player which hidden simultaneous choice will occur. Do not implement a green-button-following autopilot or imply that all green actions are equally appropriate.
- Add a compact, always-visible **opponent read** independent of the optional guide, using witnessed posture/actions/injuries and earned capability evidence. Distinguish:
  - **Now:** visible commitment/phase, current distance and movement, guarding/exposed/recovering/staggered/grounded, weapon/grip and overt injury/bleeding cues.
  - **Opportunity:** a *known* remaining recovery or loss-of-control window, whether closing/striking fits inside it, and whether that line is reachable. Use qualitative/estimated timing if exact enemy timing is not legitimately observable. Do not call an idle opponent exposed for an entire Heavy simply because no action is currently displayed.
  - **Condition:** observed signs such as labored movement, compromised weapon arm, unsteady footing or ongoing bleeding; label estimates/uncertainty and stale information. Do not substitute hidden raw enemy stamina, pain, blood or personality values for evidence.
  - **Read/history:** short witnessed tendencies, e.g. repeated forward pressure or retreat after a miss, with evidence count/confidence where useful. A tendency is not a prediction of the next choice. Unknown opponent stays unknown until there is evidence.
- Separate **guaranteed observed facts**, **conditional opportunities**, and **speculative predictions** visually/textually. Show when the enemy can choose freely: “Ready — next action unknown.” Avoid treating an uncommitted pose as a promised opening.
- Add a short **tactical intent** layer: “punish recovery,” “probe without a long commitment,” “close distance,” “create space,” or “stop bleeding.” For a selected action state **why now / requires / main counter-risk**. Do not merely list every action as potentially useful.
- Example reasoning contracts (illustrative, not guaranteed recommendations):
  - Known long recovery, already in range: Heavy is a candidate **only if its strike fits**; Light offers less commitment; Thrust depends on an exposed valid line. If the window expires first, mark that explicitly.
  - Opponent ready with next action unknown: no guaranteed punish. Light may probe with less commitment; Heavy is a read/risk; Feint depends on drawing defense. Describe uncertainty rather than mark Heavy safe just because it can deal more injury.
  - Out of range: stationary attacks can whiff; Dash+Light must reach and strike before an observed opening closes, otherwise it is a risky approach. Ordinary Move can establish spacing without the attack commitment.
  - Visible overwhelming incoming strike: Defend evaluates an actually feasible escape; heavy retaliation is a risky trade unless it can interrupt first. If neither defense nor escape is timely, say so.
  - Enemy visibly bleeding but still armed/ready: creating safe distance may exploit that condition; do not recommend automatic waiting while they can close. Own serious bleed may instead make establishing a treatment window urgent.
- Colors remain a secondary summary of **risk/opportunity**, not a ranked win-probability claim. “Conditional — good if they keep guarding; loses to an immediate attack” is more useful than unexplained green. No invented percentages without a validated model/evidence.
- Keep the opponent read visible during action selection and both choices visible after resolution. Clearly indicate what changed from the previous exchange and why the earlier opportunity has expired. Make inspection available without opening stat sheets or enabling guide/coach.
- Acceptance: guide-off users can distinguish a recovering opponent from a ready one, explain why Heavy fits one window but not another, recognize Dash+Light approach risk, and identify a spacing/treatment intent without following colors blindly. Tests cover idle versus known commitment, expiring/stale cues, unseen/private-state invariance, fixed-Accuracy semantics, timing/geometry agreement with R1 and pure/non-RNG-consuming presentation. Native capture and renewed playtest must validate actual comprehension, not only presence of colored controls.

### 31.13 Concrete novice mechanism decision — first implementation target
- **Design specification, not current runtime measurements.** These are the requested numeric first-pass targets for approval and later centralized tuning, superseding earlier instructions to leave these distinctions undecided. No gameplay implementation in this planning pass. Validate behavior before adjusting numbers; do not claim these values are medically calibrated or balanced.
- Baseline fixture: level 1, no armor, one ordinary sword, Strength 10, Speed 10, fixed Accuracy 10, stamina capacity 100, fatigue/pain 0. One simulation tick is a timing unit, not a promised real-world fraction of a second. Tile is an abstract footwork interval. All timings below are offsets from commitment at t0; own physical speed scales durations with ceil(base × 10 / effective Speed). Accuracy does not scale or change. Costs are charged once on commitment, plus explicitly listed holding/contact costs; interrupted/whiffed actions do not refund effort.

| Mechanism | Impact / first effective time | End / next readiness | Stamina | Fatigue added | Contact / purpose |
| --- | --- | --- | --- | --- | --- |
| Light cut | t3 | t6 | 6 | 2 | cutting; force = effective Strength × 0.8; quick opening/interrupt tool |
| Heavy cut | t7 | t14 | 14 | 6 | cutting; force = effective Strength × 1.6; strong committed contact, long punishable recovery |
| Thrust | t4 | t9 | 8 | 3 | piercing; force = effective Strength × 1.0; precise direct line, distinct wound/contact and escape geometry |
| Defend → parry/brace | blade ready t2; parry window t2–t4 inclusive | missed parry recovers through t6; a successful parry releases defender to next decision at contact +1 tick | 4 setup, +4 on successful parry; brace costs below | 1 setup, +1 on contact | redirect an appropriate line, or hold a sustainable covered line |
| Defend → evade/disengage | one step lands t3 | t6 | 5 | 2 | actual legal side/backward step, no attack or protective guard bundled in |

- Force is an **internal contact/defense quantity**, not HP removed or a player-facing damage score. Wound depth/site/contact determines pain, structural loss, bleed and control consequences. Thrust is not universally safer/weaker than Heavy: a deep piercing torso wound can be decisive without a large force number. R4 anatomical thresholds must demonstrate the intended outcomes rather than interpreting force as direct blood subtraction.
- **Attack geometry decision:** stationary novice sword attacks have one-tile Chebyshev reach; Thrust does not get free extra reach or an invisible movement step. Light/Heavy threaten the three adjacent forward cells around the committed octant; Thrust threatens only the one adjacent cell on its committed octant. These are potential single-target contact cells, not multi-target cleaves. The selected target alone can receive contact. Aim zone selects anatomy, not a different map direction.
- Direction tracks the visible selected target only during early preparation: Light locks after t1, Heavy after t2, Thrust after t1 (scaled using the same speed rule). At lock, choose direction from actual current target position; if target is unseen use the last legitimately observed bearing, not hidden position. After lock no homing/rotation to the target. At impact require the target's actual position inside the locked geometry and reach. Telegraph shows tracking preparation distinctly, then locked threatened cells; do not paint all surrounding tiles.
- Tick order is explicit: resolve eligible same-tick strikes using the same snapshot before that tick's voluntary movement, then voluntary movement/forced-motion according to the established collision pipeline. A t3 sidestep cannot evade a t3 strike; a t3 step can evade a t4/t7 strike if it leaves the geometry. Lock updates use positions from preceding completed ticks; list the exact lock event in traces. Lock/impact ties must not depend on actor iteration order.
- **Parry versus brace:** parry is a short timing-dependent redirection, not a held immunity state. Initial force capacity = effective Strength × 1.4 (14 in baseline), requiring usable sword hand/blade, covered line, sufficient stamina and impact inside the window. A qualifying successful parry prevents that contact, interrupts the attack and leaves attacker weapon recovery for 6 ticks from contact; it does not automatically wound them. Defender can make one next voluntary choice at contact+1; no free counterattack.
- Brace after t4 remains possible as the already committed blade response, but it is not a parry. Initial brace force capacity = effective Strength × 1.2 (12 baseline); cost per held tick = 0.25 stamina, successful held contact costs 4 additional stamina and 1 fatigue. Hold is finite through t8, then lower/recover through t10 unless interrupted/released under an explicit rule. Covered supported contact is blocked; above capacity or insufficient payment breaks defense and passes contact to injury resolution. Do not deduct force capacity from body damage as an HP shield. Existing guard-break/stagger consequences need review against these new deadlines.
- A blade response selected when no commitment is visible is anticipatory defense, not clairvoyance. Against an observed late-arriving strike the selector may time its preparation to the visible deadline, but any wait is part of the committed action, pays holding costs where applicable and is vulnerable to other events; no retrospective re-timing after an unseen choice.
- **Defend selector decision:** (1) enumerate legal blade and clear one-step responses; (2) discard responses arriving too late or leaving the known threat covering the fighter; (3) reject blade defense when public capability evidence indicates likely overload, qualifying it when strength is unknown; (4) among credible safe responses prefer a timely parry retaining an opening, then a safe evade, then sustainable brace; (5) tie-break equal evade routes by greatest clearance from all visible threats, then lowest cost, then stable direction order. If none is credibly safe, advertise best effort/uncertainty, not green protection. All choices commit on the player's decision snapshot without reading unchosen enemy action. Exact hidden capacity cannot feed player advice.

#### Numeric scenario fixtures that define the intended experience
1. **Light interrupts an exposed Heavy:** both adjacent and ready at t0; Light contacts t3, Heavy would contact t7. A qualifying disabling wound/control reaction at t3 cancels unresolved Heavy. A mere superficial nick does **not** magically cancel every Heavy: if interruption criteria fail, Heavy can still land at t7. Light's advantage is early opportunity, not automatic initiative victory.
2. **Same Light against timely parry:** Light lands t3; defender starts Defend at t0, blade ready t2, parry window t2–t4. Force 8 is within capacity 14; supported contact is parried. Defender is ready t4; attacker is unavailable until t9 (contact+6). Light committed at t4 strikes t7, inside the opening; Heavy committed t4 strikes t11, too late to call guaranteed. Disarm must also have a verified strike deadline inside the opening before being advertised.
3. **Heavy overwhelms an equal novice's static brace:** Heavy force 16 exceeds brace 12 and baseline parry capacity 14. Do not color stationary defense safe against this witnessed capability. It may still fail to connect if target moves early, and capacity evidence may initially be uncertain. A stronger novice with Strength 14 has parry capacity 19.6: Heavy is not universally unparryable.
4. **Retreat avoids Heavy but not a late Light:** positions A=(2,2), B=(3,2). A commits eastward Heavy t0, locks t2, strikes t7. B commits Defend retreat t0, steps to (4,2) t3: outside reach at t7, Heavy whiffs. Against Light striking t3 the same first step loses the tie: B is still adjacent for that strike. If B starts retreat at t-1, its step at t2 occurs before t3 and can escape, subject to tracking/reach. Show which case occurred.
5. **Thrust rewards a straight opening and loses to lateral footwork:** A=(2,2), B=(3,2); A thrusts east, locks t1, hits t4. B's early t3 step to (3,3) leaves the narrow line, so the thrust misses. That same destination remains in an east-facing Light/Heavy three-cell sector; for Heavy it is early but **not far enough out of the swing**, so it can still hit. Against Light the t3 movement also loses the strike tie. Sideways movement is useful according to geometry, not universal dodge immunity.
6. **Short versus long punish:** enemy known unable to defend/move for the next 5 ticks, in reach: Light t3 and Thrust t4 can fit; Heavy t7 cannot. With 9 ticks remaining Heavy can fit, at larger cost/recovery. If enemy is merely idle, there is no such guaranteed window. Moving targets can still invalidate the line, and tiny anatomical targets require exposure/fixed Accuracy checks.
7. **Injury changes the physical clock, not Accuracy:** effective Speed falls from 10 to 7 due to a verified serious injury/blood-loss state. Light windup becomes ceil(30/7)=5 ticks, Heavy ceil(70/7)=10, Thrust ceil(40/7)=6, evasive step ceil(30/7)=5. Accuracy stays 10. Preview timing uses the same rounding as the resolver, with named phase boundaries derived consistently rather than mixing scaled total/individual durations.
8. **No escape available:** Heavy arrives t4, own evasive step needs 5 ticks or all escape cells are blocked. Defend must show danger; it cannot teleport or parry force beyond supported capacity simply because the user pressed the combined button. A loss here is understandable rather than a misleading promise of safety.
- Before implementation, specify remaining numeric anatomical thresholds, target difficulty/exposure rules, Disarm timing/grip contest and shove impulse thresholds alongside this table. These are not silently settled by the force examples. Validate exact phase scaling and defend readiness against the exchange scheduler; if it stalls the next decision until the attacker's entire recovery ends, fix that scheduling contract before claiming the parry opening works. Recheck all eight fixtures, role swaps, paid costs, failed contacts and presentation truthfulness.

### 31.14 User refinement — extended Thrust, economical escape and stat matchup audit
- **Supersedes §31.13's one-tile Thrust restriction.** Thrust is a committed lean/lunge-and-return point attack: reach **2 tiles**, allowing one empty tile between fighters, versus reach 1 for stationary Light/Heavy. Example attacker (2,2), target (4,2): Thrust can contact along the straight line; Light/Heavy cannot. Keep its narrow locked line and piercing consequences, not simply intermediate abstract damage.
- Initial Thrust timings/cost remain impact t4, ready t9, stamina8/fatigue3 at Speed10 pending comparison; extended reach is a meaningful advantage that must be tested, not assumed balanced. The body/occupied tile stays at the original tile for this lean-and-return version; animate extension and return without claiming an actual tile move. During extension it gains no movement invulnerability, free retreat, wraparound or strike through an intervening body/wall. Trace/preview must expose any temporary counter-contact geometry if later added; do not silently make a visual lean move the simulation hitbox.
- Define reach-2 straight rays on the grid consistently (cardinal/diagonal rays only in the first pass); show legal target cells explicitly. An intervening blocked/occupied cell stops the line. Normal attack tracking locks still apply. At close range Thrust can contact, but lateral movement/deflection and recovery punishment remain counters. A two-tile gap is outside reach, not a valid Thrust. Test equal/diagonal rays, intervening obstruction, beyond reach and target movement before impact.
- **Make parry/evade rewards visible and real:** persistent “OPENING — enemy weapon recovering” with observable remaining window, reachable candidate follow-ups and whether their contact fits. Expire/update the cue when the opponent can respond or changes position. Display “Light fits / Heavy too slow / Disarm needs exposed grip,” not a generic success popup. A successful escape earns an opening only when the enemy actually misses/commits to recovery; movement alone does not stun them or create a guaranteed attack.
- **Unified escape chooses Walk/Run/Dash**, no separate emergency escape buttons to evaluate. This sits inside Defend's evade branch; direct movement modes may remain for deliberate travel. Enumerate clear routes and speeds against currently observed threat/strike timing and choose the **least-stamina-cost** route/mode that exits all relevant known threatened geometry before impact. Use stable tie-breaks and actual per-tile deadlines, not labels like “Dash is safe.” Do not choose an expensive mode if a cheaper legal route is sufficient.
- Initial comparable one-tile escape targets at Speed10: Walk step t4/end t6/stamina3; Run step t3/end t6/stamina5; Dash step t2/end t7/stamina9. These supersede the single t3/cost5 evade template. Multi-tile emergency routes must pay and time every step; define their total commitment explicitly before coding. A step on the same tick as impact is too late. Suggested fixture with incoming impact t7: Walk can suffice; impact t4: Run can suffice if it leaves threat; impact t3: only Dash can suffice; impact t2: none of these can evade from a fresh decision. These examples assume a clear safe destination, no later threat interception and no geometry still covering the route/end cell.
- Enemy anger/aggression is **not an automatic anti-dodge modifier**. Later emotion rules may change observable physical timing, tracking/chase choices and willingness to pursue; escape evaluates those real consequences. An aggressive opponent can anticipate/chase/reposition or catch a late/short escape; a slow/dizzy opponent may permit cheap walking. Do not read private anger to promise success/failure. Colors label known feasible escape versus conditional escape versus no timely route, with stated uncertainty.
- **Dash+Light gets a separate approach/strike contract:** initial one-tile approach at Speed10 lands t2, Light contact t5, ready t10, stamina13 (numeric tuning target, not current behavior). Approach is committed along a checked path; strike has reach1 from the actual endpoint and uses the Light force/contact rule. Lock the strike direction at t3 from the then-visible selected target, not silently home throughout travel. No second free move/attack, collision bypass or automatic pursuit. Interrupted/blocked approach cannot teleport the strike into reach. Multi-tile variants require separate explicit costs/timing and are not implicitly approved by this example.
- Example Dash+Light: attacker (2,2), target (4,2), approach ends (3,2) t2, strike t5. A target can parry if prepared at the right time, step out before impact, or exploit a sufficiently early attack against the approach when in reach. An opponent cannot hit an out-of-range approaching fighter solely because their strike is faster; check positions at actual contact. Unlike reach-2 Thrust at t4 from (2,2), Dash+Light physically advances and ends close enough for subsequent Light/Heavy/Shove, but costs more and risks interception. This positional gain versus exposure is its purpose, not maximum damage-per-second spam.
- **Matchup hypothesis, not a balance claim:** strength buys contact force, grip/brace/parry capacity and shove control; speed buys earlier physical deadlines and spacing opportunities, not unlimited extra voluntary attacks; fixed Accuracy buys reliable eligibility for difficult exposed anatomical targets, not bonus force, guaranteed hits or avoidance of timing/geometry. State these three roles in the UI.
- First controlled capability fixtures (level1, unarmored, same sword, same resources/personality unless deliberately varied): balanced 10 Strength/10 Speed/10 Accuracy; brute 14/7/9; lightweight 7/14/9; precision specialist 7/7/16. These are diagnostic archetypes, **not equal-priced builds**—the stat budget/progression value is not defined. Do not infer fairness from equal stat sums or assign a forced 50% target.
- With the proposed scaling, brute Light arrives ceil(30/7)=5, Heavy10, Thrust6; lightweight Light3, Heavy5, Thrust3; specialist Light5, Heavy10, Thrust6. Brute Light force11.2/Heavy22.4 and parry capacity19.6; lightweight Light5.6/Heavy11.2 and parry capacity9.8. Brute Heavy is dangerous if it connects, but lightweight can exploit a correctly read windup/geometry. High speed does not grant an automatic dodge or guarantee a wound interrupts before the stronger blow. Evaluate deadline rounding breakpoints; they can make a small speed increase disproportionately valuable.
- Precision specialist must obtain space or exploit a real opening to aim at a small vulnerable site; high Accuracy does not let a t6 thrust beat a t3 attack already in range. Site difficulty/exposure and tissue outcome must make precision meaningful without guaranteed eye/neck execution. If raw speed lets a fighter select every same target equally reliably, or Accuracy has no feasible role under novice contact rules, the model fails this audit and needs adjustment before claiming three viable stats.
- Audit potential dominant strategies explicitly: reach2 Thrust kiting; speed-based repeated Light interruption; Dash+Light spam closing every escape; parry→Disarm loops; strength making every defense fail; precision turning every legal contact into a lethal called shot. Record attempted and successful counters, geometry, injury thresholds and exact deadlines. Do not patch dominance by secretly changing fixed Accuracy or granting free defensive reactions.
- **Current discussion scope:** analyze this core matchup first with fear/anger/adrenaline modifiers disabled or held fixed, identical fresh fatigue and a short fresh-contact window. Still charge/report stamina and actual action time; do not delete the fatigue system or call long-fight results fatigue-independent. Reintroduce injury, blood/pain/fatigue accumulation and emotions in explicitly separate later passes. Serious anatomical consequence contracts remain in force; balance cannot be proven using force alone.
- Required evidence before calling these balanced: static timing/geometry/counter fixtures, mirrored seats/arena sides, matched policies and multiple personality styles, first-contact and post-miss/parry openings, actor-separated escape/approach/Thrust use, decisive injuries, win/end reasons and sensitivity to costs/rounding/target thresholds. Test both opening and sustained exchanges later. Establish the price/value of stat allocation before claiming equal-investment build balance; no supported verdict yet that strength, speed or Accuracy is universally strongest or equally balanced.
- **R0 follow-up probes (Nov 19, 2026; reproducible diagnostics).** Guard-vs-Heavy must distinguish “got hit” from guard cost/break: at adjacency, default player Guard was ready t2; Ronin Heavy contacted t9; recorded `BLOCK`/`GUARD_BREAK` with stamina cost 18.72 and stagger, but no `HIT` event—the existing system mechanically blocked that strike while visibly breaking the guard. Current strengths are 10 vs 18, exceeding the configured 0.6 heavy-break strength ratio. This does not explain the user's “31 net” display; R2 must trace every player-facing occurrence and never assume a recap number equals wound state. Default brute at initial gap4 selected `DASH_LIGHT`, path (8,3)→(7,3)@3→(6,3)@6→(5,3)@9, strike@12: this sample does maneuver, so reproduce NPC passivity across contexts/personality rather than assert it universally. R0 decision: use a matched app fixture at level1, Strength/Speed/Accuracy=10, StaminaStat20, Endurance10, no armor, same Cutlass, and stable personality in tests; preserve capability variants as controlled fixtures. §31.13 base-Speed10 targets are Light 3/6, Heavy 7/14, Thrust 4/9 and Walk4/Run3/Dash2; tune constituent phases so per-phase ceil yields those deadlines, not by replacing with one total ceil. Retain the specified proposed stamina costs; reconcile force multipliers/weapon edge against injury consequences before changing contact power. Disengage probe was rejected because the chosen direction did not increase distance; retest with a legal outward route. No player-facing guard damage was found in this Heavy fixture; discrepancy remains open pending UI audit.
- **R0 scheduler reconciliation decision (Nov 19, 2026; targets remain targets until R1 tests).** `phase_ticks` independently applies `ceil(base * 10 / effective_speed * recovery_weight)` (recovery also multiplies by `1 + 0.02 * total_weight`); `commit_action` then sums movement, windup, active, and recovery sequentially. The matched nominal Speed10 Cutlass novice has effective Speed **9.7** from 3 weight, so current config actually produces Light 5/13, Heavy 13/23, Thrust 8/18, Walk11, Run6, Dash4 (and costs are weight-multiplied). This is not the §31.13–14 target schedule. Preserve the approved target *impact/ready offsets* and least-cost escape costs, and solve/tune action phase inputs against the real equal-novice effective stats. Since target readiness is measured from impact (e.g. Light t3→ready t6), the strike is an instant event boundary; do not add the current two-tick scaled `active=1` as extra sequential time. R1 must encode/test the target-ready boundary explicitly, retain strike-before-movement tie order, and verify legal/late movement against the shared deadlines. Do not claim equal allocation or balanced variants. At current baseline the focused session/scheduler/tactics suite is **52 passed, 3 failed**: the three stale scheduler assertions expect the former faster-player/slower-Ronin fixture, including player step2 vs Ronin3; replace those with explicit controlled-speed cases plus matched-novice target cases, rather than restoring unequal defaults. Numeric stamina/fatigue/contact outcomes still require controlled tests before claiming the proposed force/cost table is achieved.
- **R1 geometry/Defend verification (Nov 19, 2026).** `geometry.py` defines the shared three-cell forward swing sector and reach-2 straight Thrust ray. Active attacks track visible target bearing until the configured lock boundary, then resolve against the saved bearing and actual line; later lateral movement can whiff. Threat tiles use the same geometry; blocker-aware ray resolution and preview filtering are covered. `defense.py` selects only visible/witnessed committed attacks, prefers an in-window supported parry for non-Heavy contact, otherwise chooses the lowest stamina-cost legal Walk/Run/Dash whose first step is strictly before impact and leaves all visible attack sectors; if none is safe it commits bounded blade defense. It does not inspect idle/unobserved hostile choices. Defend is in the standard UI alongside deliberate Move; ordinary unbounded Guard remains separate. Matched novice schedule/cost verification: Light 3/6/6, Heavy 7/14/14, Thrust 4/9/8; Walk 4/6/3, Run 3/6/5, Dash 2/7/9; one-tile Dash+Light impact5/ready10/cost13. Heavy escape, timed parry cost, bounded no-threat guard, hidden-threat non-prediction, same-tick order, body blocker, collision and legacy-active-save restore have regression coverage. Schema is 13; v12 config/session/notebook/world migration tested. **405** duel-system, UI, layout, recap and round tests passed; Python compile and `git diff --check` passed. Native SDL walkthrough passed 19 captures and a separate guide-off Defend preview showed the contextual choice without mutating state. Remaining gap: generic fatigue accrual still exceeds §31 target values; this is not fatigue/action-balance acceptance, and R2–R6 plus renewed user playtesting remain required.
- **R2 controls/read/recap verification (Nov 19, 2026).** Main dashboard directly exposes the approved tactical actions; action descriptions name intent and conditional risks. The always-visible solo read separates the opponent's observable commitment, timing/opportunity and visible condition, with unknown values for unseen opponents. Recap no longer computes or displays a damage-trade verdict, dealt/taken/net score, power-minus-armor arithmetic, or raw net values. It retains both fighters' committed choices, outcome, actual injury site/structure, pain/function, bleeding, stability/control/reactions and relevant resource changes; detailed log likewise states bodily/function outcomes without a damage score. Existing active opponent commitments at a recap boundary are projected into the choice list when visible. Verification: full duel-system/UI/layout/recap/round suite **411 passed**; `py_compile` and `git diff --check` passed (line-ending warnings only); guide-off live SDL game-display walkthrough **20 captures**, including the consequence recap, with explicit assertion that both choices/effects appear and forbidden damage-score/arithmetic strings do not. The screenshot at `%TEMP%\opencode\combat-r2-recap-native\18-consequence-recap.png` showed both Light/target and Dash+Light choices, miss/hit results, actual torso muscle injury, pain and bleeding. Still open: the quality/completeness of all consequence classes and renewed user comprehension/playtesting; R3–R6 remain.
- **R3 cross-action matrix and prototype audit (Nov 19, 2026).** Added `tests/test_duel_system_action_matrix.py` for Light interrupting an exposed Heavy (t3 vs t7), a timely lateral Run escaping the locked Thrust ray (step t3 vs impact t4), and an observed stagger/opportunity expiring exactly at its deadline. Together with existing focused tests, the conditional contracts now include Light/Heavy interrupt timing, Light/Heavy fit after Feint, parry→riposte Disarm within its opening, early/late/blocked/hidden-threat Defend movement, Thrust sidestep/body-block/range, Shove stable/unsteady/braced/obstructed/repeated-pressure cases, and no extra voluntary action. Descriptive seeded 12-fight strategy probes at `%TEMP%\opencode\combat-r3-{mirror-audit,punisher,lightspam,heavyspam,kite}.json`: mirrored Punisher policies timed out 12/12 with zero hits; Punisher beat Ronin 12/12; SpamLight beat Ronin 12/12; SpamHeavy lost 12/12; Kite lost 12/12. This confirms unresolved AI/policy and potential Light-spam concerns, **not** balanced matchup evidence: probes are unmatched scripted policies, not controlled novice strategy pairs, and must be revisited in R5 with fixed emotion/fatigue and role swaps. Full scoped suite **420 passed**; `py_compile` and `git diff --check` passed (LF→CRLF advisories only); current guide-off native SDL preview passed **20 captures** without campaign mutation. R3 action-contract verification is closed, but fatigue targets and any broad dominance/balance verdict remain open.
- **R4 Accuracy invariance (Nov 19, 2026).** `effective_stats` now returns the fighter's authored Accuracy exactly, independent of level, fatigue, pain, blood, fear, anger, nervousness, status multipliers or permanent eye injury; these remain separate from targeting legality/exposure and physical function. Removed all Accuracy-affecting action/Heavy modifiers, status wound modifiers, eye penalties and stale “anger/fear lowers accuracy” UI explanations. Target-zone difficulty, hand exposure and seeded roll jitter remain distinct mechanics; jitter affects a contact roll, not the authored/effective Accuracy stat. Current-schema validation rejects reintroduced debuff tuning; v14 config/session migration neutralizes old settings, removes obsolete wound `accuracy_mult`, and preserves an active attack's aim/deadline under schema **15**. Tests exercise damaged/healthy eye comparison, level8 + severe physical/emotional/status conditions, invariant display/help, migration and action difficulty; full scoped suite **426 passed**. Compile/diff checks passed (line-ending advisories only), native guide-off preview passed 20 captures. Limitation carried into anatomy: eye injury is currently persistent but explicitly reports target-acquisition effects as not yet modeled; do not imply blindness restrictions until implemented/tested. R4 anatomy and physiology remain.
- **R4 anatomy-site increment (Nov 19, 2026).** Schema **16** adds configured HEAD/EYE/TORSO/ARM/HAND/LEG subsite catalogues, including NECK and left/right feet. A seeded subsite and side now persist on WOUND rows and are copied into public HIT events, detailed/compact consequence recap, and injury rows; schema-15 config/session migration fills the catalogue and legacy wounds default to their broad zone. Catastrophic decapitation additionally requires resolved NECK, clean heavy CUTTING, and existing high post-armor threshold. A deep FOOT/ANKLE wound records impaired footing, reduces Speed via configured status, and blocks Dash without changing Accuracy. Bilateral eye impairment restricts precise eye-target acquisition; target state is consulted only after group visibility, and Accuracy remains unchanged. Deterministic fixtures verify site/side save round-trip, active-session v15 migration, neck/non-neck decapitation, foot function, eye targeting and hidden-target privacy. After updating superseded Accuracy/damage-score/legacy Guard assertions and fixing missing site text in detailed recap, the complete duel-file set passed **466 tests**. `compileall` and `git diff --check` passed (repository-wide LF→CRLF advisories only). Remaining: this is not full §31.9 acceptance; sided permanent limb loss, other site-specific function/target limits and native presentation still need implementation and review.
- **R4 sided hand-severance follow-up (Nov 19, 2026).** Schema **17** adds left/right wrist sites to HAND and persists explicit `weapon_hand` plus unique `severed_hands` sides. Only clean, undrifted heavy CUTTING contact resolving to a wrist can sever; weapon-hand loss drops/disables weapon use, while supporting-hand loss preserves one-handed use. Legacy unsided `hand_severed` remains a weapon-hand lockout; schema-16 config/session/snapshot/notebook data migrates to 17, and carry preserves weapon hand. HIT/WOUND, event text and permanent-injury rows distinguish the consequences. Regressions cover wrist-only qualification, seeded right-wrist severance, supporting-hand action usability/save round-trip/function text and v16 active-session migration. Focused anatomy/defense/wound set: **53 passed**; complete `tests/test_duel*.py` set: **469 passed**; touched modules/tests compile successfully. Native review remains pending; user acceptance is not implied.
- **R4 physiology checkpoint (Nov 19, 2026).** Schema **18** moves superficial grazes from indefinite `bleed_per_turn` to configured fractional one-off blood loss (0.25 baseline × contact bleed multiplier), records that loss on the wound/event, and reports no continuing bleed. Serious open wounds retain source-attributed ongoing bleed; bandaging leaves the configured residual rate. v17 config migration zeros legacy graze bleed; v17 snapshot migration converts saved active graze wounds to nonbleeding without charging the past loss again. Falling blood progressively reduces effective Speed/Strength and now proportionally limits stamina recovery using the same derived impairment curve; authored Accuracy remains unchanged. Tests cover immediate piercing-graze loss/no drain for three turns, v17 wound migration, monotonic severe-loss Speed/Strength with invariant Accuracy, and lower stamina recovery at blood 25 than 100. Focused injury/wound/recap/log suite: **83 passed**; full `tests/test_duel*.py`: **471 passed**; compileall passed. Still needed: broader equal-tick graze/moderate/deep/multiple-wound and treatment comparisons, native presentation, AI/balance and final playtest.
