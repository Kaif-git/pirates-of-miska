# Simulation implementation decisions

Record invariant-safe choices made where `MISKA_SIM_IMPLEMENTATION_SPEC.md` is
ambiguous. Keep decisions concise and link them to the affected milestone.

## M0 — package boundary

- The project already uses Python packages. The new core lives in a top-level
  `sim/` package, separate from `pirates/` (game integration) and `tales_sim/`
  (reusable game systems). It must not import the renderer, tactical engine,
  or game package; later integration will consume it from the outside.
- Follow the suggested spec layout with `sim/core`, `sim/systems`,
  `sim/chronicler`, `sim/tools`, and JSON content under `sim/data`.
- Use `python -m sim.tools.<command>` for future CLI entry points so they are
  runnable from the repository root without adding a dependency or changing
  the existing game launcher.
- The default config records the spec's one-hour tick and 24-hour/365-day
  calendar as data. This defines a calendar, not a scheduler policy.
- Scheduler order is `(tick, integer priority, insertion sequence)`; equal-key
  events preserve insertion order through snapshots. Named RNG streams use
  BLAKE2-derived counter draws, with each stream's draw counter serialized.
- Root event kinds are explicitly listed in config and must explain themselves
  with `causes_tags`; all non-root events reference earlier event IDs. Snapshot
  format is canonical JSON with a schema version and includes pending events,
  stream counters, ID counters, active/archive tables, and the structured log.

## M1 — legacy baseline scope

- The spec's cited historical 365-day/302-event log is not in the repository.
  Rather than fabricate it or rewrite live gameplay for the new schema, capture
  the current generated NPC layer as a comparison baseline using a fixed seed
  and one-year prehistory. `sim/data/legacy_baseline.json` records the run and
  its limitations; society news was intercepted before the existing 120-row
  cap so the metrics cover the full generated year.
- The captured current system has a 34-day gap between nontrivial-event days,
  4 organizations, 8 vessels, and 6 sea nodes. F1's 3-day liveness and F14's
  5–8 factions/30–60 islands/hundreds of minor people therefore remain valid
  new-simulation targets. Other old findings must be evaluated against their
  concrete current-system equivalents, not assumed from the missing log.
- The fixed-seed capture also produced only victory/disengagement clashes (no
  deaths or sinkings), 0 structured parent-event IDs, and repeated alliance
  news for two organization pairs. Repeated news is not proof of duplicate
  active treaties: the final old-system state held one expired agreement.
  F1–F14 each have a named future behavioral case and owner milestone in
  `sim/data/regression_targets.json`; `tests/test_sim_regression_map.py`
  enforces complete/unique coverage and records concrete baseline failures.

## M2 — default geography

- Use a deterministic, hand-authored 30-island/5-region seed map in
  `sim/data/islands.json`; procedural generation is deferred. Route edges are
  undirected at this stage, and `distance_days_at_base_speed` is the canonical
  authored travel weight. A region marked `dangerous_route_only` is entered
  only by a flagged hazardous chokepoint; its internal routes do not count as
  external access.
- Hazard records are reusable data templates referenced by route IDs. The
  initial data-only stage was followed by M2 route resolution, which applies
  trigger/avoidance, travel-delay, and hull-condition effects.
- Weather is sampled per region at each day boundary from a seasonal calendar
  and regional weather regime, with a per-region named RNG stream and an
  integer persistence bonus. A state transition emits a structured root
  `NATURAL_EVENT`; at a shared tick it is processed before scheduled arrivals.
  Snapshots persist the weather state and reject changed map/weather content
  hashes rather than silently changing a resumed world.
- Route path costs and voyage durations use integer time units/permille
  modifiers; JSON decimal route modifiers are converted exactly with
  `Decimal`. Weather, navigation, hull condition, regional regime modifiers,
  and seeded route-hazard outcomes influence expected travel duration.
- Hazard checks resolve when a voyage is planned; triggered hazards are child
  events of `VOYAGE_STARTED` and can delay or damage the vessel. Supplies and
  encounter effects remain deferred to their owning M4/M6 systems. In transit,
  the route/progress in `TravelSystem.transits` is the canonical location;
  active entity records omit a stale island `location`, and callers use
  `WorldState.location_of()`/`validate_locations()`.

## M3 — economy units and endowments

- Economy quantities are integer milli-units, prices are integer silver-milli
  per unit, and silver accounts are integer whole silver. Goods are transferred
  with ceiling-rounded whole-silver prices; partial fills are bounded by stock,
  hold capacity, buyer cash, and island treasury. This keeps transfers exact and
  avoids floating-point drift.
- Island residents have one aggregate `population_silver` wallet until M4
  individual wealth exists. World-generation events disclose starting resident
  and island treasury/stock endowments; daily taxes debit that finite wallet,
  and garrison/port upkeep move silver to aggregate recipient wallets. Named
  merchants receive finite, explicitly evented endowments and finite cargo
  capacity. These aggregate wallets are included in M3's global silver ledger.
- Population and goods consumption are measured against daily per-1000 rates;
  goods production, unmet essential demand, famine, unrest, prosperity, and
  integer-smoothed scarcity prices update at each 00:00 economy step. The price
  elasticity is a square-root stock ratio, evaluated in integer fixed-point.
- Merchant plans compare current finite market quotes along deterministic
  shortest routes, subtracting travel cost and route-specific piracy/patrol risk
  beliefs. Beliefs affect whether a route is profitable; travel then resolves
  weather and physical hazards independently. A successful arrival attempts a
  finite sale against the destination's treasury.
- The economy ledger includes island treasuries, aggregate resident wallets,
  garrison/port-service recipient wallets, merchant purses, island stocks, and
  merchant cargo. World-generation endowments define opening balances;
  merchant creation is an explicit source event, taxes/upkeep/trade are
  transfers, and daily population consumption is a goods sink. Each day stores
  cumulative account totals and links per-island production/consumption back to
  their structured events. Future systems must register new silver/goods
  sources or sinks through a causal event before changing those totals.
- Resident taxation is a transfer from the finite aggregate resident wallet,
  not a population-scaled mint. Merchants reassess route plans at most weekly;
  when loaded routes are unprofitable, they may reposition empty toward a
  profitable exporter, with that leg's cost and risk included in the plan.
  Three starting merchants are defined in economy data rather than created by
  an unbounded runtime actor source.
- Local market buyers can spend the island's finite treasury, resident, garrison,
  and port-service balances. A sale debits the accounts in a fixed order and
  records the exact account breakdown on `MARKET_TRADE`; this makes resident
  purchasing power and already-paid service budgets usable without creating
  silver, while retaining per-account conservation.
- Initial batch stability band compares final-year trade quantity with year one:
  every simulated year must have trade, and the final/first ratio must remain
  between 0.1x and 10x. This is a first acceptance band, exposed alongside the
  full annual series so later tuning can use observed multi-seed results.

## M4 — character generation and lifecycle

- Seed 12 MAJOR and 100 MINOR characters from a dedicated data catalog, using
  per-character RNG streams and a deterministic first-free given/family-name
  scan. The catalog's age and stat distributions are kept separate from
  individual records; snapshot restore binds the current catalog digest.
- Annual birthdays update age and age-sensitive combat/gunnery/seamanship stats;
  old-age risk is a data-tuned per-birthday roll. Dead characters move from the
  active table to the archive with their death event and full identity intact.
- Replacement is deliberately thresholded: only when named MAJOR+ population
  falls below eight are rookies spawned back toward twelve (capped at fifteen).
  This avoids manufacturing replacement actors for every isolated death.
- Parent stats and traits shift a child's generated values by one quarter of the
  parent's deviation from the population midpoint; explicitly recorded house,
  mentor, grudge, debt, and title heritage is retained. Detailed inherited
  memory/feud behavior remains owned by M7.
- Crews currently bind to the three finite starting merchant vessels, with a
  named eight-character roster selected from generated characters. Provisioning
  transfers existing island stock into separate ship stores; daily consumption
  is a ledger sink linked to `SHIP_SUPPLIES_CONSUMED`. No new goods are minted.
- Crew startup treasuries are finite transfers from merchant purses. Daily
  integer wages transfer to member wallets, with any shortfall recorded as
  compounding arrears. Crew wallets are included in global silver totals and
  checked against both character wealth and payroll event history.
- Ship combat power is an on-demand integer breakdown, not persistent state;
  damage, morale, ammo, roster skill, and essential supplies feed components.
  Belief estimates use observer cunning/navigation and observation staleness to
  bound deterministic RNG noise without exposing true strength. Living character and ship names share a
  whitespace-normalized, case-insensitive registry; renames emit causal events.

## M5 — action scoring foundation

- Keep action definitions in `sim/data/actions.json`; precondition and handler
  identifiers are validated data, while one generic scorer combines want
  urgency/alignment, dream alignment, gain/loss, personality, cost, recency, and
  per-agent random noise. The initial executable slice is dream pursuit, crew
  rest, and profitable merchant trade; expand actions in follow-up M5 work.
- Candidate filtering is a hard precondition pass before scoring. Selection is
  weighted among the top K using integer inverse-score-gap weights rather than
  floating-point softmax, preserving cross-platform deterministic replay while
  still allowing lower-ranked candidates to win.
- Decision diagnostics are private bounded snapshot state, not public `DECISION`
  events. Executed actions emit causal `AGENT_ACTION` and outcome events. An
  action's modeled time cost sets the agent's next eligible decision tick; it
  does not directly advance the shared world clock.
- Top-K temperature is deliberately broad enough for lower-ranked feasible
  actions to appear across seeded captains; a repeated-sequence penalty detects
  cycles up to three actions after two prior repetitions. This complements the
  shorter recency penalty rather than replacing it.
- Beliefs are keyed per observer by `(subject_id, attribute)` and retain
  confidence, observed tick, source agent/event, and direct-vs-rumor provenance.
  Confidence decays daily; same-location agents share through deterministic
  pair/subject RNG streams, with sociability-scaled probability, confidence loss,
  and bounded numeric exaggeration. Each day's senders' beliefs are snapshotted
  before propagation so rumors cannot cascade within one day. Rumor events cite
  both the originating evidence and the sender's latest event.
- Dream templates instantiate a persistent goal plan with an ordered step list;
  advancing steps and completing a plan emits causal records. Captains can also
  hold a bounded 1–8-step voyage queue (`travel`, `trade`, `repair`) with optional
  island targets; the queue validates each reported completion against its next
  step and snapshots its cursor/status.
- Significant events are consumed from a persisted event-log cursor and queue
  deduplicated wakes for affected characters. Wakes honor per-agent cooldowns;
  dead/archived agents are discarded, and the action/outcome chain points to the
  triggering event. Daily belief decay is integrated with world-day advancement.
- `sim.tools.inspect --decisions [--decision-agent ID]` exposes private per-agent
  plans, beliefs, wake state, and bounded score-component traces for designers,
  without adding internal decision records to the public event stream. Explicit
  belief audits currently cover ship estimated strength and island prosperity;
  material estimate errors emit `BELIEF_ERROR` with both the original evidence
  and triggering event as parents and a `belief_error` cause tag.

## M6 — encounter detection and combat phases

- Encounter checks run once per simulated day at the existing deterministic
  world-day boundary rather than adding an hourly scan; only crewed vessels
  participate, while ordinary economy merchant ships without generated crew
  remain outside named combat until they gain a crew. Parties must share an
  island or the same route within a configured progress/signal range.
- Detection is independent for each lookout using navigation, island visibility,
  and current regional weather. If only one lookout detects the other, that
  vessel receives surprise and its first gunnery exchange is prioritized and
  boosted. Pair cooldowns prevent daily repeated encounters and are snapshotted.
- Combat records pre-fight computed strengths and both captains' belief stores,
  then applies bounded seeded gunnery rounds and optional boarding rigging
  damage through existing ship-damage events. Encounter phases and child damage
  IDs are preserved in the resolution record. The following outcome/consequence
  work handles full crew casualties, sinking removal, ownership, loot, and memory.
- Combat outcome selection uses data-driven probabilities and post-phase state:
  hull loss takes precedence, weak boarding morale may surrender/capture a ship,
  defection/captain capture/casualty branches follow, otherwise combat resolves.
  A killed captain is archived immediately; a wounded captain receives a
  persistent injury. Held prisoners are tracked in the encounter snapshot and
  can be ransomed, executed, recruited, released, or traded to marines.
- Captured/surrendered ships are marked with their new owning crew; their
  pending voyages are canceled. Registered merchant accounts remain eligible
  to trade and voyage after capture, while captured non-merchant ships cannot
  start independent voyages. Defectors are
  removed from the old roster and joined to the victor's crew with updated
  character links and a causal defection event. Their accrued wallet transfers
  with them; crew validation reconciles original payroll shares plus defection
  adjustments rather than rewriting historical wage events.
- Encounter loot transfers a configured share of loser cargo/supplies within the
  victor's hold capacity and a share of losing crew treasury; all are account
  transfers. Bounties are explicit `BOUNTY_PAID` silver sources and are added to
  the global ledger. Reputation and cumulative bounty standings are snapshot
  state. Ransom transfers finite crew treasury with paired payer/recipient
  validation.
- Sunk ships move to the archive, their transit/arrival events are removed, and
  their crew becomes survivors at the encounter island (or route origin). Crew
  treasuries/member wallets are salvaged into island treasury. If the vessel
  belongs to a registered merchant, a causally logged replacement vessel is
  created at the salvage island and its finite purse/cargo account is retained;
  non-merchant ship supplies remain with the archived wreck. This avoids
  silently deleting trade capacity while conserving the global ledgers.
  Survivors remain active named characters and can be recruited by later systems.
- Combat/prisoner deaths invoke MAJOR+ replacement immediately if the population
  has crossed the configured low-water threshold; annual old-age deaths remain
  collected and replaced once after the birthday batch so mid-batch rookies are
  not exposed to that same year's death rolls.
- M6 aftermath emits reputation, bounty, loot, ownership, casualty, and
  defection records as causal follow-ons. Surviving captains affected by a
  destructive outcome gain a bounded `avenge_crew` want; full memory/opinion
  derivation remains M7. Each captain also receives a `COMBAT_MEMORY_HOOK`
  event naming the rival, ship, encounter outcome, and decisive factors; M7 will
  turn these hooks into persistent memories/opinion changes.
- When both sides carry an integer estimate of the opposing vessel's strength,
  the resolution compares those estimates with post-phase computed strength.
  If the side forecast as weaker is actually stronger, the win is recorded as
  `belief_upset` and tagged `upset`; fights without two comparable estimates do
  not enter this diagnostic denominator.

## M7 — memory and derived opinion foundation

- Memories are stored as event-attributed character records with intensity and
  per-day decay in integer permille. Current intensity uses deterministic
  fixed-point exponentiation; trait modifiers reduce grievance decay for pride
  and paranoia and increase recovery for sociability. Same-kind/same-target
  repeat offenses amplify each new memory by a bounded multiplier.
- Opinions are caches derived only from current memories plus inherited-grudge
  baseline; memory kinds provide data-driven dimension effects. They are
  recomputed when a memory is added and at each day boundary, then audited
  against recomputation during snapshot validation. Protected/core memories are
  retained when pruning to the configured cap, even if that exceeds the soft cap.
- Combat memory hooks become participant memories; parley and mutual withdrawal
  are recorded as honored, while winning/losing combat produces differentiated
  defeated/defeated_by memories. Memories for non-captain witnesses remain an
  open extension; this slice handles the encounter captains directly.
- `hunt_rival` is a data-defined utility action, eligible only when the agent has
  a derived grievance of at least the revenge threshold toward a living target.
  Its score includes a grievance-weighted bonus, and execution cites the
  triggering event plus active memory/inheritance evidence. It emits a
  `FEUD_RAID_ATTEMPTED` child tagged `feud_escalation`; this first raid slice is
  explicitly repelled with no material transfer, then gives the rival a
  reciprocal humiliation memory. Grievance memories can create a revenge want
  and replace a non-vengeance dream with a `defeat_character` dream once the
  configured threshold is met.
- Reconciliation is an explicit, manner-tagged event followed by a
  `reconciled` memory. Its negative grievance effect fades faster than the
  original grievance memories, so reconciliation lowers active hostility but
  leaves a persistent residual scar. Living children/protégés linked by parent
  or mentor receive eligible grievances at 60% intensity on a mentor/parent's
   death, with a `GRUDGE_INHERITED` event and source evidence retained in
   `heritage.inherited_grudges`.

## M8 — derived faction relations and treaties

- The initial institutional faction record is one-to-one with each existing
  finite crew (faction ID reuses crew ID); mergers, splits, and independent
  non-crew factions remain future work. Members/captains' M7 opinions are aggregated in both
  directions; shared recorded house heritage contributes kinship. Trade
  dependence uses only explicitly evented faction-to-faction volume, not all
  merchant/island sales (islands do not yet have faction ownership).
- Relations are computed on demand from current opinions, institutional breach
  adjustments, and trade volume; there is no independently editable relation
  ladder. Values and treaty transition thresholds are data-defined.
- Treaty state is a separate persisted pair record. The JSON transition graph
  rejects same-state/unsupported transitions, validates thresholds and exact
  obligation terms, and requires an event cause. Mutual-defense requests create
  one-use obligations; declines emit `TREATY_BREACHED`, lower institutional trust,
  raise grievance, and cannot be resolved a second time. An already-open
  obligation remains attached to the pair record if treaty terms are amended;
  it is not silently erased by a transition. Treaty expiry and automatic
  cross-faction trade attribution remain future work.
- Monthly faction decisions use per-faction named RNG streams. Starting home
  islands are claimed as political territory; later expansion only claims
  adjacent islands with no modeled ruler. These peaceful claims do not simulate
  garrisons, island loyalty, population consent, or conquest, and fortification
  levels are strategic state only. Council succession and ambition-driven coup
  attempts are evented; this is not yet an island-combat/conquest simulation.
- The Marine command is a separate government faction, staffed from existing
  free official/high-leadership characters rather than minting people. A
  deterministic monthly review can appoint deputies, resolve succession after
  a commander's death, or emit an ambition-driven command challenge. It is
  political state only; patrol deployment and its finite budget belong to M8
  heat work.
- Heat is persisted per active/archived character and crew, with daily fixed
  decay and global tension equal to the capped sum of crew heat. Combat events
  raise heat; policy thresholds set/raise crew bounties. A bounty is claimed only
  by defeating a crew with an outstanding warrant, and transfers silver from
  the finite Marine reserve instead of minting a ledger source. The reserve is
  seeded by transferring silver from the richest island treasury. Monthly
  patrol/hunter/fleet responses consume the same reserve and move funds into a
  separate island response account, which is conserved but not market liquidity.
  Response staging currently uses the faction home island; route prediction,
  bribes, privateering relief, and operational ship/patrol movement remain future
  work. A combat without a pre-existing warrant no longer yields automatic silver.
- Losing a crew's final ship emits a causally linked `FACTION_DISSOLVED`, moves
  its institutional record to the archived-faction registry, and releases its
  territory, fortifications, and active response assignment. Historical heat and
  bounty records remain auditable; new treaty proposals require active factions.
- Monthly diplomacy processes each active faction pair once with a named pair
  RNG stream: neutral pairs may establish nonaggression, continued peaceful
  exchanges build reciprocal member memories/opinion, and high mutual trust may
  advance to alliance. Hostile combat while a no-hostile-action treaty is active
  emits `TREATY_BREACHED`, damages institutional trust, creates betrayal memories,
  and transitions the pair to war. Treaty transitions remain the single source
  of state truth, so a repeated alliance cannot be sealed without an intervening
  valid transition.
- A combat can claim only a warrant that existed before that encounter; heat
  created by the combat may set a warrant for later encounters but cannot pay
  for itself. Crossing a response threshold can immediately allocate the
  corresponding finite Marine reserve, with the threshold/bounty in its causal
  ancestry. Dissolved crews retain historical economy records, so validation of
  past bounty beneficiaries must not require the crew to remain active.
- The batch T-HEAT audit treats runs with no threshold crossings and no funded
  responses as vacuously valid. If responses are funded, each must trace to a
  `HEAT_CHANGED` or `HEAT_THRESHOLD_CROSSED` ancestor, and a run with crossings
  must have a causal funded response. Later heat increases can raise an existing
  bounty without crossing a new threshold; the resulting response is still
  heat-caused even though its immediate bounty ancestor is not itself below a
  threshold-crossing event.
- Snapshot schema advances to 13 for politics, initial faction records, Marine
  command appointments, heat registries, and economy-held response funds; no
  old-snapshot migration is added because the simulation requires exact schema
  versions and is isolated from live game saves.

## M9 — stateful environment and director

- Yearly island harvest rolls use isolated island/year RNG streams and affect
  grain output through a persisted yield multiplier. A poor roll is a tagged
  root `HARVEST_FAILURE`; daily production then flows through the existing
  conserved economy rather than minting or deleting goods.
- Storms/hurricanes begin on weather transitions subject to a regional cooldown
  and move along the inter-region route graph on their own named streams. The
  originating weather transition affects route speed through the ordinary
  weather system; a moving track does not pin later regions into severe weather.
  Ships take damage only when a storm starts or reaches their region (not once
  per day of exposure), through ordinary causal ship-damage events; storms
  dissipate after a bounded duration. Disease is a finite-duration island pressure that spreads
  through port contacts or transiting vessels, reduces productivity, and raises
  unrest without silently changing population ledgers. Monsters are bounded,
  persistent route hazards that migrate and can damage a ship on their route.
- Treasure sites are finite, non-monetary relic-cache discoveries; clues
  propagate across connected islands as a persisted causal rumor chain. No silver or
  goods are awarded until a future design defines a conserved source/account.
  Clue events are emitted at the first daily update, not world construction, so
  adding environment content does not perturb tick-zero event IDs or unrelated
  initial combat rolls.
- Environment state and its catalog digest are part of snapshot schema 14.
  Current natural-event scope is a first data-driven slice; the density/liveness
  seed-batch acceptance is covered by the final 5-seed × 10-year on/off runs.
- The story director checks persisted health metrics every configured interval
  (conflict drought, MAJOR+ population, top-faction power share, trailing trade
  volume, and nullable player-region pressure). A throttled, yearly-capped
  `DIRECTOR_INJECTION` can introduce a MAJOR rookie with a logged vendetta when
  conflict is stagnant or the named population is low; it creates a condition,
  not an immediate combat/outcome. The rookie and grievance are ordinary
  causally linked character/memory records. Director enablement, intensity,
  cadence, cooldown, annual cap, and population floor are config/snapshot data;
  `run_sim --director on|off` overrides the setting and batch runs report
  per-seed maximum notable-event gap and monthly density.
- The configured default keeps the director off to preserve the M0–M8 reference
  population and politics runs; director-on behavior is exercised explicitly in
  M9 tests/batches. Character name generation checks archived as well as living
  names because director and rookie injections increase long-run turnover.
- Final M9 batch tuning was rechecked over five seeds × ten years with the
  director both disabled and enabled. Both modes met the liveness/density bands,
  retained exact ledgers and stable trade, and passed the inherited M8 political
  acceptance metrics. The enabled runs logged 17–18 bounded injections per seed.

## M10 — chronicler boundary

- The chronicler consumes the immutable event log and current/archive entity
  state, then returns an artifact bundle; it never appends events or changes
  simulation records. Earned epithets are derived as read-only awards with a
  deed-event reference rather than being written back into character state, so
  prose generation remains pure. Existing character/ship names remain governed
  by the simulation's shared uniqueness registry.
- Chronicler templates and significance/reputation tuning live in
  `sim/data/templates/chronicler.json`, separate from simulation event data.
  Required placeholders are validated against declared slot sets; authored and
  rendered prose rejects first-person self-reference, raw internal IDs, and
  unfilled placeholders. Low-significance activity is grouped into trade or
  annual harbor-book summaries instead of repeated per-event filler.
- The reviewed M10 sample is `reports/samples/seed-24-10year-chronicle.json`,
  generated from the existing seed-24 ten-year snapshot (148,405 events).
  It contains 399 chronicle entries, 40 rumors, all ten almanac years, 112
  biographies, four faction histories, and 14 obituaries. Audit of 494 prose
  strings found zero raw IDs, unfilled slots, first-person self-references, or
  duplicate significant sentences; the sample's major battle/sinking and
  faction-dissolution chain names the encounter as its cause.

## M11 — set-piece battle boundary

- M11 fleet doctrine, legend special traits, and battle tuning are validated in
  `sim/data/battles.json`; the battle system snapshots its catalog digest and
  command/cooldown state. Four founding crew captains (or fewer if there are
  fewer starting crews) are recognized as LEGENDs through ordinary causal
  `LEGEND_RECOGNIZED` events, with data-defined traits. They are promotions of
  existing named entities, not minted characters or ships, and still count
  toward the existing MAJOR+ population target.
- Founding doctrines are deterministic initial state without extra tick-zero
  command events, preserving baseline startup event IDs. Later commander or
  doctrine changes are logged as `FLEET_COMMAND_SET` events.
- A hostile encounter can seed a set-piece only when both opposing current
  commanders are legends. Theater rounds and the named duel resolve atomically
  within that encounter tick; reinforcement arrivals are scheduled by round.
  This prevents multi-day battle state from racing ship transit/arrival events,
  while keeping the `BATTLE` parent and all round/duel/reinforcement records in
  one deterministic causal chain.
- A battle's losing fleet takes bounded persistent hull/rigging damage that
  cannot sink a ship by itself; its crew morale is written back after the round
  cascade, and a data-tuned seeded check can add a persistent injury to a
  non-captain. Defeated commanders generate ordinary government heat and
  bounty policy events with the battle conclusion as cause. A faction defeated
  below the data-defined morale threshold can enter a `FACTION_POWER_VACUUM`
  and install the strongest living council successor. A complete rout at zero
  morale dissolves the political faction and releases its territory while
  preserving surviving ship crew and accounts under the archived faction
  record; routs cannot erase people or silver.
  This keeps battle consequences within the existing repair, character,
  government-budget, and faction-succession ledgers rather than minting ships,
  silver, or people.
- A surviving crew may continue ordinary encounters after its political faction
  collapses, but an unaffiliated winner cannot collect a government bounty;
  bounty transfers require an active faction recipient and remain budget-capped.

## M12 — LOD and scale boundaries

- The default geography expands from 30 to 40 islands by adding ten remote
  stormbound islands beyond Far Lantern. The original route IDs/edges remain
  unchanged; every new Tempest Belt ingress/edge is hazardous, and new island
  economy profiles preserve exact map coverage. This reaches the scale target
  without re-routing established ports or making the dangerous region safer.
- Existing island population/class counts remain the census authority. Twenty
  residents per year are sampled out of those counts into named BACKGROUND
  records; this moves representation, not people. Compact records retain stable
  identity, name, stats, traits, heritage, role, location, and a bounded hot list
  of event IDs. The append-only event store remains the authoritative complete
  history, so promotion can reconstruct the person and chronicler queries remain
  causally complete. Notable island events can promote one resident daily to a
  full MINOR object; inactive uncrewed promoted residents can demote when they
  have no retained memories, opinions, or political role. Crewed and major
  actors are never demoted.
- Regional LOD summaries observe—not replace—the conserved daily island economy.
  Seven-day summaries aggregate actual trade volume, active response coverage,
  and incident counts; a 104-summary ring bounds coarse regional history. Daily
  versus weekly clocks are compared on the same seeded world and must agree on
  aggregate counts/flows, with patrol coverage represented as integer
  permille-days. Full event bodies are retained because events are global cause
  parents and are queried by ID/type/entity by simulation validation and the
  chronicler; compacting those bodies would require a separate lazy causal
  archive format and is intentionally not conflated with named-actor LOD.
