# Simulation Story and Grounded Combat Expansion Plan

## 1. Purpose and status

Specify an implementation sequence that turns the current headless pirate-world simulation into a more legible source of emergent trade conflicts, rivalries, treasure stories, combat careers, and legends.

**Status: planning only.** This request creates a plan, not simulation code. Do not treat any phase below as implemented or verified. This plan complements `SIMULATION-COHERENCE-REPAIR-PLAN.md`; it does not waive its ownership, population, cause-chain, deterministic replay, or long-run gates.

**Hard scope boundary:** no magic or supernatural combat powers in this expansion. Do not add Devil-Fruit-like abilities, Haki-like powers, spells, transformations, or supernatural stat overrides. Combat growth comes from raw physical attributes, training, experience, weapons, armor, tactics, leadership, morale, ship condition, and circumstances. A legendary name or trait is not a magical ability and does not guarantee victory.

## 2. Evidence baseline and limits

Artifact reviewed: `C:/Users/Warp/AppData/Local/Temp/opencode/miska-one-year-events-2026-11-19/` (`events.jsonl.gz`, `events.md`, `run-manifest.json`, `state-census.json`). Run: seed `0`, director off, 365 days, tick 8760 endpoint.

Observed in this one run:

- 64,492 events; 59,150 were routine (about 91.7%). The raw stream is useful for debugging, but is not itself a readable chronicle.
- 206 market trades, 110 voyages started, and 91 arrivals.
- 201 encounter resolutions, including 17 ship captures, 11 ship sinkings, and 4 character deaths.
- 312 treasure-clue spreads but no `TREASURE_FOUND`; one legend promotion; one feud raid; one treaty transition; two territory claims; one monster sighting at the exact tick-8760 endpoint.
- The closing census reported 45 crews, 36 ships, and 9 hull-less crews with stranded named members.
- Event IDs were sequential, ticks nondecreasing, and all `caused_by` references resolved backward in the event stream. This supports event-log integrity, not overall game balance.

These are sample counts, not target frequencies. Director-off seed 0 cannot establish randomness, average story rates, or whether a rare outcome is unreachable. Tick 8760 is the inclusive end boundary; do not interpret day 365 as an additional full simulated day.

### Source-level gap worth reproducing first

- `sim/data/ai_rules.json:15` defines the `find_treasure` plan through `claim_treasure`; `sim/data/actions.json:27` lists that plan step.
- `sim/systems/environment.py:537-558` implements a finite, cause-linked `discover_treasure` operation.
- `sim/systems/decisions.py:649-714` creates dream plans and advances a step by incrementing `current_step`; that advancement does not validate the named step against a completed world action. `advance_dream_plan_step` has no production caller in the scanned Python sources (only a test caller).
- Separately, `sim/systems/decisions.py:477-489` increments `dream.progress` and emits `DREAM_PROGRESS` for `advance_dream`, regardless of whether an objective was achieved. That is not proof of treasure discovery or another concrete dream outcome.

Treat this as a reproduced code-path gap to turn into focused tests, not a claim that treasure discovery is impossible through every conceivable path.

## 3. Existing architecture to preserve

- Characters already have nine 0–100 stats and injury-adjusted `effective_stat`; `sim/data/characters.json:7-43`, `sim/systems/characters.py:302-330`.
- Ship combat power is already derived as a breakdown of firepower, boarding, durability, mobility, command, morale, supplies, and damage; `sim/systems/crews_ships.py:888-944`.
- Encounters already resolve stances, naval phases, boarding, capture, surrender, injury, and death; `sim/systems/encounters.py:302-558`.
- Set-piece fleet battles and named legend duels exist; `sim/systems/battles.py:271-540`, `sim/data/battles.json:1-33`. Their doctrine and legend-trait modifiers are non-magical configuration, but must be audited for bounded, explainable effects.
- Trade routes, market transactions, and route blockades exist; `sim/systems/economy.py:993-1020,1092-1213`. Treaty transitions and relations exist in `sim/systems/politics.py:930-1056,1194-1228`.
- Event classification already distinguishes routine bookkeeping from consequential outcomes; `sim/core/events.py:20-109`. Keep routine progress available for diagnostics and aggregate it only at export/render time; never coalesce decision wakes or alter RNG order just to reduce the log.
- The chronicler is a presentation layer over events, not an authority that may invent or mutate world state; use `sim/chronicler/core.py` and `sim/data/templates/chronicler.json`.

Do not merge headless simulation combat with `pirates/duel_system/`; that is a separate, more granular player/NPC duel system. This plan changes the `sim/` world simulation only unless a later request expands the scope.

## 4. Non-negotiable design rules

1. Determinism: same seed, state, and action schedule produce the same continuation. Use named/keyed RNG streams for combat outcomes; never consume global RNG from chronicler rendering.
2. Causality: every material event names its triggering event(s), affected entities, and before/after deltas. A story is a trace through real events, not a generated outcome pasted onto the world.
3. Agency: AI chooses only legal, affordable actions using its attributed beliefs. The story director may surface or prioritize existing opportunities, but cannot force a battle, treasure discovery, death, alliance, or victory to satisfy a quota.
4. Conservation: cargo, silver, weapons, ships, crew membership, and population must have explicit transfers, sinks, or sources. A battle cannot mint loot or replacement ships.
5. Bounded strength: all authored individual stats are integers in `[0,100]`; derived ratings are read-only and explained by a component breakdown. No unbounded level growth or automatic win at a tier boundary.
6. Separate systems: personal fighting ability, ship/fleet combat power, social reputation, and legend tier are different dimensions. Do not use one scalar to silently replace them.
7. Consequence classification: an attempt, clue spread, or progress tick is not a completed story outcome. Narrative significance requires a material change, lasting obligation, injury, loss, relationship change, public recognition, or verified objective completion.

## 5. Grounded character combat model

### 5.1 Attributes and levels

Keep existing `combat` as trained fighting technique/experience. Add the following raw physical attributes to the character model and validated character catalog:

| Attribute | Main effect | Must not be used as |
|---|---|---|
| `strength` | Force on a landed strike, grapple, shove, restraint, and heavy-weapon handling | A hit/accuracy guarantee |
| `speed` | Initiative, footwork, escape, and some defensive reactions | Free extra actions |
| `endurance` | Stamina capacity, exertion tolerance, and recovery between exchanges | Damage or skill by itself |
| `toughness` | Resistance to injury severity and ability to remain functional after impact | Immunity to injury or death |
| existing `combat` | Weapon handling, timing, guard, and tactical technique | Raw physical force |
| existing `willpower` | Fear, pain response, composure, and morale checks | Physical durability |

Retain existing `gunnery`, `seamanship`, and `leadership` for naval roles. Add no supernatural stat or ability field. Stats remain data-driven, injury-adjusted, age-aware, and capped at 100.

For display, provide proposed descriptive bands (tune after examining generated values): `0–19` untrained/limited, `20–39` ordinary, `40–59` trained, `60–79` elite, `80–94` exceptional, `95–100` rare peak. Bands are labels, not combat rules; crossing a band must not create a hidden multiplier.

### 5.2 Combat calculations

Implement a pure, read-only personal-combat breakdown. Do not persist a second authoritative strength total. Distinct calculations should make attributes matter for distinct reasons:

- Hit/defend/feint contests use `combat`, `speed`, weapon training, range, visibility, position, and bounded circumstance modifiers.
- Force and grapple contests use `strength`, body position, weapon leverage, and opponent resistance; force changes consequence, not legal reach.
- Stamina cost and exhaustion use `endurance`, action intensity, injury, and carried equipment.
- Injury severity/function uses weapon/contact, armor, `toughness`, and current condition. Toughness mitigates; it never makes a character invulnerable.
- Fear, surrender, and holding formation use `willpower`, morale, crew cohesion, witnessed events, and personal memories.
- Weapons and armor affect reach, force, protection, handling, and upkeep. Poor condition and missing equipment have actual costs.

A single derived `personal_combat_rating` may be shown as a rough comparison only if its formula is centralized, versioned, and accompanied by the full breakdown. It must not decide all hit, injury, or battle outcomes. Existing ship `combat_power` remains a separate breakdown; personal stats influence boarding and named duels, not cannon fire by magic.

### 5.3 Encounters, injuries, and progression

- Use a bounded, deterministic exchange model for named fighters: legal actions include attack, defend, evade, reposition, grapple/disarm where equipped, disengage, surrender, and continue. Keep this coarse enough for world simulation; do not reproduce a frame-by-frame tactical game.
- Model initiative, range/reach, stamina, openings, equipment, terrain/weather where relevant, morale, retreat, and third-party intervention. A stronger fighter can lose through fatigue, surprise, numbers, injury, bad position, or surrender; a weaker fighter can win without an unexplained multiplier.
- Emit parent encounter plus child action/outcome events. Record relevant pre-fight estimates/beliefs, actual combat breakdowns, RNG outcome identifiers (not hidden future rolls), injuries, equipment damage/loss, and post-fight state deltas.
- Keep injuries persistent and linked to the causing event. Define functional penalties, recovery, scars, and death risk in config. Avoid redundant penalties when existing injury modifiers already apply.
- Training and skill improvement require an actual training, practice, or witnessed-use event with a cause, time/cost, and bounded gain. Do not add a daily free stat increase. Cap at 100; age and injury rules remain explicit.
- Non-magical styles/feats may express a learned specialty (e.g. disciplined guard, quick reload, storm navigation). Each has prerequisites, contexts, costs/counterplay, small bounded effects, and a deed/training source. Existing legend traits must be re-authored or validated under this contract; none grant supernatural effects or automatic victory.

## 6. Story, trade, treasure, and legend systems

### 6.1 Objective plans must follow real actions

Replace counter-only plan completion with typed plan steps that each have:

- a legal action/handler;
- explicit preconditions and target identity;
- a completion predicate based on resulting state/event;
- a blocked/failed/abandoned state and reason;
- causal links to the plan, action, result, and final objective.

Advance a plan only after its predicate is true. `DREAM_PROGRESS` may remain a debug/effort signal, but cannot mean “objective achieved” or substitute for plan-step completion. In phase one, connect `find_treasure` end-to-end: learn a clue, travel to the real site, meet hazard/search preconditions, call `EnvironmentSystem.discover_treasure`, transfer the finite reward, and record whether/how the character returns or loses the prize. Repeated clues for a discovered site stop; discovery is idempotent and impossible at the wrong location.

Extend the same contract to other dreams only after their action handlers and terminal predicates exist. In particular, `defeat_character` requires an actual encounter outcome; `become_wealthiest` requires a measured rank change; `protect_entity` requires a real threat and survival/defense result; `reach_legend_tier` requires qualifying deeds and recognition.

### 6.2 Trade conflicts with economic causes

Build on existing finite market stock, trade routes, blockades, treaties, and faction relations. Add only the missing causes/outcomes required for a coherent conflict loop:

1. A real pressure: scarcity, price spread, debt, cargo theft, disputed toll/tariff, embargo, treaty breach, contraband seizure, or route blockade.
2. A motivated actor with the goods, authority, ship, money, information, and opportunity to act.
3. A chosen response: negotiate, compensate, reroute, escort, enforce, smuggle, retaliate, or withdraw.
4. A material outcome: conserved cargo/silver transfer, price/stock change, route access change, damaged asset, treaty/relation update, or unresolved obligation with a review/expiry condition.
5. A follow-on response and eventual resolution, de-escalation, or explicitly still-open state.

Do not turn every price movement into a war. Competing merchants and factions may choose different responses based on doctrine, values, beliefs, resources, and distance. Add per-cause cooldown/review bounds so one shortage does not emit the same escalation every day. A route blockade must affect actual travel/trade eligibility and state why it can be enforced or bypassed.

### 6.3 Emergent rivalries and legends

- Start rivalries from documented acts: theft, betrayal, injury, capture, unpaid debt, broken treaty, rescue, competition over a finite prize, or witnessed insult with an actual relationship effect.
- Escalation requires a capable actor, reachable target, meaningful stake, available resources, and an expiry/cooldown. Preserve parley, avoidance, compensation, escape, and reconciliation as valid outcomes.
- Legend advancement remains deed-backed and quota-free. Use qualifying event references and public recognition; distinguish what happened from what rumor says happened. A legend may be famous for seamanship, leadership, generosity, survival, trade, or combat—not just personal strength.
- Legends and epithets do not increase stats automatically. Any improvement must come from a separate, evidenced training/skill event. A non-magical feat is a bounded technique/leadership modifier, not a power-up.

### 6.4 Chronicle presentation

Create readable story threads by grouping existing parent/child causes, participants, locations, stakes, reversals, and lasting consequences. Keep routine rows in the raw/debug archive; summarize them in output. A chronicle must distinguish fact, attributed rumor, and uncertainty; must never invent a duel, power, motive, death, quote, or resolution absent from state/events.

## 7. Delivery phases and gates

### P0 — Baseline and focused reproductions

**Deliverables:** preserve the supplied run manifest/log/hash; add focused reports for dream plans, plan-step outcomes, treasure states, combat attribute distributions, encounter outcomes, trade activity, legend deeds, and closing stranded crews. Reproduce the counter-only dream progress and validate whether any production path reaches `discover_treasure`.

**Gate:** a small deterministic test demonstrates current behavior and the target behavior for a treasure plan; the report clearly labels the 2026-11-19 seed-0 run as a single sample. No balance tuning yet.

### P1 — Raw physical attributes and compatible character state

**Targets:** `sim/data/characters.json`, `sim/systems/characters.py`, `sim/core/world.py`, character snapshot/validation code, and `tests/test_sim_characters.py` / `tests/test_sim_world_snapshot.py`.

**Deliverables:** add validated 0–100 `strength`, `speed`, `endurance`, and `toughness`; preserve existing `combat` as technique; define deterministic initialization, aging, injury effects, event payloads, and a read-only combat profile. Migrate older snapshots by a deterministic documented mapping from existing stats/seed/entity ID; never re-roll a character on load.

**Gate:** bounds/role distributions, injury effects, save-load, old-snapshot migration, and same-seed resumed continuation all pass. Verify old snapshots without the new fields remain loadable and do not duplicate people/events.

### P2 — Grounded personal combat resolution

**Targets:** `sim/systems/encounters.py`, `sim/systems/crews_ships.py`, `sim/systems/battles.py`, `sim/data/encounters.json`, `sim/data/battles.json`, and new focused combat tests.

**Deliverables:** implement the derived attribute breakdown and bounded exchange model; integrate named combat into boarding/legend-duel contexts without collapsing ship combat power into personal strength. Emit causal injuries, stamina/condition changes, captures, retreats, surrender, and deaths only when their actual rules resolve them. Audit starting legend traits and doctrines for non-magical, bounded, counterable effects.

**Gate:** paired fixtures prove strength affects force but not hit legality/accuracy; combat skill affects technique; speed affects initiative/escape; endurance affects exertion; toughness mitigates but cannot nullify injury. Test equal-stat variance, disadvantaged upsets, equipment, injuries, surrender/retreat, conservation, no forced casualties, seeded determinism, and save/resume during a pending encounter.

### P3 — Real plan-step execution and treasure completion

**Targets:** `sim/systems/decisions.py`, `sim/data/ai_rules.json`, `sim/data/actions.json`, `sim/systems/environment.py`, and `tests/test_sim_decisions.py` / new treasure-plan tests.

**Deliverables:** add a typed plan-step executor/predicate registry. First complete `find_treasure` from clue to real, finite discovery/reward using the existing `discover_treasure`; record blocked/failed/abandoned states and keep causal references. Remove or relabel unearned generic progress so it cannot report completion.

**Gate:** positive path reaches one finite site exactly once; wrong-location, stale-clue, already-discovered, insufficient-capability, hazard-failure, death/capture, and save/resume paths are covered. Negative cases must not advance the objective. Prove a plan cannot complete merely by incrementing a counter.

### P4 — Consequential trade-conflict loop

**Targets:** `sim/systems/economy.py`, `sim/systems/politics.py`, `sim/systems/decisions.py`, `sim/data/economy.json`, `sim/data/politics.json`, `sim/data/actions.json`, and new trade-conflict tests.

**Deliverables:** connect pressure → legal motivated response → conserved material outcome → political/memory consequence → resolution/open obligation. Reuse route blockade and treaty systems where applicable; add no repetitive daily conflict rolls.

**Gate:** scripted cases for shortage negotiation, profitable rivalry, blockade/escort or reroute, smuggling/enforcement, treaty breach, and peaceful resolution. Assert cargo/silver conservation, information boundaries, route eligibility, event-cause completeness, and no duplicate escalation from one unresolved cause.

### P5 — Deed-backed reputation and multi-step story arcs

**Targets:** `sim/systems/crews_ships.py`, `sim/systems/memory.py`, `sim/systems/politics.py`, decision plan templates, `sim/core/events.py`, and `tests/test_sim_memory.py` / `tests/test_sim_politics.py`.

**Deliverables:** verify non-magical skill/feat prerequisites; connect rivalries and dreams to actual actions/results; add further plan executors only with explicit terminal predicates. Keep rumors sourced and distinguish witnessed fact from uncertain claims. Promotion remains achievement-based, not a quota.

**Gate:** tests demonstrate at least two distinct ways to earn public renown, one non-combat deed, one combat deed, and a rumor that differs from the underlying fact without changing it. No legend promotion or stat increase is fabricated.

### P6 — Chronicle and statistical acceptance

**Targets:** `sim/chronicler/core.py`, `sim/data/templates/chronicler.json`, `sim/tools/chronicle.py`, `sim/tools/coherence_run.py`, `sim/tools/coherence_audit.py`, `sim/tools/batch_test.py`, and tests for chronicler/coherence/batch output.

**Deliverables:** generate compact, causally grounded story threads alongside the raw event archive. Report event counts by type/class, completed versus blocked dream plans, discovery-to-clue chains, trade conflicts and resolutions, combat outcomes by relative attribute/gear/morale bands, legend deeds, repeat rival pairs, unresolved obligations, and stranded crew recovery.

**Gate:** same-seed replay and interrupted/resumed equivalence pass. Run a predeclared multi-seed 365-day set (recommend 20 seeds as an initial screen) and longer runs only after coherence gates pass. Report distributions and confidence limits; do not force minimum event quotas. Separate reachability tests (each outcome can happen in fixtures) from ordinary-run frequency (how often it happens naturally).

## 8. Regression and review matrix

| Concern | Required evidence |
|---|---|
| No magic | Schema/catalog grep and tests prove no supernatural ability fields, effect handlers, or combat overrides were added in scope. |
| Stat consistency | Every stat is bounded; effective values reflect injuries/age; rating breakdown matches the authoritative components. |
| Combat fairness | Same-strength seeded matchups show bounded variance; stronger does not guarantee a win; tactics, terrain, numbers, morale, gear, and injuries have measurable but non-dominating effects. |
| Narrative causality | Every arc event resolves to actual source/result events; no completed objective without its state predicate. |
| Trade conservation | Before/after cargo, silver, stock, ownership, and treaty state reconcile for all tested conflict paths. |
| Story possibility | Deterministic fixtures establish treasure completion, multiple conflict resolutions, combat outcomes, renown paths, and recovery after ship loss. |
| Natural variety | Multi-seed distribution report distinguishes zero/rare outcomes from bugs without adding forced events. |
| Output quality | Raw archive remains queryable; chronicle omits repetitive bookkeeping and labels rumor/unknowns correctly. |

## 9. Stop conditions and exclusions

Stop and investigate if a plan can complete without its real objective, combat strength overrides legal action/range, an injury has no source or functional rule, an event has an unresolved cause, cargo/silver/population is created or lost silently, or chronicler prose contradicts state.

This plan does not add magic, supernatural powers, a new real-time combat UI, player-duel changes, guaranteed war/treasure/legend quotas, unbounded character levels, or an LLM that writes outcomes into world state. Any later addition requires a separate scope decision.

## 10. Current checklist

- [ ] P0 baseline and treasure-plan reproduction.
- [ ] P1 physical stats and snapshot compatibility.
- [ ] P2 grounded personal combat.
- [ ] P3 real dream-plan execution and treasure completion.
- [ ] P4 causal, conserved trade conflicts.
- [ ] P5 earned renown, feats, and story arcs.
- [ ] P6 chronicler and multi-seed acceptance.
