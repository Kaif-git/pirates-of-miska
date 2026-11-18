# First Expansion — Consequential Voyages

Date: 2026-11-18
Status: implementation in progress under the voyage-expansion goal.
Parent: `PIRATES-OF-MISKA-MASTER-PLAN.md`.

## Initial Phase 0 audit notes (Nov 18, 2026)

- `World.advance()` in `pirates/game.py:247-257` routes elapsed ticks through the canonical ship/world simulation. `on_ship_second()` updates NPC fleets/society and then `VoyageSystem.step_second()` (`game.py:231-245`). Encounter/arrival state interrupts the current large advance through `Ship.advance()`; `voyage.py:393-394` defines the interrupt predicate. `voyage_menu()` currently offers only watched 10-minute/1-hour/6-hour advances with UI polling (`management.py:733-761`). New modes must be policies over canonical time, not a parallel simulator. Existing voyage event ordering is NPC/society update, then sighting handoff, then voyage progress for each second; arrival/encounter must remain terminal interruption boundaries.
- `VoyageSystem` already persists route, elapsed/duration, progress, encounter, cannon-combat and sailing state (`voyage.py:214-228,233-302,435-523,525-630`). Sightings interrupt the sailing second. Daily crew/world activity continues under the same simulation.
- `NpcFleetSystem` has persistent stable ship IDs, 5 authored vessels or a generated roster, hourly deterministic movement, one-time player sighting IDs and source-time/position reports (`voyage.py:633-709,811-913`). The player currently learns position/crew/condition when close; sightings are issued once per ship ID. It does not yet offer ordinary hailing or assistance. Do not replace the existing NPC identity/report ledgers.
- Delivery contracts already exist in `pirates/economy.py:258-365`, are generated from finite trade catalog templates and use a four-day deadline (`content/trade_rules.json:22-29`), issuer escrow, completion/expiry, funds and trader-reputation effects. The harbor UI is in `pirates/trading_ui.py:279-329`; tests in `tests/test_trade_expansion.py:66-206` cover escrow, fungible cargo, save and manual settlement at the destination harbor office. They are not integrated into voyage route selection or arrival. Acceptance does not reserve specific cargo; delivery consumes matching fungible trade cargo at the destination. Preserve manual office settlement unless an API/UI change is designed and tested; do not present an objective as settled merely because the ship arrived.
- `VoyageSystem.plan()` uses a charted six-node graph, shortest path or a waypoint detour and a single departure-time deterministic wind vector (`voyage.py:15-25,131-176,178-212`). Verdant–Cinder alternatives via Glass Shoals and Government Patrol Lane have equal geometric length. The planner does not enumerate alternatives and currently models no generalized risk/exposure differential; the routes can differ in traversed chart nodes and therefore NPC sighting opportunities. A route must not be advertised as safer/faster until a runtime difference is measured.
- There is no shipboard/sea storm forecast or storm consequence authority in `pirates/`; `wind_at()` only selects four deterministic wind vectors. Rain collection is a separate shipboard water mechanic, not an ocean-weather model. The requested storm loop therefore needs either an explicitly minimal, bounded voyage-weather extension or a scope revision backed by feasibility evidence; do not claim it reuses an existing storm system.
- Existing ship-to-ship artillery is deterministic from a persisted seed, consumes real finite shot and damages the player/NPC hull (`voyage.py:435-523`); other pirate boardings use another campaign-combat path. Keep both boundaries explicit.
- Current save format is world version 3 and serializes voyage and NPC fleet state (`game.py:418-438`). Existing tests cover encounter/arrival interruption, save/reload and chunk-equivalent voyage/NPC/economy/ship state (`tests/test_voyage.py:233-315`). The prior goal's full repository pytest process is still active in the environment; no overlapping test run was started during this static audit.
- Prior measured baseline: a light-wind Cinder→Verdant voyage took 106,999 ship-seconds versus a 14,606-second estimate; a saved replay took 106,099 ship-seconds and 311 wall-seconds (`SHIPBOARD-PLAYABILITY-AND-BALANCE-PLAN.md:54`). This is a material ETA and responsiveness problem, not proof of a stuck simulation. Advance controls must retain responsive cancel/interrupt behavior, and route ETA presentation needs calibration against this evidence.

### Phase 0 integration decisions

- Time/event authority remains `World.advance()` → per-second ship/world/NPC hooks → `VoyageSystem.step_second()`. A new skip can batch calls only if it remains equivalent, interruptible, and can stop on the same actionable state.
- Reuse `Economy` contract state and APIs. Cargo stays fungible; do not add a parallel reserved-freight ledger. Any protection from trade/supply use must be implemented through existing cargo/storage accounting or deferred until that authority is understood.
- Keep the player-facing route alternatives honest: chart/node/NPC information is the currently supported tradeoff; no unsupported safety or duration claim. First audit route runtime and ETA before tuning.
- Contacts begin from existing one-time, timestamped NPC reports and stable vessel IDs; contact outcomes must revalidate the live vessel/crew at decision time.
- No sea weather exists. If storm remains in scope, implement a bounded voyage-only forecast/event whose deterministic derivation and ship-condition effects are explicitly documented; do not retrofit the unrelated land/weather or rain collection system.
- Arrival is currently a persisted voyage status transition, not contract settlement. Report objective arrival separately from later harbor-office settlement and retain existing ownership of reward/cargo/reputation changes.

## Phase 1 implementation notes

- The existing persisted `voyage["encounter"]` plus `status` and the terminal `arrived` status are the event lifecycle for current sighting/arrival interrupts. No extra generic event ledger or duplicate checkpoint has been introduced. Future storm state must be explicitly saved as part of this voyage object and validated on restore.
- Added passage choices for fixed watches, exact next-midnight boundary, next actionable event and arrival. All call `World.advance()` in bounded 60-second batches; encounter/arrival still interrupt in the existing per-second hook. Esc is polled between batches while the chart redraws every ten in-game minutes to avoid rendering overhead. Event/arrival modes use a generous estimated-ETA-based horizon capped at 30 days and explicitly report when that horizon ends without resolution.
- The exact tick calculation for next midnight is in `ticks_to_next_day_boundary()` in `pirates/voyage.py`; it uses canonical `TICKS_PER_DAY`, not a hand-coded calendar offset. Focused test added; runtime/focused suite verification is pending because two earlier full-suite Python processes were still active during this edit.
- Voyage planning now enumerates distinct equal-shortest chart routes. A live probe confirmed Verdant–Cinder paths via Government Patrol Lane and Glass Shoals are both 5.841619 chart units. The route picker displays intermediate nodes and timestamped existing lookout reports, explicitly warning that stale/missing reports are not safety forecasts. Accepted matching delivery contracts now appear in the pre-departure review with item, quantity, destination, deadline and escrow; actual reward/cargo settlement remains the existing manual harbor-office action.
- Delivery cargo reservation is derived from active Economy contracts (no duplicate reservation ledger or save schema): ordinary trade, allocation, equipment issue, mending and packing can spend only unreserved quantities; the matching delivery can consume its own contract quantity. Tests added for sale/removal protection and route alternatives. Syntax compilation passed; pytest execution is still deferred due to the running full-suite and ten-year simulation workloads.
- Sightings now support a one-time signal exchange with a live non-pirate stable ship ID. A visibly distressed non-pirate can then receive one iron unit for up to 20 condition using the existing NPC repair scale; aid requires access beside the hold, consumes real trade cargo and a minute, and promises no payment/favor. The NPC fleet snapshot persists `player_contacts`; old snapshots default to none and restore validates identity/time/outcome. Hostile pirate combat/boarding/retreat paths remain in place. Focused persistence/resource tests were added; run is pending active pytest/sim jobs.
- Storm implementation scope: only charted routes with an intermediate waypoint get a low-confidence squall watch. Wind-specific authored eligibility is 15% (light), 20% (trade), or 30% (cross/headwind); a stable hash of departure ticks, route and deterministic departure wind decides the persisted trigger/severity. The forecast exposes probability/node, not the hidden roll or certainty. At the first waypoint, an eligible squall stops passage; a chart-validated detour updates actual route distance/estimated duration, or pressing on applies bounded hull/rigging damage through existing ship-condition fields. This is a first-slice authored voyage hazard, not an ocean climate simulation or general weather authority; frequencies/damage remain subject to scenario measurement.
- Arrival report implementation: departure/arrival checkpoints now capture canonical ticks, funds, fungible cargo, hull/rigging and the existing contract-ledger state. `arrival_report()` is read-only/repeatable and distinguishes actual elapsed time from the original departure estimate, recorded outcomes, objective arrival from office settlement, and deadline expiry. World advancement finalizes the arrival checkpoint after daily Economy updates. The menu offers the report again after arrival; the immediate arrival interruption presents it without altering any contract.
- Follow-up stays within existing authorities: stable contact IDs and timestamped sighting reports remain on the NPC fleet save and chart; no vessel location is refreshed or treated as current merely because contact was made. Delivery objectives remain active at arrival and become completed only through the existing harbor-office `deliver_contract()` path, whose active-status check prevents duplicate payout. Arrival advice points to office review or local offer inspection and guarantees no new offer/reward. Tests cover the report before/after manual settlement and contact visibility after reload.

### Integration verification in progress

- `python -m py_compile pirates/voyage.py pirates/management.py pirates/game.py pirates/economy.py tests/test_voyage.py tests/test_trade_expansion.py tools/preview_voyage.py` passed after the latest edits.
- `git diff --check -- pirates/voyage.py pirates/management.py pirates/game.py pirates/economy.py tests/test_voyage.py tests/test_trade_expansion.py tools/preview_voyage.py VOYAGE-OPPORTUNITIES-EXPANSION-PLAN.md` reports no whitespace errors; Git prints only its existing LF→CRLF conversion warnings.
- The server restart detached the shell waiting for the pre-edit `python -m pytest -q`; with user authorization, verified command PID 20324 was stopped and no result was recovered. The earlier 10-year simulation PID 4836 is also gone without a result. A later process audit found unrelated concurrent pytest PID 9568 and 3-year simulation PID 15028; both are left untouched. Fresh focused voyage pytest and native preview still need an idle window to avoid overlapping these workloads.
- `tools/preview_voyage.py` now captures route choice, departure review, passage choices, underway/chart, ordinary arrival/report, nearby-ship contact, blocked aid, feasible aid and squall decision/outcome. Controlled aid/squall fixtures assert exact time, cargo and condition deltas into `verification.json`; ordinary passage suppresses the synthetic squall. Native capture and visual inspection remain pending.
- Fresh focused run (`python -m pytest -q` over voyage, trade, economy, provisioning, storage, crew, NPC, combat-NPC, harbor-navigation and voyage-save suites): **261 passed in 159.44s**. The first run's two real failures exposed encounter-action time truncation: `World.advance(6000)` stopped after one second while a sighting remained pending. `VoyageSystem.advance_encounter_action_time()` now permits that explicit action cost through canonical ship/world hooks while preserving the ordinary passage interruption; hail/aid tests assert 60 ship seconds. The other two failures were stale test assumptions; the same-tick sighting fixture now passes the existing 30-minute eligibility gate and the UI test checks the current `Review voyage order` title. The final combat-action guard and its regression then passed targeted verification (2 tests, 2.19s). Ignored/missing-contact and contract-route UI integration regressions have since been added; their execution is pending.

## 1. Objective

Reform ordinary travel into **prepare → commit → advance → decide → arrive → choose another opportunity**.

Deliver one complete, replayable single-ship voyage loop. Ordinary ship life runs automatically. The player intervenes for meaningful situations and returns with understandable consequences and a reason to sail again.

This is not a daily-chore expansion, a new world simulator or the full master plan. Logs support the experience; they are not the main gameplay.

## 2. First-release scope

Deliver these connected capabilities:

1. A finite, paid delivery opportunity with a clear deadline and terms.
2. At least two usable route alternatives with actual, disclosed tradeoffs.
3. Advance to tomorrow, next actionable event or arrival through the existing simulation.
4. Persistent ship sightings with investigate/hail, assist where appropriate, continue/evade and existing combat handoff where valid.
5. A forecastable storm situation with avoidance/commitment and meaningful response priorities.
6. An arrival report that settles obligations once and retains a contact, consequence or follow-up lead when the outcome warrants it.

Do not guarantee a storm, fight, rescue or reward on every voyage. The verification scenario may force authored conditions, clearly labeled as fixtures; ordinary play must use actual eligibility rules.

### Explicit exclusions

- Floren, Fusion, sea monsters, land excavation/adventure and fleets.
- General-purpose autonomous agents, a new faction economy or replacing concurrent NPC simulation.
- Full contract negotiation, crew personal spending, prisoner systems or captain recovery after lethal defeat.
- Fishing, crafting expansion, new skill currencies or mandatory daily progression collection.
- A complete procedural ocean/map generator. Use the current route graph unless the audit proves a small extension necessary.
- Rewriting tactical combat or assuming the isolated duel system is campaign combat.

These exclusions do not remove them from the master vision; they protect a testable first delivery.

## 3. Existing integration points to audit first

Current source entry points, checked when this plan was drafted; line numbers may move:

- `pirates/voyage.py:15-28` — chart nodes, lanes, officer roles and free-time orders.
- `pirates/voyage.py:131-176` — routing and deterministic wind.
- `pirates/voyage.py:178` onward — planning, departure and sailing progression; inspect all before changing.
- `pirates/voyage.py:303-343` — existing captain activity and crew drill hooks.
- `pirates/voyage.py:390-433` — sighting interruptions, continuation and turn-back behavior.
- `pirates/voyage.py:435-523` — existing naval engagement; uses live NPC vessel IDs and finite ammunition.
- `pirates/voyage.py:525` onward — snapshot and restoration.
- `pirates/management.py:416,424,547,580,626` — time advancement, map/public sightings, public identity, naval UI and voyage menu.
- `pirates/ship.py` — canonical per-second crew/work/supply progression and interruption hook.
- `pirates/game.py` — canonical world advancement, save integration, harbor transitions and world adapters.
- `pirates/provisioning.py` — existing read-only readiness/forecast authority.
- `pirates/economy.py`, `pirates/society.py`, `pirates/npc_decisions.py` — audit money, cargo ownership, contacts, identity and world-event interfaces before adding state.
- `tests/test_voyage.py`, `tests/test_provisioning.py`, `tests/test_harbor_navigation.py`, `tests/test_npc_society.py`, `tests/test_ship_social_events.py` — baseline coverage to inspect and extend.
- `tools/preview_voyage.py`, `tools/preview_supply_voyage.py` — native/scenario patterns to reuse.

Earlier shipboard work and repository-wide tests may still be active. Inspect current goal/process state and working tree; do not start overlapping long runs or overwrite concurrent changes.

## 4. Non-negotiable rules

- One authority for time, funds, cargo, ship damage, NPC identity and relationships.
- All advancement modes produce equivalent simulation for identical actions and elapsed time.
- Stop at an actionable event before consuming the remainder of requested travel time; never resolve it silently.
- No hidden time advancement from inspecting reports or options.
- Show known costs, limitations and commitment before an action. Distinguish estimates from guarantees.
- Save before/within/after encounters without rerolling outcomes, repeating rewards or losing pending obligations.
- Information comes from observations or attributed reports; no hidden NPC intent or exact future weather disclosure.
- No teleporting back to port, free salvage, duplicated cargo, invented rescuable goods or automatic full repairs.
- Encounter actions never bypass ordinary access, available crew, finite stores or essential needs.
- First-version lethality remains; do not add an implicit immunity or capture guarantee. Existing combat limitations must be reported honestly.

## 5. Phase 0 — Audit and playable baseline

### Tasks

1. Map voyage status transitions, time ownership, encounter triggers, weather, arrival and save schemas.
2. Trace public NPC sightings versus actual world state; retain stable IDs across encounters.
3. Trace cargo allocation, delivery ownership, money and recipient validity.
4. Reproduce a paid normal voyage, sighting, continuation, turn-back and arrival using current entry points.
5. Check route estimates against observed sailing, including light/headwind delays. Do not build deadlines on a known misleading ETA.
6. Identify stale UI labels and existing behavior rather than assuming every menu description is accurate.
7. Record a field/source ownership map and finalize the smallest supported content slice.

### Gate

A baseline report names existing authorities, current failures, preserved behavior and explicit integration decisions. No new simulator or duplicate encounter framework is introduced without evidence it is necessary.

## 6. Phase 1 — Safe accelerated passage and event lifecycle

### Behavior

- Tomorrow means the next calendar-day boundary; display the requested target before commitment.
- Next event advances until an actionable interruption or arrival, with an explicit safety horizon/cancel behavior if neither occurs promptly.
- Arrival mode continues toward arrival but still stops for actionable events.
- Arrival, an already-active encounter and terminal states must not be advanced past.
- Routine logs/news do not force stops unless the player enables that preference.
- Long advances remain responsive; do not introduce an alternate coarse simulation that changes outcomes.

### Model

Reuse existing voyage status/encounter state where possible. Define a validated event record with stable identity, type, source, observed time/location, stage, known information, offered actions, committed outcome and resolution status.

Finalize bounded retention, processed checkpoints and same-tick ordering during design. No unseeded RNG; choice outcomes cannot reroll through viewing or loading. Persist decision state, not renderer objects.

### Gate

Tests prove day/event/arrival stops, same-tick ordering, partial-second advancement, pending-event reload, no duplicate trigger and equivalence with small-step advancement. Inspect UI cancellation/responsiveness and existing arrival behavior.

## 7. Phase 2 — A reason to sail and a meaningful route

### Delivery opportunity

Adapt the existing catalog-backed, escrow-funded harbor delivery system (`Economy.contract_offers/accept_contract/deliver_contract/update_day`) rather than creating a second quest/contract/reputation authority. Integrate one supported contract into voyage preparation/route choice/arrival; do not rebuild the existing offer board.

The integrated delivery must have:

- Stable issuer/recipient or explicitly authored port service.
- Exact cargo, origin, destination, acceptance terms and deadline.
- Known reward and any deposit/penalty; choose these only after economic measurement.
- Explicit acceptance, active, delivered, late/failed and closed states.
- Recipient/location and cargo revalidation at settlement.
- Delivery goods have one owner and cannot be freely sold, consumed, duplicated or delivered twice. Use existing cargo/accounting mechanisms; do not count the same goods as both provisions and sealed freight.
- Delay and failure are understandable; no retroactive deadline changes.

### Route choice

Use two paths in the existing graph where feasible. Present distance, estimated duration/uncertainty, known exposure and readiness through current reports.

Each route needs an actual difference: e.g. time versus reported patrol/exposure or a forecast weather risk. Do not label one dangerous if the simulation does not enforce a difference. Do not promise a safe route is encounter-free.

### Gate

Acceptance/departure/late arrival/delivery/abandonment/load are atomic and conserved. Route choice affects runtime behavior. A normal successful voyage is feasible without buying an unnecessary mountain of supplies or relying on a perfect wind roll.

## 8. Phase 3 — Other ships as situations

### Encounter stages

1. **Sighting:** show only observed clues. Do not instantly reveal cargo, allegiance or intentions without support.
2. **Approach or avoidance:** expose time/exposure costs; investigate only when feasible.
3. **Contact:** identity/condition can become known through observation or communication.
4. **Decision and resolution:** commit costs and persist consequences exactly once.

### Initial authored content

- One ordinary contact with useful information or a bounded transaction.
- One genuinely distressed vessel with an explicit assistance request.
- Existing hostile/combat handoff retained where compatible; do not force a new boarding system into this phase.

Hailing must not automatically equal combat. Assistance uses real time/resources; refuse unavailable aid before charging. No automatic friendship bonus for repeatedly reopening a conversation. Declining a rescue is not automatically witnessed worldwide.

If a live NPC can disappear, move or die while an encounter is pending, define whether the encounter pins its participation or revalidates it. Never silently substitute another ship with the same display name.

### Gate

Cover investigate, aid, refusal, continue/evade, unavailable resources, vanished/departed target and save/reload at each stage. Encounters retain identity and only publish consequences supported by witnesses/reports. Assistance and ordinary contact must be useful enough that ignoring every sighting is not always optimal.

## 9. Phase 4 — A forecastable storm

### Behavior

- Derive warnings from the existing deterministic wind/sailing model and a minimal explicitly scoped voyage-weather extension if required. There is no current sea-storm authority in `pirates/`; do not represent onboard rain collection as a storm forecast. Separate forecast uncertainty from the actual event.
- Offer avoid/detour or press on where geographically possible, with estimated costs.
- If caught, offer a small set of operational priorities, not repeated per-tick emergency prompts.
- Priorities affect genuine existing systems: course/progress, rigging work, repair or cargo handling where implemented.
- If jettison is offered, specify exact unreserved eligible cargo, ownership consequences and mass accounting. Otherwise omit it.
- No free instantaneous work or arbitrary damage unrelated to exposure, preparation and available capabilities.
- Bound duration and interruptions; support recovery, exit and arrival without event deadlock.

Do not create a second general weather, flooding or structural-damage authority. If a minimal voyage-specific storm state is necessary, document its ownership, derivation and interfaces to existing sailing, crew work and ship condition before implementation; keep it bounded to the first voyage slice.

### Gate

Compare prepared/unprepared and avoid/press-on cases from matched states. Demonstrate distinct outcomes without a universally dominant choice. Verify active-storm save/resume, depleted resources, unavailable workers, damaged rigging and simultaneous arrival/encounter boundaries.

## 10. Phase 5 — Consequences, news and the next departure

- Settle delivery once at the valid destination.
- Show arrival progress, actual duration versus estimate, funds/cargo changes, condition and important outcomes.
- Crew skill details remain in personal logs; avoid a compulsory stat-gain reward screen.
- Surface relevant existing world news with source/time. If no supported news interface exists, add a small read adapter rather than a new world event simulator.
- Retain one or more supported consequences: a contact, recorded location, changed relationship, unresolved lead or follow-up offer.
- Follow-up must reflect the outcome and remain optional. Do not fabricate gratitude from dead/unaware people or guarantee a new reward after failure.
- Bound journals and distinguish recorded history from currently actionable opportunities.

### Gate

A complete departure→interruption→arrival run leaves an understandable outcome and an actionable reason to sail again when warranted. Reloading arrival cannot repeat settlement or regenerate offers.

## 11. Phase 6 — Balance, verification and handoff

### Required scenario matrix

| Scenario | Required evidence |
| --- | --- |
| Ordinary delivery, both routes | Duration, finances, supplies, outcome and meaningful route difference |
| Quiet passage | No manufactured conflict; usable accelerated travel |
| Contact and genuine aid | Time/resource conservation, identity and persistent outcome |
| Ignored sighting | Safe continuation; no invented global consequences |
| Hostile contact | Existing combat boundary, explicit risk and valid disengagement behavior |
| Storm avoid/press on | Forecast limits, costs, work/condition/progress differences |
| Late/failed delivery | Terms enforced once, no negative/duplicated goods |
| Shortages or blocked access | Honest blockers, no free actions or hidden bypass |
| Save within each event | Identical continuation, no reward/trigger duplication |
| Different advancement chunks | Same state at equivalent decision boundaries |
| Missing NPC/issuer/recipient | Defined stable outcome rather than crash/substitution |

Record runtime, simulated time, interruptions per voyage/day, spending, supplies, damage, work completion, reward and follow-up outcomes. Measure before tuning. Use ordinary runs for frequency; controlled fixtures prove behavior/layout only.

### Verification

- Unit/state-machine, negative, conservation and migration tests.
- Focused voyage/economy/provisioning/storage/crew/NPC integration suites.
- Native departure, route, advance, sighting, aid, storm, blocked-action and arrival captures at supported sizes.
- Compile and whitespace checks, including new untracked files.
- Attempt the repository suite and classify failures with evidence; do not mask unrelated concurrent failures or weaken acceptance thresholds.
- Update this plan with actual commands, reports and limitations; update the global project skill.

### Completion checklist

- [ ] Existing authorities and baseline documented.
- [ ] Accelerated travel stops correctly and preserves simulation equivalence.
- [ ] A finite delivery and two meaningful route choices work end to end.
- [ ] Ship contacts and aid create real decisions and persistent outcomes.
- [ ] Storm warnings and responses work through supported systems.
- [ ] Arrival settles once and carries outcomes into future opportunities.
- [ ] Save migration, pending-event reload and deterministic continuation pass.
- [ ] Costs, information boundaries, NPC identity and conservation pass negative tests.
- [ ] Matched balance scenarios and native UI evidence recorded.
- [ ] Focused/full-suite status, remaining limitations and handoff documented.

## 12. Decisions to settle during Phase 0, not silently assume

- Deadline scale and economic terms, based on actual sailing variability.
- Cargo ownership/escrow representation and legal consumption/sale rules.
- Which existing NPCs or authored services may issue/receive deliveries.
- Public-information and news adapter contracts.
- Exact route-risk/weather connection and storm scope.
- Active-event time behavior and NPC participation while decisions are pending.
- Current combat consequences versus the master plan's eventual lethal-defeat direction.
- Retention/cooldown tuning and how to avoid repeated contact farming.

The delivery is complete only when the connected voyage is worthwhile—not merely when its menus, counters and isolated tests exist.
