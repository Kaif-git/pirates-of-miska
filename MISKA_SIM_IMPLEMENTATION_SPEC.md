# Pirates of Miska: World Simulation Implementation Spec

**Audience:** the coding agent implementing the simulation.
**Goal:** replace the current ship-centric, scheduler-driven sim with an agent-based, spatial, conserved-resource simulation in the spirit of Dwarf Fortress (depth, emergent history) and One Piece (named legends, crews, bounties, world-government pressure, set-piece battles).

---

## 0. How to use this document

1. Read sections 1-4 fully before writing code.
2. Implement milestones in the order of section 18. **Do not skip ahead.** Each milestone has acceptance criteria and must pass its tests before the next begins.
3. Every system must be **data-driven** (tunable constants and content live in data files, not code) and **deterministic** (same seed, same history, byte for byte).
4. When this spec is ambiguous, choose the simplest option that satisfies the invariants in section 17, document the decision in `DECISIONS.md`, and continue. Do not stop to ask unless an invariant would be violated.
5. Language/engine is not specified. Pseudocode below is language-agnostic. Use the project's existing language. If none exists, prefer a language with strong typing and fast iteration (Rust, TypeScript, C#, or Python with dataclasses and numpy for the headless sim core).
6. The simulation core must be **headless** (no rendering, no engine dependency), runnable from the CLI, and importable by the game later.

---

## 1. Design principles

1. **Individuals drive history.** The story is the log of what persistent individuals with traits, memories, and needs chose to do in a spatial world with limited resources. Nothing happens because a table rolled it.
2. **Everything has a cause.** Every event records `caused_by`. A viewer must be able to walk backwards from any event to its roots.
3. **Conservation.** Silver, goods, and people are never created or destroyed without a named source or sink event.
4. **Consequences persist.** Death, injury, debt, capture, scars, grudges, lost ships, burned ports, and broken treaties carry forward.
5. **Limited information.** Agents act on beliefs (rumors, stale reports, scouting), not on ground truth. Misjudgment creates drama.
6. **Simulate, then narrate.** The sim emits structured events. A separate chronicler turns them into prose. Never put flavor text in the sim.
7. **Test the sim as a system.** Long-run statistical tests are part of the deliverable (section 17).
8. **Scale by level of detail.** Important and nearby things run at full fidelity. Distant things run coarsely. Promotion and demotion between levels is explicit.

---

## 2. Baseline findings (what the old sim did wrong; regression targets)

These come from analysis of the previous 365-day, 302-event log. Each is a bug or design flaw that the new sim must not reproduce. Encode them as tests where marked **[TEST]**.

| # | Finding | Required behavior now |
|---|---|---|
| F1 | Events stopped between day 73 and 167; the second half of the year had only a repeating feud loop. **[TEST]** | Events occur continuously. No gap of more than 3 sim-days without at least one non-trivial event over a 10-year run (section 17, T-LIVENESS). |
| F2 | 50%+ of events occurred on days 1-2. | Event density is roughly stationary over time after a short warmup. |
| F3 | Every defeat was "withdrew with crew aboard." No deaths, sinkings, captures. | Full outcome set (section 10). Deaths, sinkings, captures, and ransoms occur at measurable rates. |
| F4 | Same ~9 ships/captains on day 365 as day 1. | Population turns over: births/recruits, deaths, retirements, new rookies, new factions. |
| F5 | Ship strength was a static scalar and unrelated to repair, crew, or supplies. | Strength is **computed** from components (section 8). |
| F6 | Upsets (41 beat 74) with no cause. | Upsets must trace to explicit factors (belief error, weather, surprise, captain skill, morale) recorded in the event. |
| F7 | Clockwork ritual: clash 11:00, repair 12:00, arrival 13:00 every 4 days. | No fixed schedules. Agents decide via utility scoring. **[TEST]** repeated-sequence detector. |
| F8 | Feud state machine looped insult, wary, friendly, allied 5 times, with no memory. **[TEST]** | Opinions have memory and decay curves. Repeating an insult after reconciliation must be *worse*, not identical. Feuds can escalate to violence. |
| F9 | Everything happened at one port. A place name contradicted its own text. | World is a graph of islands and routes (section 5). Location is a single source of truth on each entity. |
| F10 | Closed economy: one merchant, infinite liquidity, 2 iron in/out every hour. | Conserved multi-good economy (section 6). **[TEST]** conservation. |
| F11 | Flavor/data mismatch: repair titled "stirs the sea lanes"; template bug "Dain Reef claimed Dain Reef's crew invented the rival's shanty"; "Salt Finch 4" leaking internal IDs; two captains owning "Salt Finch". | Display names come from a name registry with uniqueness enforcement. Templates validated against actor roles (no self-reference). IDs never shown. **[TEST]** |
| F12 | Duplicate alliances sealed on consecutive days. | Treaty state is a single record per faction pair; transitions are validated against current state. **[TEST]** |
| F13 | "Cooperation against a shared enemy" cited with no such event. | Diplomacy changes must cite real event IDs via `caused_by`. **[TEST]** every cause ID resolves. |
| F14 | Scale: 4 factions with 1-2 ships each. | Initial world has 8-15 major named figures, 5-8 factions, 30-60 islands, hundreds of minor characters (LOD). |

---

## 3. Architecture overview

### 3.1 Module layout (suggested)

```
/sim
  /core
    clock.*            # tick, calendar, scheduling
    rng.*              # seeded, per-stream RNG
    ids.*              # stable ID generation
    eventlog.*         # append-only structured events
    world.*            # root state container, serialization
  /data                # all content & tuning (JSON/TOML/YAML)
    config.*           # global constants
    goods.*
    islands.*          # seed world
    factions.*
    traits.*
    names.*            # name pools, epithets, ship names
    hazards.*
    templates/         # chronicler text templates
  /systems
    geography.*        # graph, pathfinding, travel
    weather.*
    economy.*          # production, consumption, prices, merchants
    characters.*       # aging, skills, health, needs
    crews.*            # rosters, morale, wages, mutiny
    ships.*            # components, damage, repair, supplies
    ai.*               # utility scoring, planning, beliefs
    information.*      # rumors, scouting, knowledge propagation
    encounters.*       # detection, chase, engagement, outcomes
    factions.*         # politics, treaties, succession, heat
    memory.*           # memories, opinions, heritage
    director.*         # stagnation detection, pressure injection
    lod.*              # level of detail promotion/demotion
  /chronicler
    significance.*
    narrate.*
    summarize.*
    biography.*
    bounty.*
  /tools
    run_sim.*          # CLI: seed, years, output
    inspect.*          # query entities, trace causes
    batch_test.*       # N seeds x M years, metrics report
  /tests
```

### 3.2 Time model

- **Base tick = 1 hour of sim time** for tactical systems, but most agents do not act every tick.
- **Agent scheduling is event-driven:** each agent has `next_decision_tick`. They re-decide when idle, when interrupted (attacked, news, arrival, hunger, mutiny), or when the plan finishes.
- **Daily tick** (00:00): production/consumption, wages, aging checks, rumor propagation, weather update, director check.
- **Monthly tick:** faction politics, price smoothing, population change, bounty review.
- **Yearly tick:** aging, birthdays, heritage checks, historical summary.
- Travel is measured in hours/days. An agent on a route is "in transit" and only interruptible by encounter, weather, or news (via signal).

Use a **priority queue of (tick, event)** for agent wakeups. Do not iterate over every agent every tick.

### 3.3 Determinism and RNG

- One master seed. Derive **independent RNG streams** per system: `rng.stream("weather")`, `rng.stream("encounters")`, `rng.stream("ai:<agent_id>")`, etc. Adding a roll in one system must not change another system's outcomes.
- Never use wall-clock time, unordered-set iteration, or floating-point nondeterminism that varies by platform. Sort before iterating over any collection whose order affects results.
- **[TEST]** run the same seed twice, diff the full event log; it must be byte-identical.

### 3.4 State and serialization

- World state serializes to a single versioned snapshot (JSON or binary). Support save/load mid-run, and **[TEST]** that resuming from a snapshot yields the same future as an uninterrupted run.
- Entities are stored in tables keyed by stable ID (`char_000123`, `ship_000045`, `isle_017`, `fac_003`). Cross-references use IDs, never object pointers.
- Dead characters and sunk ships are **not deleted**; they move to an archive table (needed for heritage, history, biographies).

### 3.5 Event log

Append-only. Every state-changing action emits exactly one event.

```
Event {
  id: EventId (monotonic)
  tick: int
  type: EnumEventType             // e.g. SHIP_SUNK, CHARACTER_DIED, TREATY_SIGNED
  location: IslandId | RouteId | SeaCell
  actors: [{ role: string, entity_id }]    // e.g. {role:"attacker", id:...}
  targets: [{ role: string, entity_id }]
  data: map<string, value>        // structured, type-specific payload (no prose)
  caused_by: [EventId]            // may be empty only for root events (see below)
  causes_tags: [string]           // e.g. ["grudge","greed","storm","belief_error"]
  magnitude: float                // raw importance hint (0-1), set by the emitting system
}
```

Rules:
- `caused_by` may be empty only for **root events** (world generation, director injections, random natural events). Those must set `causes_tags` explaining why.
- No prose in `data`. Prose belongs to the chronicler.
- Events are immutable. Corrections are new events.
- Index the log by: entity, location, type, tick range, and cause graph (forward and backward).

---

## 4. Core data model

All numeric attributes are on a **0-100 scale unless noted**. Store raw values; derive modifiers in code.

### 4.1 Character

```
Character {
  id, display_name, epithet (nullable, earned), sex/gender (data-driven, optional)
  birth_tick, birthplace: IslandId, death_tick (nullable), cause_of_death_event
  heritage: { parents: [CharId|null], mentor: CharId|null, house: HouseId|null,
              inherited_grudges: [{target, intensity}], inherited_debts: [...],
              titles: [...] }
  home_faction: FactionId|null     // current allegiance
  location: { kind: island|route|sea, id, progress? }
  stats: { combat, seamanship, navigation, leadership, cunning, willpower, gunnery, medicine, trade }
  traits: { ambition, honor, greed, loyalty, cruelty, curiosity, pride, paranoia, boldness, sociability }
  values: ordered list of top 2-3 from [freedom, order, wealth, glory, kin, knowledge, vengeance]
  dream: Dream { kind, target_ref, progress, stated_tick }
  wants: [Want { kind, target_ref?, urgency(0-1), created_tick, source_event }]
  health: { hp, injuries: [Injury], chronic: [...], max_hp_from_age }
  bounty: { amount, issuer_faction, tick_set }
  reputation: map<RegionId|FactionId, { fame, infamy, respect, fear }>
  wealth: { silver, assets: [AssetId], debts: [Debt] }
  memory: [Memory]
  opinions: map<CharId, Opinion>
  role: captain | first_mate | navigator | gunner | cook | surgeon | shipwright | sailor | merchant | official | civilian | ...
  crew_id, ship_id (nullable)
  tier: ENUM { LEGEND, MAJOR, MINOR, BACKGROUND }   // LOD tier, see section 16
  flags: set<string>      // e.g. "has_devil_fruit_like_power", "marked_by_world_gov"
}
```

**Dream kinds** (data-driven, extend freely): `find_treasure(X)`, `become_title(Y)`, `destroy(entity)`, `restore_house(Z)`, `chart_region(R)`, `free_island(I)`, `become_wealthiest`, `defeat(character)`, `find_person(P)`, `protect(entity)`, `reach_legend_tier`.

**Want kinds:** `repair_ship`, `recruit(role)`, `pay_debt(D)`, `revenge(C)`, `resupply`, `find_safe_harbor`, `sell_cargo`, `heal`, `bribe(official)`, `escape(pursuer)`.

### 4.2 Memory and opinion

```
Memory {
  event_id, subject_ids: [CharId], valence: -1..1, intensity: 0..1,
  kind: ENUM { betrayed, saved, insulted, robbed, defeated_by, defeated, witnessed_death_of,
               traded_fairly, cheated, honored, humiliated, gifted, ... },
  tick, decay_rate (derived from traits + kind)
}

Opinion {   // holder's view of target
  trust, fear, respect, grievance, affection, debt   // each -100..100 (debt is signed)
  last_interaction_tick
  derived: recomputed from active memories + inherited baseline
}
```

Rules:
- Opinions are **derived** from memories (plus inherited baseline). Do not directly mutate opinion values from events; create a memory and let the recompute apply it.
- Memory intensity decays over time: `intensity(t) = base * exp(-decay_rate * age)`. Grievance from betrayal or killing a kinsman decays extremely slowly; insults decay faster. Traits modulate: high `pride`/`paranoia` slows decay of grievances; high `sociability` speeds recovery.
- **Repeat offenses compound.** If a new memory of the same kind and subject arrives while a prior one is still active, apply a multiplier (e.g., x1.5 per prior active memory, capped). This fixes F8.
- Cap each character's memory list (e.g., 200 active); prune lowest decayed-intensity first, but keep "core memories" (intensity > 0.8 or kinds in a protected list) permanently.

### 4.3 Ship

```
Ship {
  id, name (unique registry), class, built_tick, owner_char, owner_faction
  location, destination, route_progress
  hull: { max, current }, mast/rigging: { max, current }
  cannons: [Cannon { caliber, count, condition }], ammunition
  speed_base, maneuverability, cargo_capacity
  cargo: map<GoodId, qty>
  supplies: { food, water, rum?, powder, timber, cloth }   // goods that matter for operation
  upgrades: [...]
  crew_id
  condition_flags: set<string>
  history: [EventId]
}
```

### 4.4 Crew

```
Crew {
  id, name (jolly roger / banner name), captain_id, ship_ids, roster: [CharId],
  roles: map<role, CharId>, morale (0-100), cohesion (0-100), discipline (0-100),
  pay_policy: { share_rule: enum, wage_per_day }, arrears: silver,
  treasury: silver, ideology_tags: [...], founded_tick,
  faction_id (nullable), flag_symbol
}
```

### 4.5 Faction

```
Faction {
  id, name, kind: pirate_alliance | marine | merchant_league | kingdom | cult | outlaw_republic | ...
  leader_id, council: [CharId], succession_rule: enum
  ideology: { order, freedom, profit, glory, faith } (weights)
  doctrine: { prefers_raiding, prefers_trade, tolerates_slavery, honors_parley, ... }
  treasury: silver, income_sources: [...], expenses: [...]
  territory: [IslandId], strongholds: [IslandId], fleets: [FleetId]
  members: [CharId], relations: map<FactionId, FactionRelation>
  heat_policy (for government factions): bounty rules, patrol budget
}

FactionRelation {
  trust, fear, grievance, kinship, trade_dependence,    // each -100..100
  treaty: enum { none, nonaggression, trade_pact, alliance, vassalage, war },
  treaty_since_tick, treaty_cause_events: [EventId],
  derived_from: aggregated individual opinions among leaders and members + institutional events
}
```

### 4.4 Island / Port

```
Island {
  id, name, coords, region_id, climate, size
  population: int, population_classes: {laborers, artisans, merchants, sailors, officials, criminals}
  harbor_tier: 0..4 (affects repair, docking, shipbuilding, anchoring capacity)
  produces: map<GoodId, rate/day>, consumes: map<GoodId, rate/day per capita>
  stockpile: map<GoodId, qty>, prices: map<GoodId, float>, price_history (rolling)
  treasury: silver, ruler_faction, governor_id, garrison_strength, fortifications
  hazards: [HazardId], unrest (0-100), prosperity (0-100), famine_level (0-100)
  services: { repair, shipyard, tavern(recruit pool), black_market, bounty_office, infirmary }
  local_recruit_pool: [CharId of BACKGROUND tier available for hire]
}
```

---

## 5. World: geography and travel

### 5.1 Graph

- Islands are nodes. Routes are weighted edges: `{from, to, distance_days_at_base_speed, hazards:[...], patrol_density, season_modifiers, monster_risk, chokepoint_flag}`.
- 30-60 islands grouped into 5-8 **regions** (e.g., a safe coastal "Blue" region, contested mid-seas, a dangerous "Grand Line"-style region where legends concentrate, forgotten backwaters).
- Provide **at least 2 chokepoints** and at least one region reachable only via a dangerous route.
- Author the seed world in `data/islands.*`. Optionally support procedural generation behind a flag, but the hand-authored world is the default for tuning.

### 5.2 Travel

- A voyage is a path of route edges. Travel time = `distance / effective_speed`, where effective speed depends on ship speed, weather, supplies, damage, and crew seamanship.
- Ships in transit occupy a **sea cell** or position along a route. Encounters are only possible between ships on the same route segment/cell within overlapping time windows, or at the same island.
- Long voyages require supplies. If food/water hits zero: morale collapse, then illness and deaths, then mutiny risk.

### 5.3 Weather and seasons

- Per-region weather state updated daily: `calm, fair, rough, storm, hurricane, fog`. Seasonal tables drive probabilities.
- Weather modifies speed, detection range, hull damage risk, and encounter outcomes. Storms can sink ships, scatter fleets, and force unplanned port landings.

### 5.4 Hazards

Data-driven: sea monsters, whirlpools, reefs, calm belts, cursed waters. Each has a region/route, trigger probability, effect, and an optional avoidance skill check (navigation).

---

## 6. Economy (conserved)

### 6.1 Goods

`data/goods.*`: id, name, base_price, weight/volume, perishable?, category (food, material, luxury, arms, contraband, medicine, etc.), and what each is used for (ship upkeep, population consumption, shipbuilding).

Start with 12-20 goods. Required: food, water/rum, iron, timber, cloth/sailcloth, powder, medicine, luxury, spices, contraband, rare relics.

### 6.2 Islands as producers and consumers

Each day, per island:
1. **Produce:** `stockpile[g] += produces[g] * productivity` (productivity falls with unrest, famine, blockade, disaster).
2. **Consume:** population eats and uses goods; shortfall raises `famine_level` and `unrest`.
3. **Price update:** `price[g] = base_price[g] * clamp((target_stock / max(stock,epsilon))^k, min_mult, max_mult)`, smoothed toward new value (e.g., 20% per day) to avoid oscillation.
4. **Treasury:** island treasury gains from taxes and port fees, pays garrison and upkeep.

### 6.3 Trade

- Anyone (merchant, pirate, faction) trades via **order matching against island stock and price**. A purchase takes goods from island stock and silver from buyer, sale does the reverse. Islands have **finite treasury** (cannot pay unlimited silver). If an island can't afford it, it pays less or refuses.
- **There is no infinite merchant.** Brann Ash-style named merchants are individuals with finite stock and silver, subject to the same rules.
- Merchant AI scores trade routes: `expected_profit = (sell_price_at_B - buy_price_at_A) * qty - travel_cost - risk_cost`, where risk uses their *beliefs* about piracy and patrols along the route. They only haul when profitable.

### 6.4 Conservation rules

- **Silver sources:** mines/mints, farming/fishing/crafting output (valued at base_price when exported), faction taxes (a transfer, not a source), treasure discovery (finite, defined in data), government subsidy (explicit event, drawn from a faction treasury). Everything else is transfer.
- **Silver sinks:** consumption of goods, repair costs paid to a shipwright/island, wages (transfer to crew), bribes (transfer), destruction events (shipwreck cargo lost).
- Maintain a global ledger: `total_silver_in_system = Σ individuals + crews + factions + islands + ships' cargo value`. Track `Δ = sources - sinks` per day. **[TEST]** ledger balances within epsilon every day.
- Same for goods: per-good global quantity tracked against production minus consumption minus destruction.

### 6.5 Emergent trade-route rivalry

No special code. It emerges from: pirates raid profitable routes, merchants avoid risky routes, prices spike on blockaded islands, unrest rises, and unrest feeds recruitment into piracy. Verify with a metrics test that blockading a route changes prices within N days.

### 6.6 Ship upkeep

Ships consume supplies daily per crew member. Repair consumes timber, cloth, iron, plus silver labor at a port with `repair` service (quality depends on harbor tier and shipwright skill). Repair restores hull/rigging by a defined amount per unit time, not instantly, and takes real days.

---

## 7. Characters: lifecycle and growth

### 7.1 Generation

- **Character generator** takes (role, region, faction, tier, seed stream) and produces a coherent character: stats drawn from distributions shifted by role and heritage; traits drawn with correlated priors (e.g., high `honor` slightly lowers `cruelty`); values and dream chosen from trait-compatible options; backstory fields populated.
- Initial world: 8-15 `LEGEND/MAJOR` characters (hand-authored where you want specific flavor), 100-300 `MINOR`, and populations of `BACKGROUND` (statistical until needed).

### 7.2 Aging and death

- Characters age yearly. Past ~45-50, apply stat decay curves (combat/seamanship slowly decline; leadership/cunning may rise).
- Death causes: combat, shipwreck, disease, starvation, execution, old age, duel, accident, curse. Always emit `CHARACTER_DIED` with `cause_of_death_event` pointing to the root cause.
- Chance of natural death increases with age and chronic injury.
- **On death:** resolve succession (captaincy, faction leadership), inheritance (wealth, ships, grudges at reduced intensity), crew reaction (morale shock, possible dissolution), and memory events for everyone who knew them.

### 7.3 Skill growth

- Stats improve by **use** with diminishing returns and a cap by potential. Example: winning combats with a tougher opponent gives larger growth. Long voyages grow seamanship/navigation.
- Training with a mentor accelerates growth and creates a mentor/student heritage link.

### 7.4 Injuries

- Injuries have severity and permanent effects: lost eye (gunnery/detection penalty), lost limb, scars (intimidation bonus, `fear` modifiers), chronic illness. Treated by surgeon skill and medicine supplies.
- Injuries persist and are shown in biographies.

### 7.5 Reproduction and heritage

- Characters can have children (kept simple at first: probabilistic, requires settled time at an island, affects heritage). Children start as BACKGROUND and are tracked lightly.
- On coming of age, a child may: inherit a ship/crew/house, take up the parent's grudge (at ~60% intensity of the parent's active grievance toward the killer/target), seek vengeance, or reject the legacy (trait-dependent).
- Mentors/students: a mentor's reputation partially transfers; a mentor's enemies may distrust the student.

### 7.6 Retirement, replacement, rookies

- Old or wealthy characters may retire (settle on an island, become governor, open a tavern) when their dream is done or health fails.
- The world must **spawn new talent** to avoid the "same 9 forever" failure (F4): a rate of new rookies appears from islands with unrest, famine, or high piracy glamour, each with a dream and starting modest. Tune so the count of MAJOR+ characters stays roughly stable over decades (target band set in config).

---

## 8. Crews and ships

### 8.1 Computed ship strength (replaces the scalar)

Do **not** store "strength." Compute on demand:

```
combat_power(ship, crew, captain, context) =
    firepower(cannons, gunner_skill, ammo)
  + boarding_power(crew_fighters, crew_combat_avg, captain.combat)
  + durability(hull.current, rigging.current)
  + mobility(speed, maneuverability, seamanship, rigging.current, weather)
  + command_bonus(captain.leadership, crew.cohesion)
  - morale_penalty(crew.morale) - supply_penalty(supplies) - damage_penalties
```

Return a **breakdown** (struct), not just a number. The breakdown gets logged into encounter events so upsets are explainable.

**Estimated strength** (what an AI believes about another ship) = computed from *believed* data with noise proportional to information staleness and the observer's cunning/navigation. This is separate from true strength.

### 8.2 Crew roster and roles

- A crew has named members (MAJOR/MINOR characters for key roles, BACKGROUND members as statistical groups for ordinary sailors, e.g., "34 sailors, avg combat 38, avg loyalty 55").
- Key roles: first mate, navigator, gunner, cook, surgeon, shipwright. Missing roles impose penalties (no navigator: higher chance of getting lost; no surgeon: higher death rate; no cook: morale and supply efficiency drop).
- Crew size limits by ship class. Recruiting happens at ports with a tavern; recruit quality depends on the island's `local_recruit_pool` and the captain's reputation.

### 8.3 Morale, loyalty, and crew events

Morale (0-100) changes: +victory, +loot shares paid, +rest in port, +respected captain; -defeat, -missed pay, -death of popular member, -hunger, -captain cruelty, -long voyages.

Crew-level events (each emits a logged event with causes):
- **Desertion** (at port when morale and loyalty low).
- **Poaching** (rival captain recruits a member; creates grievance).
- **Wage arrears** compounding into mutiny risk.
- **Mutiny** (when morale < threshold AND a charismatic member with ambition exists; outcome contested by loyalty distribution; can replace captain, kill captain, or fail).
- **Splintering** (a lieutenant leaves with part of the crew to form a new crew).
- **Merging** (a defeated or leaderless crew pledges to a victor).

### 8.4 Ships as persistent assets

Ships are persistent objects that can be captured, scuttled, sold, inherited, renamed (rename logs an event), or lost. Ship names come from a registry that enforces uniqueness among living ships (fixes F11).

---

## 9. Decision-making (AI)

### 9.1 When agents decide

An agent decides when: idle, arrived at a destination, a need crosses a threshold, new significant information arrives (they saw an enemy, got news), or an event interrupts them. Decisions are not clocked. Each decision sets `next_decision_tick` or a wake-condition.

### 9.2 Action set

Each action has: preconditions, a cost model (time, supplies, silver, risk), an expected-outcome model (using the agent's *beliefs*), and an execution handler that emits events. Initial action list:

`trade_run`, `raid_merchant_route`, `hunt_rival(target)`, `escort(target)`, `patrol(region)` (marines), `blockade(island)`, `repair(at island)`, `resupply`, `recruit(role)`, `pursue_dream_step`, `explore(region)`, `seek_treasure(rumor)`, `flee`, `lay_low`, `parley(target)`, `propose_treaty(faction)`, `pay_debt`, `bribe_official`, `take_job (contract)`, `rest_in_port`, `retire`.

Actions are structured so adding a new one means adding a data entry plus a handler, without changing the scoring core.

### 9.3 Utility scoring

```
score(action) =
    Σ_over_wants( want.urgency * personality_alignment(traits, want, action) )
  + dream_alignment(dream, action) * dream_weight
  + expected_gain(action, beliefs) * (0.5 + greed/100)
  - expected_loss(action, beliefs) * (1.5 - boldness/100)
  + grudge_bonus(target) * (pride/100 + vengeance_value)
  - fear_penalty(target) * (1 - boldness/100)
  + faction_orders_bonus(action)         // obedience scaled by loyalty
  + opportunism(recent_news_bonus)
  + noise(trait-scaled, from agent's RNG stream)
```

Rules:
- Pick the action by **weighted random among top-K** (e.g. K=3, softmax with temperature by caution), not argmax, to avoid lockstep loops (F7).
- Apply **recency penalty** to actions the agent just did to prevent robotic repetition, scaled by `curiosity`.
- **Hard constraints** filter before scoring (e.g., can't raid without a seaworthy ship; can't trade without cargo or silver).
- Every decision emits an internal `DECISION` record (not necessarily in the public log) with the top candidates and score components. Keep this in a debug channel so designers can ask "why did X do that?"

### 9.3.1 Personality mapping (examples)

- High `greed`: weights expected loot, ransoms, trade margin.
- High `honor`: penalizes betrayal and attacking the helpless; increases parley acceptance; keeps treaties.
- High `cruelty`: raises chance of executing prisoners; affects crew morale negatively but raises enemy `fear`.
- High `ambition`: weights actions that raise rank, fame, territory.
- High `paranoia`: weights fortifying, spying, preemptive strikes, distrust of allies.
- High `curiosity`: weights exploration, unusual rumors.
- High `loyalty`: weights faction orders and protecting kin/crew.
- High `pride`: grudges decay slower, takes challenges, rejects ransom/parley when humiliated.

### 9.4 Beliefs and information

- Each agent has a **belief store**: `Belief { subject, attribute, value, confidence, source, observed_tick }`. Beliefs include other ships' positions, strength estimates, island prosperity, bounties, treaty states.
- Information sources: direct observation (within detection range), port gossip (taverns, bounty boards), merchant reports, faction briefings, spies, rumors.
- **Propagation:** each day, information spreads between agents co-located at ports/routes with probability scaled by `sociability`, with **degradation** (value noise, confidence decay) and occasional distortion/exaggeration (legend inflation). Ships far away carry stale beliefs.
- Beliefs age: `confidence` decays; low-confidence beliefs add noise to expected-outcome calculations.
- Agents may be **wrong**: attack a "weak" rival who was recently reinforced, flee from a hollow reputation, or walk into an ambush. These errors must be traceable in the event log (`causes_tags: ["belief_error"]`).

### 9.5 Planning depth

Start single-step with a short **plan queue** (voyage plan: go to A, trade, go to B, repair). Dreams generate multi-step plans via simple templated goal decomposition (e.g., `find_treasure(X)` becomes: acquire clue, travel to region, survive hazard, dig/take, return). Don't build a general planner unless needed; template plans are enough for depth and are easier to debug.

---

## 10. Encounters and outcomes

### 10.1 Encounter detection

Two parties can encounter when they are in the same sea cell/route segment at overlapping times, or both at an island. Detection chance depends on: weather, lookout skill, ship profile, time of day, and signal range. Detection can be asymmetric (one party sees the other first, which grants surprise).

### 10.2 Intent resolution

Each party decides a stance from AI (attack, avoid, parley, ignore, flee). If both avoid, no encounter. If any attacks and the other can't escape, combat resolution begins.

### 10.3 Multi-phase resolution

1. **Pursuit/escape phase:** compare mobility, weather, captain seamanship. Fleeing succeeds or fails; failed fleeing weakens the fleer.
2. **Gunnery phase:** exchange broadsides across N rounds. Damage to hull/rigging/crew based on firepower, range, gunner skill, weather. A crippled rigging prevents escape.
3. **Boarding phase** (if chosen/possible): crew combat, captain duels, morale checks, and special "legend" moves.
4. **Outcome determination.**

Variance comes from skill, weather, surprise, morale, and a bounded random term. The result must record in the event: both sides' computed strength breakdowns, beliefs held before the fight, and tags for decisive factors.

### 10.4 Outcomes (all must be reachable)

`one_side_flees`, `parley_ended_fight`, `surrender`, `ship_captured` (ship changes owner, crew choices follow), `ship_sunk` (cargo lost, survivors by chance, drowning, rescue), `captain_killed`, `captain_wounded`, `captain_captured` (ransom, imprisonment, execution, recruitment, release), `crew_defects`, `mutual_withdrawal`, `both_sunk_rare`, `fleet_scattered` (for fleets).

Mandatory consequences:
- **Casualties** to crews (named members can die; background members reduce count and quality).
- **Damage** persists (hull, rigging, cannons, supplies lost).
- **Loot moves real goods and silver** from loser to winner under conservation.
- **Memory creation** for every participant and witness, with valence and intensity by outcome and cruelty/honor of acts.
- **Bounty updates** and reputation shifts per region.
- **Follow-on wants** (revenge, repair, recruit replacements, pay ransom).

### 10.5 Prisoners, ransom, execution

Prisoners are held on a ship or at a stronghold. Captors choose via AI: ransom (to faction/crew/family, amount from wealth/value), execute, recruit, release, trade to marines (bounty). Choices create heavy memories (execution of a respected captain creates an enduring grievance and possible vengeance dream for followers and heirs).

### 10.6 Fleet and set-piece battles

- Fleets have commanders and a doctrine. A "set-piece battle" triggers when large forces (e.g., > N ships, or at least 2 LEGEND-tier figures on opposing sides) converge at a location.
- Resolve as multi-round **theater combat** with named duels at key points (legend vs legend), siege/blockade effects, reinforcements arriving by schedule, and morale cascades. Large rewards and losses are possible (kills of legends, collapsed factions).
- Emit a parent `BATTLE` event with child events for each duel, sinking, and capture, linked via `caused_by`.

---

## 11. Factions and politics

### 11.1 Individual vs institutional

Faction relations are **derived** from individuals (leaders' and members' opinions) plus institutional events (treaties, border incidents, trade volume). Do not store a single hand-edited relation ladder.

### 11.2 Multi-dimensional relations

`trust`, `fear`, `grievance`, `kinship`, `trade_dependence` each drift based on events, shared enemies, trade volume, and leader opinions. Treaty state is a **separate validated record**; transitions follow a state machine and require thresholds and a proposing event. Impossible to "seal an alliance" twice (F12).

Treaty states: `none, nonaggression, trade_pact, alliance, vassalage, war`. Transition rules and thresholds go in data. Treaties have **terms** (obligations) and **honor checks**: when a treaty obliges aid and the member declines, the breach creates memory and trust damage.

### 11.3 Faction decisions

Faction leaders decide via the same utility architecture at a coarser time scale (monthly): expand to an island, fortify, declare war, sue for peace, propose treaties, set bounties, appoint commanders, hire privateers. Factions pursue doctrine and ideology.

### 11.4 Succession and internal politics

- Leader death or incapacity triggers succession by rule (heir, election, strongest, council). Contested successions can split factions.
- Internal factions/cliques form around ambitious lieutenants; coups and defections are possible. Disgruntled members may leave with ships and crews.
- Marines: budget, patrol allocation, bounty policy, corruption level, rank and promotion politics, and conflicts between commanders (an ambitious admiral may disobey orders for glory).

### 11.5 World-government heat

A **heat** meter per character and per crew (and a global tension level):
- Heat rises with: notoriety, successful raids on government assets, killing officials, high bounty, alliances with other high-heat pirates, hitting protected trade routes.
- Heat triggers escalating government responses: patrols near the character's usual routes, bounty increases, dedicated hunter assignments, fleet deployments, and (at the extreme) set-piece operations.
- Heat decays slowly when lying low, bribing officials, or earning official titles (privateering).
- The government's response budget is finite (conserved). Heavy investment in one hunter weakens patrol elsewhere, which empowers other pirates.

### 11.6 Territory

Islands have a ruling faction, a garrison, and local loyalty. Conquest requires defeating the garrison and holding it against counterattack. Occupation affects production, unrest, and the island's alignment. Territory changes are major events and ripple into trade and relations.

---

## 12. Memory, opinions, causality, heritage (integration)

Feud mechanics in the new model (replaces F8's loop):

1. Dain insults Edda in public. Emit `INSULT` event. Edda gets a `humiliated` memory (valence negative, intensity from her `pride` and the audience size).
2. Her opinion of Dain updates: `grievance` up, `respect` down.
3. Her utility for `hunt_rival(Dain)` rises by `grudge_bonus`. She may prefer targets closer to Dain's trade routes, or allies of Dain.
4. If she acts (raids a Jackal convoy), the raid event has `caused_by: [INSULT event]` and tags `["grudge"]`.
5. Dain gets memories of being robbed. His grievance toward Edda rises, with the **repeat-offense multiplier** if he'd previously offended her. Escalation or a third party's intervention follows.
6. Reconciliation requires explicit events (apology gift, shared victory against an enemy, mediation by a respected party). Reconciliation reduces active grievance but leaves a residual scar; the next insult starts from a worse baseline.

**Heritage:** at a character's death, create `inherited_grudges` for children/protégés for grievances above a threshold, at ~60% intensity. Inherited grievances can also be passed from houses (factions of kin).

**Causality tooling:** `tools/inspect` must support `why <event_id>` (walk `caused_by` recursively) and `consequences <event_id>` (forward graph). Use these for debugging and for the chronicler.

---

## 13. Events catalogue (minimum set)

Define as an enum with schemas in `data/event_types.*`. Minimum list:

World/natural: `STORM`, `HURRICANE`, `MONSTER_SIGHTING`, `PLAGUE`, `HARVEST_FAILURE`, `TREASURE_RUMOR`, `DISCOVERY`, `SHIPWRECK`.
Economic: `TRADE_COMPLETED`, `PRICE_SPIKE`, `FAMINE`, `BLOCKADE_START/END`, `PORT_BURNED`, `SMUGGLING_BUSTED`.
Personal: `BIRTH`, `COMING_OF_AGE`, `CHARACTER_DIED`, `INJURY`, `DUEL`, `INSULT`, `GIFT`, `OATH`, `RETIREMENT`, `DREAM_ACHIEVED`, `DREAM_CHANGED`.
Crew: `RECRUITED`, `DESERTION`, `POACHED`, `MUTINY`, `CREW_SPLIT`, `CREW_MERGED`, `CAPTAIN_CHANGED`, `WAGES_UNPAID`.
Conflict: `ENCOUNTER`, `SHIP_CAPTURED`, `SHIP_SUNK`, `CAPTAIN_CAPTURED`, `RANSOM_PAID`, `EXECUTION`, `BATTLE`, `SIEGE`, `ISLAND_CONQUERED`.
Political: `TREATY_PROPOSED/SIGNED/BROKEN`, `WAR_DECLARED`, `PEACE`, `SUCCESSION`, `COUP`, `FACTION_FOUNDED/DISSOLVED`, `BOUNTY_SET/RAISED/CLAIMED`, `HEAT_THRESHOLD_CROSSED`.
Director: `ROOKIE_SPAWNED`, `RUMOR_INJECTED`, `ENVIRONMENT_PRESSURE`.

Each event type defines: required actors/targets, required `data` fields, default `magnitude`, and which systems consume it (memory, reputation, economy, politics).

---

## 14. Random events and the story director

### 14.1 System-driven randomness

Random events are generated by **systems with state**, not a global table:
- Weather system: storms with paths affecting routes and ports.
- Disease: spreads through ports and ships with crowding and poor supplies.
- Harvest: yearly yield variance by island; failures trigger famine chains.
- Monsters/hazards: spawn and move along regions.
- Treasure and rumor: finite items placed in the world with clues that propagate as rumors.

Every random event has a location, affected entities, and downstream effects through normal systems.

### 14.2 Director (light touch)

The director runs daily/monthly and monitors **health metrics**:
- Time since last notable conflict.
- Faction power concentration (e.g., Gini / top-faction share).
- Count of MAJOR+ characters.
- Trade volume.
- Player-region pressure (if a player is present).

When metrics leave target bands, it injects **conditions**, not outcomes: spawn a rising rookie with a vendetta, trigger a coup in a stagnant faction, seed a treasure rumor in a quiet region, create a famine in an overly prosperous port, offer a faction a casus belli. Injected events are root events with `causes_tags: ["director:<reason>"]` and must be visible in the log (no hidden hand).

Configurable intensity; can be disabled for purist runs. Test with the director both on and off.

---

## 15. Chronicler (separate from simulation)

Input: the structured event log plus entity state. Output: prose and artifacts. **The chronicler never mutates the simulation.**

### 15.1 Significance scoring

```
significance(event) = magnitude
  * fame_weight(actors)             // legends matter more
  * rarity(type)                    // first-ever, rare outcomes
  * consequence_reach(event)        // size of forward causal subgraph
  * emotional_weight(memories created)
  * novelty (distance from recent similar events)
```

Events below a threshold are **collapsed into summaries** ("Between spring and autumn, Widow's Wake completed 43 trade runs on the Cinder-Brine route, netting 2,100 silver.").

### 15.2 Narration

- Template library in `data/templates/` with variants per event type, tone, and context tags; templates declare required slots (actor, target, place, cause) and are **validated at load** (no self-reference, no missing slot, no raw IDs).
- Causal narration: use `caused_by` chains to add clauses ("Because Edda lost her navigator at Brine Reach, she turned against the Jackals...").
- Epithets are **earned**: derived from deeds and stats (e.g., a captain with 5 captured warships, "the Hull-Breaker"; many kills with a signature tactic; a famous betrayal, "Oathbreaker"). Epithets are assigned by the chronicler/reputation system and are stored on the character, with the event that granted them.
- Output forms: running chronicle, tavern rumors (intentionally distorted), bounty posters (name, epithet, crimes, amount, sketch tags), per-character biography, faction history, "legends" page (ranked by fame), yearly almanac, obituary, and a "this week in Miska" digest.

### 15.3 Name registry

A central registry guarantees unique display names for living ships and characters, supports "Jr./II" naming only via explicit lineage (not internal counters), and never leaks internal IDs into text.

---

## 16. Level of detail (LOD) and performance

### 16.1 Tiers

- `LEGEND`/`MAJOR`: full simulation always, everywhere.
- `MINOR`: full simulation near the player or major events; coarse otherwise.
- `BACKGROUND`: statistical populations per island/crew (counts and averages). Promoted to MINOR when they do something notable (win a fight, earn a bounty, inherit a ship).

### 16.2 Coarse simulation

Distant regions advance on a coarser clock (e.g., daily or weekly) with aggregate outcomes (trade flows, patrol coverage, random incident rates) that match full-fidelity statistics. Verify with a test that coarse vs. fine simulation of the same region gives similar aggregate metrics within tolerance.

### 16.3 Promotion and demotion

Promotion constructs full objects from stats and context; demotion compresses back to aggregates but **keeps a record** (events, name, key traits) so they can reappear coherently.

### 16.4 Performance targets (initial)

- 10 sim-years of the default world headless in under 5 minutes on a typical dev machine (tune later).
- Memory bounded: archives compress old events (store full events for N years, summaries after, retaining all events for legends).
- Profile every milestone. Use the event/priority queue so cost scales with activity, not population.

---

## 17. Testing and metrics

### 17.1 Unit/integration tests

- **RNG isolation:** adding rolls to one stream doesn't change another's results.
- **Determinism:** same seed gives identical log. Snapshot/resume equivalence.
- **Conservation (T-CONSERVE):** global silver and per-good ledgers balance each day (epsilon).
- **Causality (T-CAUSE):** every `caused_by` ID exists and precedes the event; non-root events have at least one cause.
- **Treaty state machine (T-TREATY):** no duplicate or invalid transitions.
- **Name registry (T-NAMES):** unique living names; no template renders contain raw IDs, self-references, or unfilled slots.
- **Location (T-LOC):** every entity has exactly one location; text-visible location matches state.
- **Opinion derivation (T-OPINION):** opinion equals recompute from memories; repeat offenses compound.
- **Ship strength (T-STRENGTH):** strength changes when hull, crew, morale, or supplies change.

### 17.2 Batch statistical tests (`tools/batch_test`)

Run **100 seeds x 10 years** (configurable). Compute and assert bands (set initial bands after the first baseline, then fail on regression):

| Test | Metric | Pass condition (initial, tune) |
|---|---|---|
| T-LIVENESS | Max gap with no non-trivial event | <= 3 days in all seeds (F1) |
| T-DENSITY | Events/month variance over time | no month with < 25% of the median (F2) |
| T-TURNOVER | Deaths/year among MAJOR+; new MAJOR+ per year | non-zero, within configured band |
| T-POP | Count of living MAJOR+ over time | stays within [min, max]; no extinction, no explosion |
| T-FACTION | Faction count; top faction power share | no permanent monopoly; at least some founding/dissolution over 10 years |
| T-WEALTH | Gini of wealth among captains | within configured band; trend not monotonic to 1.0 |
| T-OUTCOMES | Distribution of encounter outcomes | all outcome types occur; `mutual_withdrawal`/`flee` < configured share (F3) |
| T-DIVERSITY | Event-type entropy per year | above threshold (F7) |
| T-LOOP | Repeated-sequence detector (n-gram of actor/type) | no sequence repeats > K times without an escalation or change (F7/F8) |
| T-CONSERVE | Coin and goods conservation | exact within epsilon (F10) |
| T-UPSET | Share of fights where weaker-estimated side wins | has plausible band (e.g., 10-30%) and every upset has a tagged cause (F6) |
| T-FEUD | Grudge to action conversion | a measurable fraction of high-grievance pairs lead to a hostile act within N days |
| T-HEAT | High-heat characters draw government response | response events follow threshold crossings |
| T-CRASH | No exceptions, NaNs, or negative stocks across all seeds | zero |

Emit a report (`reports/batch_<timestamp>.md` plus JSON): per-seed summary, distributions, failing tests, and sample "interesting stories" (top-significance causal chains).

### 17.3 Designer tooling

- `inspect entity <id>` (state, history, opinions, memories).
- `inspect why <event_id>`; `inspect consequences <event_id>`.
- `inspect decision <char_id> <tick>` (score breakdown).
- `inspect map` (ASCII or simple export of islands, routes, ship positions).
- `export chronicle --years 10 --min-significance X`.
- Timeline plots (CSV/PNG): population of MAJOR+, faction power, prices of key goods, global silver.

---

## 18. Milestones (implement in order)

Each milestone: **Tasks**, **Deliverables**, **Acceptance criteria**. Do not begin the next milestone until the current one's acceptance tests pass in the batch runner.

### M0: Foundations
**Tasks:** project skeleton; clock and priority-queue scheduler; seeded RNG streams; ID generation; append-only event log with the schema in 3.5; world snapshot save/load; CLI `run_sim`; config loader; `inspect` stub; test harness and batch runner skeleton.
**Acceptance:** determinism and snapshot-resume tests pass; event log indexed by entity/type/tick; `caused_by` validator works.

### M1: Fix-and-contain the old sim (baseline harness)
**Tasks:** if the old sim code exists, instrument it with the new event schema, reproduce F1-F14 as failing tests, and keep it as a comparison baseline. If the old code is not worth keeping, write the failing tests against the new interfaces so they fail until the relevant systems exist.
**Acceptance:** the failing-test list is documented; each finding F1-F14 maps to a named test.

### M2: World graph, travel, weather
**Tasks:** island/route data model; seed world (>= 30 islands, 5 regions, chokepoints); pathfinding; travel with transit state; weather per region; hazards; `inspect map`.
**Acceptance:** ships (dummy agents) travel between islands taking realistic time; weather changes travel times; T-LOC passes; no teleporting.

### M3: Conserved economy
**Tasks:** goods data; island production/consumption/prices/treasury; order-matching trade; merchant agents with profit-driven route choice and finite stock/silver; ledger and conservation checks; famine/unrest feedback.
**Acceptance:** T-CONSERVE passes over 10 years x 20 seeds; blockade test (remove a route) raises destination prices and unrest within N days; no infinite-liquidity actors; merchant trade volume stable (not collapsing or exploding).

### M4: Characters, crews, ships (computed strength)
**Tasks:** character schema and generator; traits/values/dreams/wants; aging, death, injuries, skill growth; crew rosters, roles, morale, wages; ship components and supplies; computed strength breakdown; estimated strength with noise; name registry.
**Acceptance:** T-STRENGTH, T-NAMES pass; characters age and die in long runs; crews lose/gain members; ship strength responds to damage/crew/supplies; supply exhaustion causes consequences.

### M5: Utility AI with limited information
**Tasks:** action framework; scoring; top-K softmax with recency penalty; belief store; information propagation with decay and distortion; voyage plan queue; decision debug channel; dream-driven plan templates.
**Acceptance:** agents choose varied actions; T-LOOP and T-DIVERSITY pass without encounters yet; belief-error cases occur and are traceable; `inspect decision` works; T-LIVENESS passes for non-combat activity.

### M6: Encounters with permanent consequences
**Tasks:** detection; multi-phase resolution; full outcome set; casualties and damage persistence; loot with conservation; prisoners/ransom/execution; captures and sinkings; bounty and reputation updates; memory creation hooks.
**Acceptance:** T-OUTCOMES, T-UPSET pass; all outcomes occur; ships and characters actually leave the world; replacements spawn (T-POP); loot conservation holds.

### M7: Memory, opinions, feuds, heritage
**Tasks:** memory store and decay; opinion derivation; repeat-offense compounding; grudge-driven utility; reconciliation events; heritage and inherited grudges; mentor/student links; vengeance dreams.
**Acceptance:** T-OPINION, T-FEUD pass; scripted scenario (insult leads to raid leads to escalation) occurs organically in test seeds; reconciliations leave residual scars; heir vengeance appears in multi-decade runs.

### M8: Factions, politics, bounty/heat, Marine politics
**Tasks:** faction objects and leaders; derived multi-dimensional relations; treaty state machine with terms/breaches; faction-level decisions; succession and internal politics; territory and conquest; heat and government response with finite budget; marine commander politics.
**Acceptance:** T-TREATY, T-FACTION, T-HEAT pass; alliances/betrayals/wars occur with logged causes; at least one faction founded and one dissolved in a typical 10-year run; no duplicate alliance events.

### M9: Director and random-event systems
**Tasks:** weather-linked disasters, disease, harvest, monsters, treasure/rumor system; director with metrics and bounded interventions; director on/off toggle.
**Acceptance:** stagnation scenarios trigger interventions that appear in the log with `director:` tags; T-DENSITY and T-LIVENESS pass in director-off runs for at least 80% of seeds (the director shouldn't be a crutch); on-runs pass 100%.

### M10: Chronicler
**Tasks:** significance scoring; collapse/summarize; template library with validation; causal narration; epithets; bounty posters, rumors, biographies, almanac, legends page; name registry integration.
**Acceptance:** T-NAMES passes on all generated text; a 10-year chronicle reads as a coherent story with causal links and no repeated filler; sample outputs reviewed by a human (store in `/reports/samples`).

### M11: Set-piece battles and legends
**Tasks:** fleet command model; battle resolver with named duels; reinforcement and morale cascades; legend-tier special traits/moves (data-driven); aftermath rules (faction collapse, bounty spikes, power vacuums).
**Acceptance:** at least one set-piece battle occurs in most 10-year runs involving 2+ legends or 10+ ships; its causal chain is traceable; aftermath produces observable political shifts.

### M12: LOD and scale
**Tasks:** tiers, promotion/demotion, coarse regional simulation, archive compression, performance profiling and optimization.
**Acceptance:** 300+ named characters and 40+ islands over 10 years within the performance target; coarse/fine aggregate equivalence test passes; promoted characters appear coherent (history retained).

### M13: Balance pass and handoff
**Tasks:** tune config bands using batch reports; final regression suite; documentation (architecture, data authoring guide, event catalogue, tuning guide); API for the game to consume (query state, subscribe to events, inject player actions as events).
**Acceptance:** full batch suite green (100 seeds x 10 years); docs complete; a sample "season of history" export reads like a story.

---

## 19. Content authoring (data you must provide)

Create starter data with enough variety to avoid repetition:

- **Names:** 300+ given names, 150+ surnames/house names, 100+ ship name stems with modifiers, 50+ faction names, 60 island names.
- **Epithet bank:** 80+ epithets keyed to deed patterns.
- **Insult/slight bank:** 60+ petty-feud triggers with role-aware slots (insulter, target, object), each with `pride` thresholds and severity. Validate that templates never reuse the same actor in both roles.
- **Trait archetypes:** 15-20 archetype presets (e.g., "honorable duelist", "greedy smuggler", "idealistic revolutionary", "corrupt admiral", "reckless rookie", "paranoid warlord") to seed coherent generation.
- **Dream pool:** 40+ dream templates with plan decomposition.
- **Hazards, goods, ship classes, cannon types, injuries, diseases.**
- **Legend special traits:** 10-20 rare "gimmick" traits (signature technique, cursed relic, secret knowledge) with concrete mechanical effects, not just flavor.
- **Seed world:** hand-author 8-15 named legends/majors with distinct personalities, histories, heritage links, and starting tensions that will create conflict naturally, plus 5-8 factions with mutually conflicting doctrines.

All of this in data files; adding content must not require code changes.

---

## 20. Configuration and tuning

Single `config` with sections: `time`, `economy`, `characters`, `crews`, `ai`, `encounters`, `factions`, `heat`, `director`, `chronicler`, `lod`, `tests`. Every constant referenced in this spec (decay rates, multipliers, thresholds, K for top-K, temperature, caps) must live here with a comment on its meaning and safe range. Provide named **presets** (`default`, `harsh_world`, `peaceful_world`, `fast_history`) and expose a seed + preset on the CLI.

---

## 21. Pitfalls and rules of thumb for the implementer

- **Do not store derived values** (ship strength, opinions, faction relations). Derive them, or cache with invalidation.
- **No global event tables rolling events.** If you're about to write `if rand() < 0.01: emit(...)` in a scheduler, stop: put it in a system with state and a location.
- **Every random choice gets a logged reason tag** when it influences an outcome.
- **No hidden infinite sources.** Any code path that adds silver/goods/people must emit a source event.
- **Beware lockstep:** use jittered `next_decision_tick`, softmax selection, and recency penalties.
- **Avoid death spirals and runaway winners:** add soft caps (diminishing returns on wealth/power, upkeep scaling with size, government pressure on the famous), and verify with T-WEALTH and T-FACTION.
- **Keep prose out of the sim.** If you find yourself writing a sentence in `/systems`, move it to a template.
- **Add the test when you find the bug.** Every bug fixed adds a regression test.
- **Profile early.** Event-driven scheduling is the main guard against O(agents x ticks).
- **Log decisions for debuggability,** but keep them in a separate, optional, compressible channel.

---

## 22. Definition of done

The simulation is done when, over 100 seeds x 10 years:
1. All tests in section 17 pass.
2. A human reading the exported chronicle for any seed can answer "why did that happen?" for any major event by following causes.
3. Within a 10-year run there are named legends who rise and fall, at least one feud that escalates and pays off across generations, at least one betrayal, one mutiny or crew split, one conquest or loss of an island, one marine-vs-pirate set-piece, and one faction founding or collapse, without any of it being scripted.
4. The same seed always produces the same history, and different seeds produce visibly different histories.
5. The game can consume the sim via the documented API: query world state, subscribe to events, and inject player actions that the world reacts to through the same systems.
