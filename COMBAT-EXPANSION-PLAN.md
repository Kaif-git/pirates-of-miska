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

- **Multi-actor exchange and terminal scheduling.** The encounter remains one `CombatState`; explicit team/target identity, shared-snapshot NPC plans, one commitment per active participant, deterministic movement contention, strike obstruction, local visibility/hearing, witness-limited morale, and the configured guard deadline are implemented. A regression uncovered that session-level fear counters were only advancing when a yield already happened; NPC decisions now advance/reset fear once each, and the configured three-decision break can yield in both 1v1 and group sessions. Another regression found terminal actors could retain stale action records, blocking the next group exchange; terminal commitments are now interrupted, and inactive actors cannot strike, move, or receive passive updates. Commitments are not refunded.
- **Compatibility and invariants.** Schema-v6→v7 state/session migration preserves an in-flight movement path/deadline and deterministic continuation while adding group defaults; current group-session roundtrips preserve exchange commitments and replay. Tests cover active-path contention, cycles, bodies, explicit target lock, obstruction, action counts, deadline release, witnessed events/morale, hidden-state rendering purity, and incapacitated actors' stale strike/movement commitments. P5 BIND remains deferred.
- **Group UI and launch.** The group sandbox is separate from the legacy one-on-one game route: `python tools/play_duel_group.py --actors 5` (also accepts 2/10/20 total actors). Group cards show at most three visible threats plus overflow and omit enemy profile/stat/action internals; target choices use visible glyph/name, approximate range and posture. The native audit `python -B tools/preview_duel_group.py --output C:/Users/Warp/AppData/Local/Temp/opencode/combat-expansion-p7-group` passed 12 actor-count/window combinations (2/5/10/20 at 1280×800, 960×700, 640×600), controls/hitbox bounds, target-lock → strike → recap, and the real SDL live loop's pause/save serialization. Five native frames confirmed one player commitment, five-actor restore, and unchanged ship-world state. Inspected the 20-actor compact ready, multi-target list, and resolved exchange captures. Rendering stayed read-only.
- **Visible action path and startup UI fix.** The ordinary action row intentionally keeps advanced controls out of the primary row: select `More` → `Tactics` to see Feint, Shove, Disengage, Thrust, Disarm and Adrenaline. Disabled/dim entries indicate unmet legality/trigger requirements (for example, Disarm needs a parry-earned riposte and Adrenaline needs a configured trigger). A live hover over the dynamic `Stability (bal N)` meter previously passed that changing label to the static stat-help dictionary and raised `KeyError`; meter hitboxes now use the stable `Stability` key and the help sheet includes stability. `tests/test_duel_system_render_model.py`, `tests/test_duel_layout.py`, `tests/test_duel_system_tactics.py`: **84 passed**. The rerun native audit passed and produced `C:/Users/Warp/AppData/Local/Temp/opencode/combat-expansion-p7-ui-fix/group-stability-help.png` and `group-tactical-actions.png`; both were visually inspected.
- **Controlled balance examples.** `python -B tools/audit_duel_group_balance.py --samples 10 --output C:/Users/Warp/AppData/Local/Temp/opencode/duel-group-balance.json` (config SHA-256 `05578058ef9e4e8d7f4d140021c45b35732587ff6c4fc20af512630905b828a7`) recorded 10/10 wins in an intentionally favorable choke-point fixture: a strong player, a one-tile corridor, low-morale opponents and an injured leader, with a fixed NPC policy that attacks only visible in-reach targets and otherwise waits. The leader was incapacitated, three allies yielded after witnessed leader-loss morale responses, player blood ended at 88.33, and at most one distinct attacker hit on a tick. This demonstrates a controlled isolation/morale path, not ordinary matchup odds. In the separate open four-sided surround with normal group AI and a depleted player, 10/10 seeded cases incapacitated the player in two exchanges/10 ticks: eight hits, up to four distinct attackers on a tick, 23.8 blood remaining at collapse. This verifies lethality, not universal inevitable defeat.
- **Scale sample.** `python -B tools/audit_duel_group_scale.py --rounds 5 --seed 5200 --output C:/Users/Warp/AppData/Local/Temp/opencode/duel-group-scale.json` separately measured fixed-WAIT scheduling and normal-AI decisions. Fixed-WAIT five-round median exchange time for 2/5/10/20 actors: 1.460/3.434/8.479/24.195 ms. Normal-AI one-exchange median: 3.883/20.780/55.039/119.271 ms; maxima were 221.722/24.170/85.183/124.940 ms, including a single noisy 2-actor outlier. Mean pure render-model time was 11.876/9.794/17.222/35.214 ms. Action counts and deterministic replay assertions passed; peak traced allocation for the normal-AI sample was 315,740/345,072/396,641/497,254 bytes. These small synthetic workloads are scaling evidence, not a frame-time guarantee or a 1v20 balance claim.
- **Regression and audit.** The full scoped duel/UI/inspection suite passed **387 tests** after the scheduler changes; the subsequently added v6 session-migration regression passed in the focused migration run (`2 passed`, including the adjacent snapshot migration test). This yields coverage of the current scoped cases without claiming a single combined post-addition run. Scoped compilation, plus an explicit touched-file trailing-whitespace audit, passed. `python -B -m pytest --collect-only -q` collected 1,309 repository tests; a repository-wide run was not started because separate `python -m pytest -q` and 10-year simulation processes were already active. No unrelated processes were stopped. The native walkthrough is scripted; unaided player feedback was not collected. These limits do not weaken the scoped combat acceptance evidence and remain explicit.
