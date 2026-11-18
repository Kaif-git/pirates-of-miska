# Pirate NPC Motives, Predation, Strength, and Emergent Stories

## Status and objective

**Proposed; implementation not started.** This is the phased plan for a long-running goal. It extends the generated NPC society and strategic sea layer; it does not replace NPC generation or the authored legacy cast.

Make NPC piracy produce understandable, consequential stories. Pirate captains and organizations should pursue wealth, survival, reputation, revenge, freedom, and power through actual strategic choices. They should raid government/Marine vessels, prey on pirate rivals when risk and reward make sense, retreat or seek allies when outmatched, and change strength through what they do. Named captains must cause those outcomes—not be names attached after an organization-level simulation.

Do not force a fixed number of battles or manufacture dramatic headlines. Seeds may yield cautious, prosperous, defeated, or violent histories; outcomes must follow from motives, known targets, relative capability, resources, and events.

## Current diagnosis

The November 2026 seed-run review found mostly port arrivals and peaceful relationship changes. In three 45-day runs, all 120 retained news entries were port arrivals. Shorter runs also had repetitive port/trade reports crowding out the few distinct political events.

- `pirates/npc_generation.py` generates attributes, personality, values, traits, skills, ambitions, active goals, named leaders/captains, organizations, and finite opening ship/crew ledgers. Generation must remain deterministic and must not reroll saved societies.
- `pirates/npc_decisions.py` scores trade, patrol, hunt, regroup, and negotiate. The society layer supplies candidates and executes them.
- Generated target contacts in `pirates/society.py` require recent knowledge and hostile relationships. A clash requires a strong feud, or a hunt contact with sufficient hostility. The current material outcome is chiefly condition loss, retreat, reputation, and relationship changes—not plunder or durable crew/fleet growth.
- The legacy NPC pulse in `pirates/voyage.py` already has simple trade, raid, and captive-market transfers. Audit and reuse valid paths; do not create a second fleet, cargo, economy, or population ledger. Legacy behavior is not proof that generated societies have a complete predation loop.
- `NpcSocietySystem._append_news` retains a bounded 120-event list, while generated port arrivals are emitted on arrival. Routine traffic can evict consequential history.
- Generated stats and ambition text alone do not create agency. Feasible actions, motive, outcome, goal progress, and future state changes must connect in a causal chain.

## Principles and boundaries

1. **Consequences over flavor text.** A raid is reported only after cargo, money, crew status, vessel status, or relations actually change.
2. **Motive plus opportunity.** Consider expected reward, relative power, condition/readiness, supplies, fresh intelligence, route cost, personality, goals, relationships, and consequences. Pirates are predatory, not suicidal or omniscient.
3. **Contextual, inspectable strength.** Separate personal capability, reputation, vessel condition, crew readiness, and organizational reach. Expose a breakdown; no opaque universal rating decides every outcome.
4. **Comparable rivals are valid prey.** Permit reasoned pirate-on-pirate attacks between similar forces; do not target only helpless vessels or guarantee safe victories.
5. **Growth is paid for.** Cargo, silver, crew, vessels, affiliation, and reputation change only through finite transfers, costs, losses, or explicit status changes. No free crew, loot, ships, or faction power.
6. **Named characters have agency.** Their motives and capabilities influence choices/outcomes; events preserve who acted and personal consequences.
7. **Knowledge is limited.** Use sightings/reports with provenance and age. Preserve uncertainty; no default live enemy-position access.
8. **Diplomacy remains distinct.** Relationship status is not a signed treaty. Violence may cause grudges, fear, respect, truces, or treaty breaches only through modeled transitions.
9. **Strategic scope only.** NPC encounters remain abstract chart-scale events. Do not modify tactical shipboard/player combat, ship construction, or unrelated crew simulation.
10. **Preserve parallel work.** Inspect the current worktree before edits and retain unrelated/concurrent changes.

## Target gameplay loop

1. A captain/organization holds a persistent ambition and actionable goal: seize stores from a patrol, avenge defeat, build notoriety, protect a route, or regroup after losses.
2. It has only its own and reported/sighted knowledge of targets and routes.
3. It evaluates feasible pursuit, attack, evade, negotiate, patrol, trade, provision, or regroup actions.
4. Ships follow valid chart lanes. At an actionable encounter, a strength-aware resolver determines a bounded outcome.
5. Loot/losses transfer through finite ledgers. Outcomes update ships, crew, resources, named captains, organizations, relations, memories, and goals once.
6. Structured news explains who acted, why, against whom, where, and what materially changed.
7. The actor adapts: success can fund repairs/provisioning/recruitment or fulfill a goal; loss can force retreat, create a grudge, prompt protection, or change the next goal.

## Implementation phases

### Phase 0 — Baseline, audit, and ownership

- Inspect the current worktree, generated/authored save schemas, action-to-execution path, legacy raid/trade behavior, news UI, and tests before editing.
- Capture reproducible baseline runs over fixed seeds and short/medium/long histories. Record event counts by kind, substantive conflicts/economic outcomes, retained-news composition, goal progress, strength spread, runtime, and conservation invariants.
- Name the single authority for every mutable field: ship cargo/silver/crew/condition; market stock/free population; organization cohesion/assets; person XP/reputation/career; society goals/events/memories.
- Document generated vs authored behavior and required migration defaults. Make no schema change before ownership is clear.

**Gate:** baseline is reproducible; data ownership and compatibility boundaries are explicit; no duplicate ledger or tactical-combat scope is introduced.

### Phase 1 — Strength and motivation contracts

- Define explainable encounter power from crew count/readiness, vessel condition, supplies, captain command/tactics/seamanship, and relevant support.
- Keep personal capability distinct from command power and reputation. Define organizational strength from eligible assets, readiness, resources, cohesion, and reach without counting any asset twice.
- Define goal lifecycle and fields: target, reason/source event, priority, commitment, progress, prerequisites, review, completion, failure, and cancellation. Goals require state transitions, not merely reaching a node.
- Support actionable predatory ambitions: acquire wealth/stores, raid government shipping, defeat a rival, seek revenge, gain notoriety, protect independence, and recover from losses. Individual and organizational goals may conflict.
- Add controlled tests proving motive, personality, circumstances, relative strength, or commitment can alter a feasible choice without overriding feasibility or acting as an absolute rule.

**Gate:** decision explanations expose inputs and strength breakdown; travel alone cannot complete goals.

### Phase 2 — Intelligence, target choice, and piracy intent

- Build targets only from plausible knowledge: co-location/sighting, recent report, or modeled route/port opportunity. Keep source and observation age.
- Score expected usable loot, target type, likely resistance, relative power, own condition/readiness/supplies, route cost, escape risk, relationships, grievance, mandate, captain ambition, and retaliation consequences.
- Make pirate organizations predisposed to consider Marine/government and rival-pirate targets. Independent/merchant crews remain more selective; Marines pursue lawful mandates rather than pirate plunder.
- Permit comparable-strength rival attacks when motive and opportunity justify the risk. Include evade/retreat choices where modeled; avoid both “attack only weak ships” and one global hostility threshold.
- Preserve bounded cooldowns, commitment/hysteresis, deterministic tie-breaking, valid chart routes, and retreat orders that are not immediately overwritten.

**Gate:** fixed scenarios demonstrate an opportunistic Marine raid, a reasoned comparable-rival attack, an outmatched pirate evasion/regroup, and Marine pursuit. Choices replay deterministically with inspectable score inputs.

### Phase 3 — Strength-aware encounters and finite plunder

- Replace binary winner/retreat-only resolution with bounded outcomes based on relative power and circumstances: decisive/costly win, disengagement, retreat, or surrender where justified. Larger forces must not win automatically.
- Define initial material scope. Prefer cargo/silver seizure, damage, retreat, and explicit captive/crew transfers through existing finite state. Do not add deaths until population accounting and migration support them.
- Limit loot by target stock, carrying capacity, and attacker ability; transfer atomically. Account for contested cargo/escape. Never mint cargo or silver.
- For Marine targets, transfer available stores/valuables and apply a modeled political/heat response. For rival pirates, support plunder, ransom/captives, humiliation, theft, or feud only where actual state transitions exist.
- Define the location/status of every surrendered or captured person. No person or vessel may be simultaneously active, captured, and free. Ransom, release, recruitment, and transfers must be finite, recorded movements.
- Emit one authoritative event with participants, location, cause, power context, damage, transfers, and outcome; apply effects exactly once across save/load.
- Decide vessel-loss boundaries. Prefer disabled/withdrawn state before destruction or acquisition; hull loss requires explicit ownership/save rules.

**Gate:** tests prove real finite plunder, bounded outcome changes, retreat, and crew/cargo/silver conservation for wins, disengagements, and losses.

### Phase 4 — Named-character agency and growth

- Link captains to commanded ships and persist status, ambition/goal, relationships, memories, achievements, XP, and reputation.
- Award bounded experience only for meaningful completed work/outcomes. Avoid farming through no-op ticks or merely choosing/travelling toward an action.
- Make reputation follow witnessed/known success or failure; do not grant omniscient global updates without report propagation. Keep it separate from capability.
- Support modest, auditable organization growth: plunder can pay for provisioning/repairs and, if a finite source exists, recruitment; success may improve cohesion/reach/reputation. Losses consume resources, damage readiness, reduce cohesion, or force regrouping. Centralize caps/costs.
- Audit the existing finite unassigned population/market before using it as a recruitment source. If no valid source exists, defer recruitment rather than invent crew. New ships require a finite source/model or remain deferred.
- Advance personal/org goals from event-linked outcomes; reassess after major success/loss, target disappearance, or review time. Record why goals change.
- Memories of defeat, victory, betrayal, rescue, debt, and promise apply once and can influence future action selection.

**Gate:** successful captains measurably improve through earned outcomes; failure can change behavior; resources, people, and assets remain finite across save/load.

### Phase 5 — Story chronicle and player inspection

- Keep structured event truth separate from presentation; prose is never the source of simulation state.
- Prevent routine arrivals/trade from evicting important events. Evaluate suppression/deduplication, aggregation, a high-priority retained set plus a recent feed, or a bounded archive/index. Pick one simple save-compatible policy.
- Retain discoverable fights, captures, major loot, goal outcomes, betrayal, alliance, defection, loss, and major growth according to impact, not randomness.
- Reports identify named people/factions, motive/cause, opponent, location, outcome, material consequences, and aftermath without leaking unknown facts.
- Profiles/organization inspection distinguish known ambition/activity, reputation, estimated strength/readiness, assets, and recent history. Views remain read-only and hide secret goals/exact hidden enemy state.
- Provide a compact starting chronicle from bounded prehistory, retaining references to consequential events.

**Gate:** key causal stories remain retrievable despite routine traffic; every headline matches an actual transition and respects player knowledge.

### Phase 6 — Saves, balance, and validation

- Add explicit versioned migration for new fields. Preserve authored and legacy saves without regenerating identities, duplicating assets, replaying history, or losing old news. Validate before replacing the live world.
- Test deterministic generation/continuation, small-vs-large time chunks, save/load during pursuit, before/after encounters and during active goals, plus bounded catch-up work.
- Run a fixed diverse seed matrix at short, medium, and long histories. Report distributions of substantive raids, outcomes, plunder, losses, goal progress, pirate-vs-Marine and pirate-vs-pirate conflict, and peaceful stories. Investigate both inactivity and constant combat; do not tune to one seed.
- Audit person/crew/captive population, vessel ownership, cargo/silver, market stock, reputation/XP caps, goal/event references, memory bounds, and agreement validity.
- Run focused NPC/voyage/economy/save suites, full suite, compile checks, and a save-free seeded simulation/native UI walkthrough. Inspect transcripts and screenshots; green tests alone do not prove story quality.
- Verify tactical-combat files/formulas were untouched and unrelated concurrent work remains intact.

**Gate:** fixed-seed simulations show varied, motive-driven, consequential stories with recorded evidence for determinism, conservation, compatibility, and quality.

## Overall acceptance criteria

- Pirates sometimes execute profitable attacks on Marine/government targets and rival pirates, including credible comparable-strength contests; frequency is balanced empirically, not scripted.
- Outmatched/poorly supplied captains sometimes evade, regroup, seek aid, or choose safer targets. Bold/greedy/vindictive motives can affect risk without overriding feasibility.
- Encounters have varied strength-aware outcomes. At least one outcome transfers finite plunder and consequences affect future capability, goals, relations, or resources.
- Named captains' motives/skills influence decisions and outcomes; earned careers, resources, and reputations can grow or suffer.
- Comparable-pirate encounters can produce lasting rivalry, debt, revenge, submission, or settlement through actual modeled transitions.
- No free population, vessels, cargo, silver, or progression; no unmodeled deaths/disappearances; all transfers conserve authoritative ledgers.
- Consequential news survives routine port traffic and is causal, bounded, deterministic, and knowledge-filtered.
- Same-state continuation is deterministic; save/load, legacy migration, and chunked advancement preserve outcomes.
- Authored compatibility, voyage/economy behavior, tactical combat, and unrelated concurrent changes remain intact.

## Explicit non-goals / deferred

- Player naval combat, cannon simulation, boarding resolver, or changes to the isolated shipboard tactical-combat system.
- Individual simulation of all ordinary crew; new population generation during normal play; unrestricted recruitment or vessel spawning.
- New shipbuilding, player contracts/bounties, or a second economy.
- Automatic wars, betrayal, deaths, or dramatic events without feasible motives and state transitions.
- Guaranteeing a particular narrative, winner, event rate, or protagonist in every seed.

## Relevant files and verification

- `pirates/npc_generation.py` — seeded people/org/vessel/goal generation and headless prehistory.
- `pirates/npc_decisions.py` — utility scoring and deterministic choice.
- `pirates/society.py` — generated knowledge, target candidates, hourly actions, abstract clashes, diplomacy, memories, news, and save state.
- `pirates/voyage.py` — chart movement and legacy finite trade/raid/captive handling; audit before reuse.
- `pirates/game.py` — generated-world entry point, clock, and save/load.
- `pirates/management.py` — profiles, sea chart, and recent reports.
- `tests/test_npc_generation.py`, `tests/test_npc_decisions.py`, `tests/test_npc_society.py`, `tests/test_voyage.py`, and relevant economy/save tests.
- Existing design references: `NPC-GENERATION-PLAN.md` and `NPC-SOCIETY-PLAN.md`. This plan scopes the next goal to predation, strength, growth, named-person agency, and story retention; it does not claim broader deferred features are implemented.

## Re-entry checklist

1. Read this plan and load `pirates-of-miska`.
2. Inspect `git status`; preserve unrelated and concurrent changes.
3. Run baseline NPC tests and capture a fixed-seed transcript before edits.
4. Complete Phase 0 and record decisions/evidence before schema changes.
5. Implement one phase at a time; update this plan with evidence and verification.
6. Run focused tests after each phase, then full tests and multi-seed simulation.
7. Do not mark a phase complete on code inspection alone; demonstrate its gate.
