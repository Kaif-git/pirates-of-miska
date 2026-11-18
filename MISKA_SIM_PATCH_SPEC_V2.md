# Pirates of Miska: Patch Spec v2

**Audience:** the coding agent. This extends `MISKA_SIM_IMPLEMENTATION_SPEC.md` (call it the *Base Spec*). Section numbers like "Base §9" refer to it.
**Triggered by:** review of the seed-0, 10-year run (181,828 events, 330 characters, 60 s).
**Goals:**
1. Fix everything the review found (Part A).
2. Make every simulated person **meetable in the game**: generated as a full NPC, belonging to a squad, living in a real economy, and carrying an explainable motivation trail on every log line (Parts B to F).
3. Define the sim-to-game handoff and the game-to-sim write-back (Part G).

Rules from the Base Spec still apply: deterministic, data-driven, conserved, causal, prose-free sim core.

---

## Part A. Fixes from the seed-0 review

Each item has an ID, a root cause hypothesis, required changes, and an acceptance test. Do these **before** Parts B to G. Work in order.

### A0. Diagnostics first (do this before changing behavior)

The seed-0 output cannot tell us which systems are running. Add a per-year diagnostics report (`reports/diag_<seed>.json` + `.md`):

- Decisions scored per year, by action type, by tier.
- Ships alive per year, by owner type (merchant, pirate, marine, privateer, faction fleet).
- Characters alive per year by tier and role; births/recruits; deaths by cause.
- Fraction of MAJOR+ characters with a valid `next_decision_tick`, and with a non-idle plan.
- Per-system last-fired tick and call counts (economy, AI, encounters, factions, director, heat, memory, lod).
- Event counts per year split into **routine** vs **narrative** (A1).
- Count of vendettas/wants created vs converted into actions.

**Acceptance (T-DIAG):** report is produced for every batch seed; a system with zero calls for 30 days fails the run with its name in the error.

### A1. Separate routine events from narrative events

**Problem:** 14,600 market updates per year (40 islands x 365) and shipboard supply draws inflate event counts, so liveness/density tests pass while no story occurs.

**Change:**
- Add `event.class` to the schema: `routine | narrative | structural`.
  - `routine`: market tick, supply draw, weather shift, trade run completion that is not notable, disease contained, repair.
  - `narrative`: any encounter, death, capture, mutiny, treaty change, conquest, vendetta action, bounty, rookie spawn, discovery, set piece.
  - `structural`: faction founded/dissolved, succession, island ownership change.
- Routine events may be **aggregated at write time** into counters (per island per day, per ship per voyage) instead of one row each. Keep raw rows only for debug runs (`--keep-routine`).
- Liveness, density, and diversity tests (Base §17) run on `narrative + structural` only.

**Acceptance:**
- **T-LIVENESS-N:** max gap with no narrative event <= 7 sim-days in >= 95% of seeds, <= 14 in all seeds, after a 60-day warmup.
- **T-DENSITY-N:** narrative events/month never below 25% of the median month; median >= 30 events/month for the 40-island default world.
- Log size for a 10-year run drops by >= 80% without losing any narrative event.

### A2. Dead agents must not act

**Problem:** Aven Sable's ship sinks in year 1 and Tarin Quill's and Milo Vey's sink in year 2, yet all three keep completing trade runs through year 10. Either replacement happens silently or sinking does not remove the agent from scheduling.

**Change:**
- Introduce an `AgentLifecycle` guard: any scheduled wakeup for an entity whose `state` is `sunk | dead | archived | captured` is dropped and logged to the diagnostics channel as `STALE_WAKEUP`.
- Every ship/character replacement must be an explicit event: `SHIP_ACQUIRED` (purchase, build, capture, gift), `CAPTAIN_APPOINTED`, `CHARACTER_BORN/ARRIVED`. A merchant with no ship **cannot** trade. They go to a port, seek a loan or ship, or become a dockhand.
- Name reuse: a new ship may not be named after a sunk ship unless the event says `renamed_from` or `successor_of`.

**Acceptance (T-DEAD):** for every entity, no event with that entity as an *actor* has a tick after its terminal event. Zero violations over 100 seeds. A merchant whose ship sank has a `SHIP_ACQUIRED` event before its next trade run.

### A3. Population floors and an actors-with-ships budget

**Problem:** three merchants carry a 40-island economy, 330 characters are mostly cooks and civilians, and nobody raids them. Turnover is 18 deaths in 10 years, almost all background.

**Change:**
- Add a world budget in config (`world.actors`): target counts by band, with min/max, scaled by island count.
  - Merchant ships: 1.5 per island (min 0.8).
  - Pirate/raider ships: 0.6 per island, concentrated in lawless regions.
  - Marine patrol ships: 0.4 per island, concentrated by `patrol_density`.
  - Privateers/escorts: 0.15 per island.
  - Major captains: 12 to 25 at all times; legends: 2 to 6.
- A **recruiter system** runs monthly: if a band is under its min, spawn replacements through the real pipeline (Part B): from island recruit pools, unrest-driven rookie spawns, loans for ship purchase, shipyard output. Overages thin through attrition, never through deletion.
- Only `CAPTAIN`-capable characters count toward ship budgets. Cooks and civilians are `BACKGROUND` and live inside crews/island populations (Part C), not as individual rows.
- Raiding must exist: raider AI (Base §9) targets merchants on routes with low patrol density and high cargo value, using beliefs.

**Acceptance:**
- **T-BAND:** every actor band stays within [min, max] at every yearly sample in all seeds.
- **T-TURNOVER:** per 10 years, >= 6 MAJOR+ deaths or retirements, >= 6 new MAJOR+ risers, and at least 2 rookies who reach MAJOR via logged deeds.
- **T-RAID:** >= 30% of merchant losses (sinking, capture, cargo loss) in a decade come from piracy, with the rest from weather/disease/accidents.

### A4. Vendettas must be real state, not text

**Problem:** the director's "took up a vendetta against their rivals: conflict drought" lands on random surgeons, merchants, officials, names no rival, repeats verbatim, and no one acts on it. `conflict drought` leaks an internal tag.

**Change:**
- The director may **not** emit a vendetta by itself. It can emit `ENVIRONMENT_PRESSURE` conditions (Base §14). Vendettas are created by the memory/AI systems only, from a grievance memory above threshold plus a named target:
  `VENDETTA_DECLARED{holder, target, cause_event, stakes, deadline?}`.
- A vendetta is an entity with state (`active | pursued | satisfied | abandoned | inherited`), a target, a motivating memory, and a utility bonus for `hunt_rival(target)` plus related actions (spy, blockade, poison supply, poach crew, sabotage, duel challenge).
- Cooldown: the same holder cannot re-declare the same vendetta within 180 days unless the grievance rose. A reconciliation event can set it to `satisfied`.
- Only characters with a ship, crew, or faction role and capacity to act (`capability >= threshold`) can hold active vendettas. Others can hold *grudges* (memory-only) that matter in dialogue.
- Tag leakage: all `causes_tags` map to display strings through the template layer (Part F). Raw tags never print.

**Acceptance:**
- **T-FEUD:** >= 60% of declared vendettas by capable holders produce a hostile act (attack, blockade, sabotage, challenge) within 120 days, or are logged as `abandoned` with a cause.
- **T-VENDETTA-VALID:** every vendetta has a resolvable target, cause event, and holder capability. Zero orphaned vendettas.
- **T-NO-REPEAT:** no verbatim duplicate narrative line for the same holder within 365 days.

### A5. Bounty, heat, and epithets derive from deeds

**Problem:** posters list 900 and 600 silver with "no recorded crimes"; no epithets awarded despite a legend winning a duel and a fleet battle; the Royal Marines never act.

**Change:**
- `Bounty` = sum of itemized `CrimeCount{type, event_id, value, issuer}` entries. A poster is rendered from those. No crimes means no bounty. Bounty issuance is a faction decision at monthly tick, using doctrine and heat.
- Heat (Base §11.5) accumulates per character/crew from the same crimes, with decay. Heat thresholds produce government actions: patrol reassignment, hunter assignment, fleet deployment. The Marines are a full faction with budget, commanders, and decisions. At least one marine action per month in a healthy world (T-MARINE).
- Epithets: the reputation system evaluates deed patterns monthly against `data/epithets.*` rules (e.g., `won_duel_vs_legend`, `captured_3_warships`, `sank_flagship`, `betrayed_ally`). When satisfied, emit `EPITHET_EARNED{char, epithet_id, cause_events}`. A character can hold at most one displayed epithet plus a list of earned ones.

**Acceptance:**
- **T-BOUNTY:** every bounty amount equals the sum of its itemized crime values; every crime event exists. Posters with zero crimes are impossible.
- **T-EPITHET:** in a 10-year run, >= 3 epithets awarded; Kellan Rill-style duel winners over legends always earn one within 30 days.
- **T-MARINE:** Marines issue >= 1 bounty change or patrol/hunter assignment per quarter in every seed.

### A6. Faction coherence

**Problem:** factions named after merchants; founded by unrelated captains; a faction "dissolved after the battle" before its namesake's ship sank; no foundings after year 1; Royal Marines inert.

**Change:**
- Faction names come from the founder's data (founder epithet, flag, home island, ideology) via a name generator, never from an unrelated entity's name.
- Founding requires: a founder with ambition and leadership above thresholds, >= 2 aligned captains or crews, a stronghold or anchorage, and a stated purpose. Dissolution requires an event cause (leader death with no heir, conquest, bankruptcy, merger).
- Faction timeline must be consistent: validate that events reference faction members who were members at the time (T-FACTION-CONSISTENCY).
- Foundings and mergers occur throughout the decade at a configured rate, driven by unrest, vacuums, and ambition.

**Acceptance:**
- **T-FACTION-NAME:** founder-derived names only; no faction named after a non-member.
- **T-FACTION-TIMELINE:** every faction event actor was a member at that tick.
- **T-FACTION-FLOW:** >= 3 foundings and >= 2 dissolutions/mergers across years 2 to 10 in at least 90% of seeds.

### A7. Chronicler rewrite (volume, quality, bugs)

**Problem:** ~80% of the chronicle is "X completed N trade runs near Y"; counters repeat every year; capitalization bugs ("lucan Mere", "aven's Wake"); "ship's ship"; "ship sunk." fragments; the "grown in the telling" suffix is mechanical; biographies are 90% empty; obituaries say "died after the passing years"; legends drop off a cliff.

**Change:**
- **Collapse routine:** one line per merchant per year ("Milo Vey ran 62 trade routes across 14 ports, moving 4,100 units, mostly iron and sailcloth") and only if they were notable; otherwise merge into one regional commerce summary per year. Counter lines ("14,600 market updates", "storm tracks shifted N times") go to an appendix, not the chronicle.
- **Significance threshold** for the chronicle; sort by significance, not by sim order. Each year includes at most N (config) items plus a "quiet year" paragraph if nothing exceeds threshold, built from economic and demographic shifts.
- **Template validation at load** and at render: sentence-initial capitalization applied *after* slot fill, but proper nouns preserved; reject double possessives and repeated words; verb fragments forbidden; slot types checked (Part F). Unit-test every template with synthetic data.
- **Rumor distortion** must actually change something (numbers, attribution, exaggeration of outcome, wrong culprit) according to a distortion model with seeded variance and a recorded `truth_event_id`. If nothing is distorted, no suffix.
- **Biographies from state:** render from traits, values, dream, current wants, crew, ship, wealth tier, grudges/allies (top N opinions), injuries/scars, epithets, bounty, notable deeds. Any living tier >= MINOR gets at least 3 facts; BACKGROUND gets a one-line "known as".
- **Obituaries:** age, cause of death, place, notable deeds, survivors, heirs, unfinished dream, last known grudge.
- **Almanac headlines:** must be the top-significance event of the year; if none qualify, use the largest economic/demographic shift. "The records place X at a grievance" is forbidden.
- **Legends list:** ranked by fame with a minimum-population guarantee (promote the top N by fame regardless of absolute value, with a "rising" section).

**Acceptance:**
- **T-CHRON-SIZE:** a 10-year chronicle for the default world is <= 400 lines (excluding appendices).
- **T-CHRON-QUALITY:** zero lowercase-initial sentences, zero doubled words, zero fragments, zero raw IDs, zero raw tags across 100 seeds (regex plus POS-tagger-lite checks).
- **T-RUMOR:** every distorted rumor differs from its truth event on at least one recorded dimension, and the log records the dimension.
- **T-BIO:** >= 95% of MINOR+ characters' biographies include >= 3 distinct facts.

### A8. Meta: the "post-M12" claim must be verified

M5 to M11 behaviors (betrayal, mutiny/crew split, feud payoff, heritage, conquest, marine set pieces, heat, succession) appear absent. Add a **Behavior Coverage Report** to every batch: for each behavior, the count per 10-year run and which seeds had zero. Gate milestones on coverage:

| Behavior | Min per 10-year run (median across seeds) |
|---|---|
| Captures / sinkings from combat | >= 25 |
| Ransoms or executions | >= 5 |
| Mutinies or crew splits | >= 3 |
| Betrayals (treaty or personal) | >= 2 |
| Vendetta paid off (satisfied by action) | >= 4 |
| Inherited grudge acted on | >= 1 |
| Island conquests/liberations | >= 3 |
| Marine-vs-pirate set pieces | >= 1 |
| Successions | >= 3 |
| Heat-driven government responses | >= 10 |

**Acceptance (T-COVERAGE):** medians meet the table; any behavior with zero median fails the batch and names the missing system.

---

## Part B. NPC generation pipeline ("meetable people")

Base §7.1 describes a generator. This part defines the **full pipeline** so that every character the sim creates can be dropped into the game without a rebuild.

### B1. Three representation levels

| Level | What exists | When |
|---|---|---|
| **L0 Statistical** | Counts and averages inside a crew/island pool | Most BACKGROUND people |
| **L1 Sim Character** | Full sim record (Base §4.1) | MINOR/MAJOR/LEGEND; any person who *did something* |
| **L2 Game NPC** | L1 plus appearance, voice, schedule, inventory, dialogue state, quest hooks | Anyone the player can see, talk to, fight, or hear about in detail |

Rules:
- **Lazy instantiation:** L2 data is generated the first time it is needed and then **persisted** (the NPC never changes face, voice, or quirks later). Generation is deterministic from `(world_seed, char_id)` so regeneration after a lost save reproduces the same result.
- **Promotion path:** L0 to L1 on notable events (kills a named target, wins a duel, takes command, makes a big sale, is named in a rumor). L1 to L2 when the player is within interest range, or when a quest/dialogue references them.
- **Demotion:** L2 data is retained (small), only runtime AI state is dropped.
- Every L0 person promoted must keep **continuity**: the sim keeps a stable `person_id` for sampled members of L0 pools (e.g., "the cook of the Widow's Wake") so that when the player meets "the cook," the same person is there tomorrow and the history stays coherent.

### B2. Generation inputs

`generate_person(spec, ctx, rng)`:
- `spec`: role, tier, faction, region/island, age band, optional archetype, optional required traits, optional heritage links (parents, mentor, kin).
- `ctx`: local economy (wealth tier, prices), local culture, current events affecting the island (famine, war, blockade), crew membership if any.

### B3. Generated components (all data-driven)

1. **Identity:** name from regional pools; family/house links; birthplace; birth_tick consistent with age.
2. **Stats and aptitudes:** per Base §4.1, drawn from role and heritage distributions; includes `potential` caps.
3. **Traits and values:** archetype presets with correlated priors; no all-high or all-low profiles (enforce variance).
4. **Dream and wants:** one dream; 1 to 3 current wants derived from circumstances (debt, hunger, a lost kin).
5. **Backstory graph:** 3 to 8 historical facts, each a *simulated-consistent* record (not just text): a parent who died at sea (creates inherited grudge or debt), a mentor, a past employer, a former crewmate who betrayed them. These facts become **seeded memories and opinions** so they matter in decisions and dialogue.
6. **Skills and gear:** stat-appropriate weapons, tools, clothing; ship-role kit.
7. **Appearance (stable):** a compact descriptor set (`build, height, skin, hair, eyes, scars, tattoos, clothing_style, signature_item, posture`) chosen from data tables with region/role/injury weighting. Injuries and age update descriptors consistently (a lost eye in the sim adds an eye patch descriptor to the NPC; the game reads descriptors, never the other way around).
8. **Voice and manner:** `speech_register (formal, rough, sly, warm...)`, `verbal_tics` (1 to 3), `topics_of_pride`, `taboos`, `accent_tag`. Derived from traits, region, and class. These drive dialogue line selection (Part F).
9. **Daily schedule template** (for island presence): sleep/work/tavern/market/patrol blocks by role and traits, with exceptions when wants are urgent.
10. **Inventory and pocket money:** from wealth tier; must be drawn from the economy (Part D); purchases and thefts are real transfers.
11. **Social ties:** family, friends, rivals, employer, creditors, lovers (data-driven relationship kinds), each as an `Opinion` with cause memories.
12. **Hooks:** 0 to 3 generated **quest hooks** or interaction hooks derived from wants and dream (e.g., "needs a navigator who knows the Brine Reach", "owes silver to the Dockmaster", "will sell a map for a favor").

### B4. Coherence validators

On generation and every N days after:
- No person with contradictory facts (e.g., age < parent's age).
- Appearance descriptors match injuries and age.
- Inventory value consistent with wealth tier and recent income.
- Voice not conflicting with traits (a very formal speech register on a rough archetype requires a backstory reason).
- Name uniqueness among **living named** characters in a region; duplicates allowed only for explicit lineage.

**Acceptance:**
- **T-NPC-DET:** `generate_person(spec, ctx)` with the same seed and inputs yields byte-identical output; regeneration after load matches stored L2 data exactly.
- **T-NPC-COHERE:** 1000 random generated NPCs pass all validators.
- **T-NPC-VARIETY:** across 1000 NPCs of the same role, no more than 3% share the same (traits-top-3, values, dream-kind, speech-register) tuple.
- **T-NPC-LINKS:** every backstory fact referencing another person points to a real, loadable character (L0-sampled or L1) with a reciprocal record.

---

## Part C. Squads, crews, and groups

The sim crew (Base §4.4) is a roster plus morale. For the game it must also work as a **squad** that can be met, followed, fought, hired, and commanded.

### C1. Group types

`Group { id, kind, name, leader_id, members[], structure, doctrine, morale, cohesion, supply, base_location, current_orders, history }`
Kinds: `ship_crew`, `boarding_party`, `landing_squad`, `patrol`, `escort`, `caravan`, `garrison`, `gang`, `guild_cell`, `expedition`, `mercenary_band`.

- A ship crew can **detach** squads (e.g., a landing party of 6 from a crew of 40). Squad members remain crew members and keep their roles. The ship's computed strength (Base §8.1) updates while they are away.
- Squads have **roles** (point, shield, marksman, medic, scout, runner, carrier) assigned from member skills and traits, and a **formation preference** by doctrine.

### C2. Composition from the roster

- Named members (L1/L2) fill leadership/specialist slots. Unnamed members are L0 pool slots: "34 sailors (avg combat 38, avg loyalty 55)". When a squad is needed in the game, **sample L0 members into L1 persons deterministically** (seeded by `(group_id, slot_index)`), generating minimal NPCs (Part B) that persist from then on.
- Casualties in the game or sim remove sampled persons from the pool and record deaths; unsampled casualties reduce pool counts.

### C3. Squad AI (sim-level)

Squads obey orders from their leader's utility scoring: `escort`, `ambush`, `board`, `raid_shore`, `guard`, `scout`, `retreat`, `parley`. Orders produce events with `group_id` and `leader_id`.
- **Cohesion** matters: low cohesion means orders are partially obeyed, with chance of desertion or panic.
- **Doctrine** (aggressive/defensive/opportunist/honor-bound) shifts utility terms.

### C4. Game-facing squad contract

When the player encounters a group, the sim provides a **GroupSnapshot**:
```
GroupSnapshot {
  group_id, kind, name, leader: PersonRef, members: [PersonRef + role + position_hint],
  intent: { goal, urgency, stance_toward_player }, morale, cohesion,
  carried: { supplies, cargo, notable_items },
  recent_history: [EventRef x N], rumors_about_them: [...],
  engagement_options: [talk, trade, fight, flee, recruit, bribe, follow]
}
```
- `PersonRef` expands to an L2 NPC on demand.
- The game may run its own tactical simulation, but at the end it returns a **GroupOutcome** (Part G) which the sim applies (casualties, loot, morale, memory).

### C5. Player recruiting and command

- Player can recruit sim persons subject to their opinion of the player, wants, loyalty to current employer, and pay. Recruiting creates `RECRUITED` and may create `POACHED` consequences for the former employer.
- Player squads use the same Group schema. Their members continue to have wants, opinions, and memories; they can leave, demand pay, mutiny, or die like anyone else.

**Acceptance:**
- **T-SQUAD-CONSERVE:** member counts are conserved across detach/reattach; no person is in two squads simultaneously.
- **T-SQUAD-SAMPLE:** sampling the same slot yields the same person each time; sampled deaths reduce the pool exactly.
- **T-SQUAD-ORDERS:** every squad order event has a leader, a cause, and an outcome event.

---

## Part D. Economy for people (not only for ports)

Base §6 gave a conserved goods/silver economy. Extend it down to **individuals and shops** so the player can trade, steal, hire, and cause shortages that NPCs react to.

### D1. Personal finance

Each L1/L2 person has `wealth.silver`, `income_streams[]` (wage, shares, rent, trade profit, stipend), `expenses[]` (food, lodging, upkeep, debt payments, vice), `debts[]`, and `savings_goal`. Daily/weekly settlement transfers silver between real accounts (employer, island treasury, shops). Shortfalls produce wants (borrow, steal, take a risky job).

### D2. Shops, services, and inventory

- Each island has **vendors** (named L1/L2 people or institutions) with finite inventory and silver, specialty goods, markup by `trade` skill and disposition, and restock rules from real island stock.
- Services: repair, healing, lodging, information (rumors for a price), smuggling, forging papers, hiring crew. Each has a price formula from local prices, vendor skill, and the player's reputation/opinion.
- **Player trades** are real transfers: they shift island stock and prices (small but nonzero). Large dumps or purchases move prices measurably, and NPCs react (merchants arbitrage, officials tax, rivals notice).

### D3. Crime and enforcement

- Theft, smuggling, bribery, and fraud are transfers with detection chances. Detection creates memories, opinions, bounties, and patrol changes.
- Black markets exist per island tier with their own prices and stock; contraband flows have sources and sinks (Base §6.4).

### D4. Wages, shares, and crews

- Crew pay follows `pay_policy` (fixed wage, shares, mixed). Arrears are tracked per person and drive morale/loyalty (Base §8.3). Loot shares are real transfers computed per rule and are visible to the player (a crew complaint is based on real numbers).

### D5. Economic motivations and feedback loops

- Persons with the **wealth** value or high greed weigh income more; honor-bound persons refuse some jobs; debtors prioritize repayment; starving persons accept bad offers.
- Island shocks (blockade, famine, storm, new tax) propagate to prices, wages, unrest, crime, and recruitment within a configured number of days.

**Acceptance:**
- **T-ECON-PERSON:** the sum of all personal, crew, shop, island, faction, and cargo silver equals the global ledger, every day.
- **T-ECON-PLAYER:** simulated player buy/sell of a good changes island stock and price in the correct direction, and the change decays toward baseline within N days.
- **T-ECON-FEEDBACK:** a scripted blockade raises food price on the target island, lowers its prosperity, raises unrest, and increases pirate recruitment within the configured windows.
- **T-ECON-DEBT:** every debt has a creditor, a creation event, and a repayment or default event eventually, or remains active with accruing consequences.

---

## Part E. Motivation tracking on every log

Goal: for any event, the game (and a designer) can answer **"why did they do that?"** without guessing. This replaces the optional debug channel of Base §9.3 with a first-class record.

### E1. Decision trace (per decision, stored compactly)

```
DecisionTrace {
  id, tick, agent_id, agent_tier
  trigger: ENUM { idle, arrived, need_threshold, news, interrupt, scheduled_review, order }
  context_ref: {location, ship?, group?, cargo_value?, supplies_state, health_state}
  candidates: [ { action, params, hard_filter_passed, score, components } ]   // top K=5 kept
  chosen: { action, params, score, selection_method: softmax|forced|order }
  drivers: [ MotivationDriver ]    // the top contributors to the chosen score (max 5)
  beliefs_used: [ { subject, attribute, believed, confidence, source, truth_gap? } ]
  alternatives_rejected: [ { action, reason_code } ]   // top 3
  emitted_events: [EventId]
}

MotivationDriver {
  kind: ENUM { want, dream, trait, value, grudge, fear, debt, loyalty, order, opportunity, need, habit, mood }
  ref: (want_id | dream_id | trait_name | char_id | debt_id | faction_id | event_id)
  weight: float (contribution to the score)
  note_code: string   // machine reason code, e.g. "WANT_REPAIR_SHIP", "GRUDGE_HUMILIATED"
}
```

### E2. Every event links to a motivation

- Each **narrative** event produced by an agent decision carries `decision_trace_id`. Events produced by non-agent systems (weather, disease) carry `cause_system` instead.
- Add to the event schema: `motivation_summary: [MotivationDriver x <=3]` copied from the trace for cheap queries.
- **Retention:** keep full traces for L2 NPCs, legends, majors, and any decision that led to a narrative event of significance >= threshold. Keep only drivers + chosen action for MINOR. Keep nothing but counters for routine decisions. Config `trace.retention` governs. Compress with delta encoding.

### E3. Mood and state tracking

Add a lightweight `Mood` vector per L1+ person: `stress, anger, fear, joy, grief, resolve, boredom` (0 to 100), updated by events, memories, and needs, and decaying toward a trait-based baseline. Mood adds drivers (`kind: mood`) and modulates utility (angry = more aggressive; grieving = less risk-averse). The game reads mood to choose dialogue tone and animation.

### E4. Motivation timeline per person

Maintain, per L1+ person, a rolling **motivation ledger** of: active wants (with age and urgency), active vendettas/grudges, the dream and its progress, loyalty targets, debts, mood trend, and the last N decisions. This ledger is what the game queries to render "what is this person up to and why."

### E5. Queries (API and CLI)

- `why(event_id)` -> decision trace + drivers + beliefs used + truth gaps ("believed Meridian was weak (conf 0.6); true strength was 2.1x").
- `why_not(agent_id, action, tick)` -> the rejection reason or score gap.
- `motivation(person_id, at_tick?)` -> the ledger snapshot.
- `story_of(person_id, from, to)` -> events grouped by motivation arc ("revenge for Dain", "keep the crew fed").
- `who_wants(thing)` -> people whose active wants/dream reference a good, place, or person (the basis for quests and rumors).

### E6. Chronicler integration

Narration reads `motivation_summary` to add the clause ("hoping to settle the debt he owed Brann Ash...") without inventing reasons. Reason codes map to phrases through templates (Part F). A line is only allowed to state a motive if a driver supports it.

**Acceptance:**
- **T-TRACE-COVER:** 100% of narrative events with an agent actor have a `decision_trace_id` resolving to a trace; 100% of those traces have >= 1 driver.
- **T-TRACE-TRUTH:** for decisions with `beliefs_used`, truth gaps are computed at decision time (from ground truth) and stored; every upset in T-UPSET links to a trace with a non-trivial gap or to an explicit random factor.
- **T-MOTIVE-CONSISTENT:** for 1000 sampled traces, the chosen action's score is within the top K candidates, and its top drivers match the highest components (re-score offline and compare).
- **T-MOOD:** mood changes are bounded, decay toward baseline, and have a causing event for every large (> 20) jump.
- **T-WHY-LATENCY:** `why(event_id)` returns in < 50 ms on a 10-year log.

---

## Part F. Text and dialogue layer (so people sound like people)

### F1. Reason codes to phrases

All sim output is codes and structured data. The text layer maps codes to phrases with slot typing and variants:
- `note_code` -> reason phrases per speech register and relationship ("It was the debt." / "Business, nothing personal." / "I owed him that blade.").
- Slot types: `PersonName`, `ShipName`, `Place`, `Amount`, `Good`, `Time`, `Faction`. The renderer fills slots by type and applies capitalization and possessive rules by type (fixes A7 bugs structurally).
- Templates declare `required_roles`, `allowed_registers`, `min_trust/max_trust`, `requires_driver`, and `forbids_self_reference`.

### F2. Dialogue state per NPC

```
DialogueState {
  person_id, opinion_of_player, known_facts: [Fact], rumors_known: [RumorRef],
  secrets: [Fact + reveal_conditions], topics: [TopicId + availability],
  last_conversation_tick, promises_made: [...], promises_owed: [...],
  mood, current_wants_visible: [...]    // wants they'd mention
}
```
- **Knowledge is bounded.** An NPC can only say what is in their belief store/known facts. Gossip they would plausibly have heard (co-located events) comes from the information system (Base §9.4), with confidence and distortion.
- **Honesty model:** lies and omissions depend on traits (cunning, honor), relationship, and stakes; if an NPC lies, the lie is recorded as a Fact with `truth_value=false` so the game stays consistent in later conversations.
- **Opinion of the player** is the same `Opinion` struct as NPC-to-NPC, derived from memories of player actions.

### F3. Topic generation

Topics are generated from the NPC's motivation ledger and knowledge:
- "my current want" (ask for help), "my grudge" (mention a rival), "my dream" (if trust is high), "local news" (recent events at the island), "rumors", "trade" (from the vendor role), "work" (hiring, jobs), "my crew," "my past" (backstory facts).
- Each topic has availability conditions (opinion thresholds, completed prerequisites, reputation).

**Acceptance:**
- **T-TEXT-SLOTS:** 100% of templates render without placeholders, wrong-case proper nouns, or double possessives under 10,000 randomized fills.
- **T-TEXT-REASON:** any rendered motive statement is backed by a driver in the trace (audit by re-checking `requires_driver`).
- **T-DIALOGUE-CONSIST:** an NPC never contradicts a recorded Fact in later conversations unless the contradiction is flagged as a lie or a change of belief with a cause.
- **T-KNOWLEDGE:** an NPC does not reference an event they could not have learned (no information path in the belief/rumor graph).

---

## Part G. The meeting layer: sim <-> game contract

### G1. Presence resolver

`where_is(person_id, tick) -> { location, activity, visibility, confidence }`
- For L1+ persons the sim's authoritative location is used. If at an island, the **activity** comes from the NPC's schedule and current wants (sleeping, working, drinking, patrolling, bargaining, brooding).
- The game requests **LocalPopulation(island, tick)**: the set of persons present, derived from island pools + sim-known persons + crews docked + visitors. It returns persons at L2 if they are within interest range and L1/L0-sampled otherwise.
- Persons in transit: `where_is` returns "at sea on route R, ETA ...", and the game can **intercept** (Part G3).

### G2. Interest management

- **Interest set** around the player (configurable radius in islands and routes). Persons inside the interest set run at full L1 fidelity, and L2 data materializes on view.
- **Always-on set:** legends and persons tied to active player quests stay full fidelity regardless of distance.
- Entering/leaving interest triggers LOD promotion/demotion (Base §16); no one "pops" into existence: arrival and departure follow real travel events.

### G3. Encounter materializer

When the player and sim entities come into contact (port, route, ambush, rumor chase), the sim hands the game an **EncounterSpec**:
```
EncounterSpec {
  encounter_id, location, weather, time_of_day, kind,
  parties: [GroupSnapshot], relationships: [pairwise opinion/stance],
  stakes: { cargo, ransom, bounty, contracts, grudges },
  initial_intents: per party,
  allowed_resolutions: [talk, trade, bribe, fight, flee, join, betray],
  seed
}
```
The game runs it however it likes (dialogue, tactical combat, auto-resolve) and returns an **EncounterResult**:
```
EncounterResult {
  encounter_id, outcomes_per_party, casualties: [{person_id, state: dead|wounded|captured|fled}],
  damage: [{ship_id, hull, rigging, cargo_lost}], loot_transfers: [...],
  decisions_by_player: [...], promises_made: [...], facts_revealed: [...],
  witnesses: [person_id], duration
}
```
The sim applies it as events with `source: "game"`, `caused_by` the encounter spec, running the normal systems: memories, opinions, bounty/heat, mood, economy transfers, morale, faction reactions. **The game never writes to sim state directly.**

### G4. Player as an agent in the sim

- The player is a Character (L2, `controlled_by: player`). Their actions emit events. Their reputation, bounty, opinions, debts, and crew are normal sim state.
- NPCs form beliefs about the player via the information system (they only know what they saw, heard, or were told), so the world reacts with realistic delay and distortion.
- **Time handling:** the world advances during sleep/travel/wait using the normal scheduler. If the player triggers a time skip, run the sim at LOD appropriate to distance and report a **"while you were away"** digest from the chronicler (only items the player could plausibly learn).

### G5. Quests and contracts from motivation

- A **QuestSeed** is generated when a person has a want/dream/grudge that the player could affect and the person's trust/need conditions are met:
  `QuestSeed { giver, want_ref, objective_template, reward (from giver's real wealth/goods), deadline, consequences_if_ignored, consequences_if_failed }`
- Quests must **fund rewards from real accounts** and **have world consequences** (an ignored request lets the want resolve some other way in the sim: the debt goes to default, the navigator takes another job, the vendetta is pursued).
- Because quests derive from the motivation ledger, they track world state: if the giver dies in the sim first, the quest ends with an event, no dangling objective.

### G6. Consistency and retcon rules

- Once the player has seen a fact (appearance, name, relationship, a statement), it is stored as **observed canon** and the sim must not contradict it.
- If sim state diverges from observed canon (e.g., a person the player met is reported dead by an earlier-scheduled event), the sim resolves in favor of causality: produce the missing cause event (rescue, rumor was false, identical-sounding person) rather than rewriting history.
- Save/load: player-observed canon, L2 persisted data, and the sim snapshot share one version stamp.

### G7. Interfaces (language-agnostic)

Expose as a library API and optionally an IPC/RPC server:

```
sim.advance(ticks | until)           -> [EventRef] (narrative only by default)
sim.query.person(id)                 -> L1/L2 record
sim.query.where_is(id, tick)
sim.query.local_population(island, tick, lod)
sim.query.group(id)                  -> GroupSnapshot
sim.query.motivation(id, at?)
sim.query.why(event_id)
sim.query.rumors_at(island, tick, listener?)
sim.query.market(island)             -> prices, vendors, services
sim.query.who_wants(thing)
sim.query.quest_seeds(player_ctx)
sim.encounter.begin(spec_request)    -> EncounterSpec
sim.encounter.resolve(EncounterResult)
sim.player.act(Action)               -> events
sim.snapshot.save/load
sim.subscribe(filter, callback)      // push narrative events to the game
```
All responses carry the `tick` they are valid for. All writes are **events**.

**Acceptance:**
- **T-MEET-PERSIST:** meet an NPC, save, reload, meet again: identical L2 data, location logic consistent, remembered facts intact.
- **T-MEET-NOPOP:** 10,000 random player positions/times: no person appears at a location without a travel path, schedule entry, or arrival event.
- **T-MEET-WRITEBACK:** apply 100 random EncounterResults: all ledgers (silver, goods, headcount) balance; every change has an event with `source: "game"`.
- **T-MEET-REACT:** a scripted player action (e.g., kill a named captain in front of witnesses) produces memories for witnesses, opinion changes, a bounty/heat change, a succession event, and at least one NPC with a new want or vendetta toward the player within the configured window.
- **T-QUEST-FUND:** every quest reward is paid from a real account; ignored quests produce a sim-side resolution event.
- **T-AWAY-DIGEST:** the "while you were away" digest only includes events with a plausible information path to the player.

---

## Part H. Updated milestones (add to Base §18)

Add after the existing M12, in this order. Gate each on its acceptance tests.

| ID | Title | Contents |
|---|---|---|
| **P0** | Diagnostics and event classes | A0, A1 |
| **P1** | Lifecycle correctness | A2 |
| **P2** | Actor budgets and raiding | A3 |
| **P3** | Vendettas, bounty, heat, epithets, factions | A4, A5, A6 |
| **P4** | Motivation tracing | Part E (E1 to E5); wire all agent actions through DecisionTrace |
| **P5** | NPC pipeline | Part B (L0/L1/L2, generator, validators) |
| **P6** | Squads | Part C |
| **P7** | Person-level economy | Part D |
| **P8** | Text and dialogue layer | Part F; A7 chronicler rewrite on top of it |
| **P9** | Meeting layer | Part G |
| **P10** | Coverage and balance | A8 coverage report gating; tune; full regression |

**Order rationale:** diagnostics before changes; correctness before volume; tracing before NPC/dialogue work, since both depend on it; text layer before the meeting layer, because the game will consume it.

---

## Part I. Data to author for the new parts

- **Archetypes:** 25+ (tavern keeper, dock fixer, retired gunner, scarred bosun, corrupt harbor official, idealistic ensign, smuggler, cartographer, zealot, orphan runner...) each with trait priors, speech register, schedule template, typical wants, typical secrets.
- **Appearance tables:** by region/role/age/injury, with scar/tattoo/clothing catalogs and combination rules.
- **Speech registers and tics:** 8+ registers x 6+ tics each; line variants per topic for each register.
- **Reason-code phrase bank:** >= 3 variants per code per register.
- **Backstory fact templates:** 60+, each with the sim records they create (memories, opinions, debts).
- **Quest templates:** 40+, each tied to a want/dream kind with funding and consequence rules.
- **Services and vendor types** with price formulas.
- **Mood modifiers** per event kind and trait.

---

## Part J. Definition of done (v2)

Over 100 seeds x 10 years:
1. All Part A tests pass, and the Behavior Coverage table (A8) meets its medians.
2. Pick any narrative event: `why(event_id)` shows the actor's decision, the drivers, the beliefs used, and the truth gap, and the chronicler's sentence states only motives that appear in the drivers.
3. Pick any person in a port at any tick: they can be generated at L2, remembered, spoken to, and traced back to a history. Their stated wants match their motivation ledger, and their possessions come from real accounts.
4. A player can walk into a port, meet a squad, trade with a vendor, witness a feud, and leave. When they return after a time skip, the people they met have moved on through simulated causes, and the digest tells them only what they could have learned.
5. A scripted player action ripples through economy, opinions, bounty, politics, and rumors with logged causes, and nothing in the game ever writes to the sim except through events.
6. The same seed and the same player inputs reproduce the same world.
