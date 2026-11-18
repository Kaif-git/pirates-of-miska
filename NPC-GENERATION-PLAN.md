# Procedural NPC Society and Emergent History — Detailed Plan

## 1. Status and intent

This document specifies the next NPC-society expansion. It is a plan, not a claim
that the features below are implemented. Preserve `NPC-SOCIETY-PLAN.md` as the
design record for the first authored society/news slice.

Replace the fixed cast as the default for new worlds with a seeded, generated
society: distinct people, abilities, personalities, organizations, ambitions,
relationships and histories. Take inspiration from Dwarf Fortress's causal world
generation, not its exact mechanics or content.

Core rule: **randomize starting conditions, then simulate consequences.** Do not
reroll personalities, ambitions or alliances arbitrarily every hour. A story must
be explainable through actual people, decisions, resources and events.

The user explicitly requires that tactical-combat code remain untouched. All
power and conflict work here belongs to the strategic NPC layer. Shipbuilding,
player trade contracts and unrelated combat fixes are also outside this plan.

## 2. Existing foundation and integration points

- `pirates/society.py`: authored people/organizations, ambitions, signed social
  scores, hourly meetings, abstract clashes, reputation and structured news.
- `pirates/voyage.py`: persistent NPC vessels, chart routes, hourly movement,
  economic activity and fleet snapshots.
- `pirates/game.py`: world initialization, time advancement and save/load hooks.
- `pirates/management.py`: sea chart, named captains and recent-news UI.
- `tests/test_npc_society.py` and `tests/test_voyage.py`: existing society and
  fleet regression coverage.
- `tools/preview_npc_society.py`: save-free native society/news walkthrough.

Important replacement points:

1. Fixed `PEOPLE` and `ORGANIZATIONS` become an optional authored scenario, not
   the only valid identity universe.
2. Restore must validate generated state and references rather than requiring
   exact equality with canonical names, ships, ambitions and organization lists.
3. Fixed roster/headcount expectations become scenario-specific opening ledgers
   plus conservation checks. Generation is an explicit creation boundary, not
   permission to spawn people during ordinary simulation.
4. The single relationship score becomes a richer directed relationship model.
5. Personality/goal-driven behavior replaces hardcoded organization-ID decisions.
6. Existing strategic simulation, UI and save hooks are reused where appropriate;
   parallel tactical-combat and shipbuilding work must be preserved.

## 3. Scope and initial scale

### Included

- Seeded character, ship-group and organization generation.
- Backgrounds, attributes, personality, values, meaningful traits and skills.
- Skill experience, career progression and legible derived power summaries.
- Generated command structures and directed social relationships.
- Persistent actionable ambitions and explainable strategic choices.
- Formal agreements, memories, defections and consequence-driven social change.
- Bounded pre-game history using the same rules as live strategic simulation.
- Save migration, player-facing profiles/history and reproducibility testing.

### Initial scale targets

- Approximately 20–40 notable NPCs across 8–12 NPC vessels.
- Named founders, captains, important officers and rivals.
- Ordinary crew remain aggregate headcounts initially; named people count within
  those headcounts, not as extra people added on top.
- Small configurable numbers of pirate organizations, independent crews and
  Marine commands, constrained by available captains, vessels and resources.
- Initial prehistory target: 30–90 strategic days, adjustable through generation
  settings after performance measurement.

These are tuning targets, not hardcoded invariants. Validate settings before
generation and reject impossible combinations rather than repairing them by
silently inventing actors or resources.

### Deferred

Full genealogy, birth/children/aging simulation, dynastic succession, individually
simulated ordinary crews, unrestricted population growth, world geography
generation, player recruitment UI and tactical-combat integration. No automatic
new wars or betrayal rolls disconnected from motivations.

## 4. Deterministic world-generation foundation

Persist a generation manifest containing:

- World seed in a stable serializable representation.
- Generator version and schema version.
- Validated generation settings and content-table version or fingerprint.
- Stable world/entity identity namespace.
- Explicit history-start and player-start strategic times.
- Opening people, vessel and resource ledgers.

Use separate deterministic random streams for names, backgrounds, characters,
organizations, initial relationships, and strategic stochastic outcomes. Derive
stream seeds with a stable algorithm, not Python's process-randomized `hash()`.
Iterate entities and candidate actions in stable ID order before tie-breaking.

Same seed + settings + generator/content version must produce the same initial
world. Different seeds should produce materially different societies, not only
different names. A generator update may change new worlds, but must never reroll
an existing save. Store generated records in saves; do not reconstruct them from
the latest generator on load.

Use a saveable, versioned PRNG state or stable event-keyed draws for live choices.
Rendering, inspecting profiles and opening menus must not consume simulation
randomness. Loading and continuation must match uninterrupted simulation.

## 5. Character generation pipeline

Generate connected layers rather than unrelated uniform rolls:

1. Allocate stable identity and choose a unique display name from content tables.
2. Choose origin, age band, upbringing and former occupation.
3. Generate bounded physical and mental attributes, informed by background.
4. Generate continuous personality axes and personal values.
5. Derive a small set of noticeable traits consistent with those axes.
6. Allocate skill experience using a bounded background/age training budget.
7. Assign social role, organization, vessel and circumstances.
8. Derive aspirations, starting goals and initial relationships.
9. Validate the character and all ownership/command links.

Correlations are biases, not destiny. A former merchant is more likely to know
trade, but can still be a reckless pirate. Avoid generating universal experts or
making every leader the strongest person in every domain.

### Character data

| Layer | Proposed fields | Intended effect |
|---|---|---|
| Identity | ID, name, origin, age band, background | Persistent identity and generation context |
| Attributes | Strength, endurance, perception, intellect, presence | Basic capability inputs |
| Personality | Boldness, greed, empathy, pride, patience, sociability, discipline | Decision preferences and reactions |
| Values | Loyalty, freedom, wealth, order, fame, family | Long-term priorities |
| Traits | Vindictive, merciful, boastful, cautious, charismatic | Salient behavior and readable characterization |
| Skills | Leadership, navigation, trade, tactics, diplomacy, personal fighting aptitude | Learned strategic competence |
| Experience | Per-skill XP, career achievements | Progression grounded in actions |
| Circumstances | Wealth, health/readiness, morale, rank, followers | Feasibility, risk and opportunity |
| Social state | Membership, command role, relationships, commitments | Social behavior |
| Motivation | Aspiration, active goal, progress, commitment | Action selection |

Numeric scales and distributions must be centralized and documented when
implemented. Prefer moderate values with uncommon extremes; avoid uniform rolls
that make exceptional personalities ordinary.

### Trait contract

Every trait needs a documented mechanical effect and a behavior test. Examples:

- Vindictive: stronger grievance weight and slower forgiveness of major injuries.
- Merciful: greater reluctance toward harsh strategic choices; favors release or
  negotiated outcomes where supported by actual state transitions.
- Cautious: higher risk penalty and earlier regrouping threshold.
- Boastful: greater preference for visible successes and reputation rewards.
- Charismatic: improves persuasion through relevant skill/context, never a
  guaranteed alliance or immunity to betrayal.

Do not double-count a trait and its underlying personality axis as two unrelated
bonuses. Personality describes tendencies, not absolute prohibitions. Apparent
contradictions can be valid if values or circumstances explain them.

## 6. Levels, progression and power

Separate four concepts:

1. **Attributes:** relatively stable basic capability.
2. **Skill levels:** derived from accumulated skill XP and bounded progression.
3. **Career level/title:** a readable summary of development, not a universal
   multiplier used to decide every outcome.
4. **Power:** context-dependent capability and access to resources.

Personal capability is distinct from command power. Command estimates should use
actual crew strength/readiness, vessel condition, available supplies, leadership,
cohesion and dependable support. Organization power aggregates eligible assets
without counting the same ship or crew twice.

Reputation is a separate social estimate and may be misleading. A feared captain
can be weaker than reports suggest. A capable individual may lack resources or
recognition. Display a power breakdown rather than an unexplained magic number.

Experience is awarded for completed meaningful work, training and relevant
outcomes—not merely existing, claiming a goal or repeatedly opening a menu.
Use bounded gains, appropriate learning rates and no infinite farming loops.
Starting skill XP must agree with the generated skill levels.

These fields affect strategic NPC evaluation and abstract clashes only. Do not
edit tactical fighter stats, hit chance, damage or tactical skill persistence.

## 7. Organization and hierarchy generation

Generate organizations from founders, opportunities, starting budgets and shared
interests. Distinguish pirate fleets, independent crews and Marine commands.

Each organization contains:

- Stable identity, generated name/type and founder/leader.
- Values, strategic purpose and leadership style.
- Membership, vessel ownership and explicit command links.
- Resources, cohesion, reputation and current strategic objective.
- Agreements, rivals and institutional memories.

Assign captains and officers from actual generated people. Validate no dangling
leader, duplicate captain, cyclic command chain, incompatible simultaneous roles,
or vessel counted under multiple owners. Preserve a distinction between legal
affiliation, actual loyalty, personal friendship and formal subordination.

A captain might respect a rival, fear their admiral and distrust an ally. Internal
ambitions may conflict with organization objectives without instantly causing a
mutiny. Defection requires a feasible destination/support and an actual transfer
of membership/assets; it cannot duplicate a crew or ship.

## 8. Relationships and formal agreements

Relationships are directed: A's view of B need not equal B's view of A. Proposed
dimensions are trust, affection, respect, fear, grievance and obligation/debt.
Specify valid ranges and neutral defaults centrally.

Generate a sparse meaningful social graph from shared backgrounds, service,
values and actual prehistory. Avoid an eagerly stored all-pairs graph. Distinguish
person-to-person relationships from organization diplomacy.

Formal alliances are separate records, not a friendship-threshold label. Store:

- Participants and authorized signatories.
- Purpose, terms, start time and review/expiry conditions.
- Expected contributions and obligations.
- Fulfillment, refusal, breach and termination history.

Common enemies, complementary needs, trust, relative power and perceived risk can
motivate an offer. Acceptance evaluates both sides' interests. Alliances can end
through expiration, incompatible goals or a concrete breach—not an unexplained
random betrayal roll. Joining a fleet is different from allying with it.

## 9. Ambition generation and goal lifecycle

Each important NPC has a long-term aspiration and initially one active actionable
goal. Organizations also have strategic objectives. Goals include:

- Goal ID/type, actor, target and source reason/event.
- Priority, progress, commitment and creation/review time.
- Required knowledge, resources and feasible actions.
- Success/failure conditions and cancellation reasons.

Examples:

- Proud subordinate: earn an independent command or recognition.
- Wealth-focused trader: profit from a known route.
- Loyal officer: recover a captured companion.
- Cautious defeated pirate: seek protection from a stronger fleet.
- Ambitious Marine: improve standing by containing a notorious rival.

Use prerequisites so impossible ambitions are not selected. Reconsider goals
after success, failure, major losses, new information or scheduled reviews.
Commitment/hysteresis prevents hourly flip-flopping. Do not let every NPC default
to pursuit simply because an enemy exists.

## 10. Strategic decisions and knowledge

Evaluate a bounded set of feasible actions at appropriate strategic intervals.
An initial utility model is:

`goal progress + personality fit + relationship interests - cost - perceived risk`

Keep component scores inspectable for debugging and player-facing explanations.
Randomness may resolve comparable options, but should not routinely override
overwhelming preferences or feasibility constraints.

Candidate actions include trade, patrol, pursue, evade, regroup, request support,
offer/accept an agreement, fulfill/refuse an obligation, train, provision and
recruit. Only enable actions with implemented, conserved state transitions.
Recruitment needs an actual finite source population; provisioning needs stock,
funds and access. Unsupported actions remain deferred, not narrative-only events.

Characters act on limited knowledge: sightings, known ports, communicated reports
and remembered positions. Store source/time and let information become stale.
Do not grant perfect live access to an enemy's chart position. Separate truth,
NPC belief and player knowledge so the UI does not accidentally reveal hidden
state. Communication can initially use simple explicit port/meeting exchanges.

Movement must still follow valid chart lanes. Decisions should not overwrite
retreat/regroup commitments on the next tick. Use bounded search, action
cooldowns and stable iteration to control performance and determinism.

## 11. Memories, events and emergent consequences

Consequential state transitions emit structured events. Event records contain
stable ID/time, participants, place, cause, relevant pre/post changes and outcome.
Memories reference events instead of copying unbounded prose histories.

Examples: aid, rescue, defeat, humiliation, betrayal, honored promise, unpaid debt,
shared success and refused support. Minor impressions decay; major grievances
can persist. Memory effects must not be reapplied on every load or review.

Example causal chain:

1. A cautious captain loses a strategic clash.
2. Damage and fear change the risk calculation.
3. The captain seeks protection and negotiates fleet membership.
4. Accepted terms create obligations and an actual affiliation transfer.
5. A dangerous later order conflicts with the captain's values.
6. Refusal damages trust; available support may make defection feasible.

No step is guaranteed, and every step must have a real state transition. News is
a readable projection of recorded events, not a separate random headline system.

Keep recent detail bounded and retain compact long-term summaries or important
event references. An archive policy must not erase evidence still referenced by
active goals, debts, agreements or major memories.

## 12. Pre-game history simulation

Pipeline:

1. Validate settings and allocate opening ledgers.
2. Generate people, vessels, organizations and initial social connections.
3. Run a headless strategic history period using live-world strategic rules.
4. Record promotions, meetings, agreements, defections, clashes and economic
   events only when their corresponding mechanics actually occur.
5. Validate all identities, ownership, balances and active commitments.
6. Produce a starting chronicle and introduce the player at the final epoch.

Do not advance the player's ship-life simulation for months just to create NPC
history. Use an explicit strategic clock/context and reconcile it with the
player-start time; avoid negative save clocks or replaying history on first tick.

Provide progress/cancellation and a bounded work budget. Prefer deterministic
step limits to wall-clock-dependent truncation, so identical seeds remain
reproducible. Reject/report invalid histories rather than silently regenerating
until the output looks dramatic. No fabricated biography victories.

## 13. Player and developer presentation

- New-world settings: seed, population scale, organization balance and history
  duration. Existing saves bypass generation.
- NPC profile: identity, known background, visible traits, career, abilities,
  reputation, organization, aspiration and current known activity.
- Organization view: leadership hierarchy, assets, allies and rivalries.
- Relationship explanations: known trust, fear or grievance with concrete causes.
- Chronological news/history browser with participant and organization context.
- Clear explanations such as 'Seeking protection after defeat by …'.

Developer inspection can show exact values, truth, utility components, RNG keys
and invariant failures. Normal play shows known information and estimates; do
not expose an exact secret ambition merely because it is stored on the NPC.
All reports remain read-only and must not advance time or randomness.

## 14. Persistence, migration and validation

Save the manifest, generated entities, dynamic affiliations, XP, goals,
relationships, agreements, knowledge, memories, events and deterministic random
state. Load validates the saved world before replacing the live world.

Legacy authored societies remain valid and keep their identities, relationships,
vessels and news. Add missing fields through an explicit deterministic migration;
never run the new-world generator over an existing population. Old saves must
not acquire duplicate ships/people or have their histories rewritten.

Validation includes:

- Unique bounded IDs, valid names/types/ranges and supported schema versions.
- Reciprocal membership/ownership and acyclic command links.
- Named individuals accounted for within crew/population ledgers.
- Skill XP/level consistency and derived-power recomputation.
- Valid goal targets, agreement signatories and relationship endpoints.
- Ordered simulation time and valid event/memory references.
- Resource and population conservation since the generation boundary.
- Deterministic continuation after save/load, including cooldowns and randomness.

Save compatibility is based on schema and validated state, not equality with a
fixed authored cast or assumptions about exactly five vessels/40 people.

## 15. Proposed module boundaries

These are proposed new paths, not existing implementation claims:

- `pirates/npc_generation.py`: generation manifest, seeded streams and pipeline.
- `pirates/npc_character.py`: character schemas, personality/skills and validation.
- `pirates/npc_decisions.py`: goal lifecycle, feasible actions and utility scoring.
- `pirates/npc_history.py`: event/memory handling and headless history runner.
- `content/npc_generation.json`: names, backgrounds, distributions and templates.
- `tests/test_npc_generation.py`: generation/reproducibility/schema tests.
- `tests/test_npc_decisions.py`: personality, knowledge and goal behavior tests.
- `tests/test_npc_history.py`: causal history, conservation and continuity tests.

Keep `pirates/society.py` as strategic coordination/diplomacy, with clear state
ownership. Do not build a second vessel ledger or duplicate the economy. Final
splits should follow the current code after an implementation-time audit.

## 16. Implementation phases and acceptance gates

### Phase A — Generation foundation

- Audit current worktree and preserve concurrent changes.
- Define schemas, settings, stable ID rules, RNG policy and generation manifest.
- Implement correlated character generation and validation.
- Add seed fixtures and reproducibility tests.

Gate: identical versioned inputs produce identical character records; different
seeds produce meaningful attribute/personality/background variation.

### Phase B — Generated societies and compatible saves

- Generate organizations, command hierarchies, vessels and opening ledgers.
- Replace fixed-identity restore checks with schema/reference validation.
- Keep the authored cast available as a legacy/scenario fixture.
- Add roundtrip and legacy migration tests before switching the new-world default.

Gate: generated societies have valid command/ownership graphs and balanced
opening ledgers; old authored saves load without rerolls or duplication.

### Phase C — Motivation and social behavior

- Implement aspiration/goal lifecycle and inspectable action evaluation.
- Add personality/value/risk influences and limited information.
- Add directed relationships and formal agreements.
- Ensure actual conserved actions and persistent commitments.

Gate: controlled characters choose different actions because of personality or
circumstances; alliances require mutual feasible interests and explicit terms.

### Phase D — Memory and prehistory

- Record causal events/memories and apply effects once.
- Add headless bounded history using the same strategic action rules.
- Generate starting chronicles and preserve important references.

Gate: sampled history chains can be reconstructed from actual changes; population,
vessels and resources remain accounted for; no first-tick replay after generation.

### Phase E — Player presentation

- Add generation settings and seed display.
- Add known-NPC profiles, hierarchy/agreement views and history browsing.
- Keep truth/debug views distinct from player knowledge.

Gate: native screenshots demonstrate readable generated identities, motivations,
social consequences and history without leaking hidden state or advancing time.

### Phase F — Integration, balance and verification

- Test diverse fixed seeds, long runs, chunking and save/load continuity.
- Check growth/memory bounds and measure generation/runtime performance.
- Audit that tactical-combat code was untouched.
- Run focused and integrated tests, compile checks and save-free walkthroughs.
- Report pre-existing/out-of-scope test failures separately; do not modify combat
  merely to make the whole suite green.

Gate: all in-scope tests pass with deterministic repeatability, valid migration,
conserved ledgers and inspected native evidence.

## 17. Verification matrix

| Area | Required evidence |
|---|---|
| Reproducibility | Same seed/settings/version yields identical generated snapshots |
| Diversity | Fixed diverse seeds vary personality, capability, hierarchy and motives |
| Correlation | Background biases skills without making every result identical |
| Traits | Each implemented trait changes at least one controlled behavior |
| Levels/power | XP agrees with levels; power follows actual assets/readiness |
| Goals | Progress/success/failure/review works; no hourly oscillation |
| Knowledge | Unseen ship movements do not instantly update enemy beliefs |
| Relationships | Directional attitudes and agreements remain distinct |
| Causality | News and memories reference real changes and valid participants |
| Conservation | No duplicated crew/ships or free recruitment/provisioning |
| Persistence | Save/load continuation equals uninterrupted simulation |
| Migration | Authored saves retain identities and never reroll |
| Time | Chunking and prehistory/live handoff produce the same valid state |
| Bounds | Entity/event/memory growth and generation work stay within limits |
| UI | Profiles/history are readable, read-only and knowledge-filtered |
| Scope | No tactical-combat edits; parallel changes preserved |

## 18. First playable milestone

Deliver two clearly different generated societies from two seeds. Repeating one
seed reproduces the same society. Generated personalities cause demonstrably
different strategic choices, those choices create contextual news, and saving/
loading preserves the population and future simulation exactly.

Build this small coherent loop before expanding into genealogy, dynasties or
large populations. A few memorable, explainable histories are preferable to
hundreds of random names with disconnected statistics.
