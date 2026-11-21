# Ship Preparation and Organization — MVP M2 Implementation Plan

Date: 2026-11-19
Status: implementation guidance only; no gameplay changes or passing gates are implied by this document.
Parent: `LAWFUL-TRADER-MVP-PLAN.md` §7 and M2 (§15).
Prerequisite: the authored archipelago foundation recorded in `AUTHORED-ARCHIPELAGO-IMPLEMENTATION-PLAN.md`.

## 1. Deliverable

Make preparing the authored trading ship a coherent, consequential player activity using the existing construction, stock, crew and readiness authorities.

> Launch the authored scenario, buy finite supplies/materials, construct or rearrange a useful space, designate storage, have crew physically move stock, diagnose and fix a blocked job, equip and assign workers, inspect readiness, and sail with disclosed risks. Save/reload must preserve unfinished work and in-transit materials without duplication.

M2 connects and verifies existing systems before adding missing mechanics. It must not become a new colony-management game, a mandatory departure checklist, or a combat integration project.

## 2. Scope and boundaries

### Included

- Construction and rearrangement with real materials, worker time and access.
- Functional spaces/fittings: sleeping, galley, cargo, workshop, medical and brig areas, using only supported functions and honestly labelling unsupported ones.
- Stock designations, supported filters/priorities, mass/bulk capacity, reservations and physical hauling.
- Merchandise versus allocated ship supplies: clear ownership, location and conversion rules.
- Crew equipment, labor priorities, officer assignments and meaningful staffing coverage.
- One read-only readiness summary, actionable job blockers and informed departure warnings.
- Persistence, conservation, native UI evidence and a complete preparation-to-departure walkthrough.

### Excluded

- Campaign combat, NPC decks, prize ships, bounty/custody law and new medical outcome mapping.
- Strategic world simulation, new generated history, fleets, production chains, land content or swimming.
- Full M3 economic balancing, M4 relative-motion/at-sea trading, or M7 skill/salvage expansion.
- New generic XP meters, automatic room-name bonuses, free instant hauling or daily reward-collection chores.

Preserve legacy/generated mode, the protected authored save slot, current optional employment commitments, and concurrent combat/simulation work. Never modify vendored engine behavior for a game-specific preparation screen if an app adapter suffices.

## 3. Source authorities to inspect first

These are verified entry points, not claims that every required behavior already works. Recheck current callers and line positions before editing.

| Authority | Entry point / responsibility |
| --- | --- |
| Construction | `pirates/ship_building.py:13,122,204,211,250,396` — candidate geometry, access, queued orders, blueprint submission and completion. |
| Spatial stock | `pirates/ship_storage.py:56,67,83,136,326,345,356,379,391,412,427` — units/mass, piles, collection/return, capacity/allocation validation and transfers. |
| Spaces/access | `pirates/ship_spaces.py:24,114,137` — room designations, hold routes and inspection. A label alone is not a functional fitting. |
| Logistics | `pirates/ship_logistics.py:10` — inspect existing route/storage constraints before introducing another blocker calculator. |
| Load/sailing | `pirates/ship_physics.py:17,130,210` — positional load, capacity/stability approximation and sailing forces. Do not claim naval-engineering accuracy. |
| Provisioning | `pirates/provisioning.py:12,132` — forecast and canonical `readiness_rows`; extend this summary rather than adding a rival one. |
| World/persistence | `pirates/game.py:139,236,250,260,468,499` — authored creation, transitions, canonical hooks and save/load. |
| Launch | `play.py:1492,1536` — normal scenario dispatch and CLI. |
| Markets/travel | `pirates/economy.py:228,729,774`; `pirates/voyage.py:212,273`; `pirates/management.py:863` — port rebuilding, persistence and actual departure path. |

Trace the current `ship_model`, blueprint, fixtures, navigation, ship jobs, labor/gear UI and employment owners during the baseline audit. Record exact entry points instead of guessing new APIs. Existing tests include `test_ship_building`, `test_ship_blueprint`, `test_ship_storage`, `test_ship_navigation`, `test_ship_spaces`, `test_ship_fixtures`, `test_ship_relocation`, `test_ship_physics`, `test_ship_logistics`, `test_ship_hazards`, `test_provisioning`, economy and employment suites.

## 4. Required authority and conservation rules

1. **One item, one location.** Distinguish trade cargo, stockpile quantities, reservations, carried bundles, equipped gear and ground items. Reservations are claims on stock, not extra stock.
2. **One construction authority.** Queued work is not completed geometry. Completion consumes only the materials actually allocated/collected through current APIs.
3. **No free conversions.** Moving merchandise into supplies or materials must disclose and conserve quantity, mass and bulk under the existing item/yield definitions. Do not silently count the same cargo in hold stock and economy cargo.
4. **Cancellation is accountable.** Cancel/replan/interruption/death/release/load must return or retain collected materials at a real valid location; overflow cannot disappear or teleport into a full destination.
5. **One clock.** Work, needs, wages, food, water and hauling advance through canonical world time. Browsing reports neither advances work nor changes reservations.
6. **Geometry grants function.** Supported fixtures, reachable space, stores and staffing confer effects—not a room name. Preserve multi-deck coordinates and stairs.
7. **Capacity follows actual geometry/rules.** A larger hull must demonstrate the supported increase in usable capacity/access, not receive an arbitrary bonus from a label. Mass/bulk do not vanish when stowed.

Before implementation, write a compact ownership/transition diagram and enumerate the existing allocation/transfer rollback paths. Fix the narrow authority defect rather than layering another inventory ledger on top.

## 5. Functional ship arrangement

Audit each requested space against current gameplay and classify it as supported, partly supported or deferred.

### P0 source audit (2026-11-19)

| Area | Current evidence-based status |
| --- | --- |
| Sleeping / berths | **Supported.** Bunks are stable fixtures, crew retain assigned bunk indices, sleep targets the assigned bunk, and recruitment installs a bunk. A `berths` label is descriptive only. |
| Galley | **Supported.** The installed galley fixture, reachable cook labor, finite recipe inputs, water reserves and queued cook jobs control cooking; readiness forecasts whether cooking is actually available. A `galley` label alone does nothing. |
| Cargo / hold | **Supported, with controls gap.** Hold/water fixtures and floor piles own physical contents/capacity; jobs reserve then crew carry bundles; transfer cancellation returns stock; trades must be allocated at the hold. There are no pile filters/priorities; source choice is nearest reachable, then pile ID. Floor designation UI uses the default 1,000 kg although the authority accepts explicit capacity. |
| Workshop | **Partial.** The installed workshop supports the existing arms-practice route. Structural building currently queues as `repair` labor and uses the repair station; no independent workshop designation/function is required or granted. |
| Medical | **Partial, useful current function exists.** The installed medicine chest plus an `infirmary` designation and medicine supply enable current shipboard health/injury recovery; bandage actions are separate. No combat-specific outcome bridge is in M2 scope. |
| Brig | **Partial.** The installed cage, a `brig` designation, boundary doors and locked doors gate the current receive-surrendered-boarders helper. This does not implement custody law, rewards or a broader prisoner-care loop. |

Source anchors: `pirates/ship.py:1934-1948,1983-2024`; `pirates/ship_fixtures.py:29-47`; `pirates/ship_storage.py:136-260,326-375`; `pirates/management.py:1504-1641,1837-1907`; `pirates/economy.py:512-571`; `pirates/trading_ui.py:146-207`; `pirates/provisioning.py:132-234`.

### Baseline verification (pre-gameplay-change)

Working directory: `E:/Pirates of Miska`.

| Command scope | Result |
| --- | --- |
| Construction, blueprint, storage, navigation, spaces, fixtures, relocation, physics, logistics, hazards | **120 passed in 21.59s** |
| Authored scenario, economy/trade, voyage/save, provisioning, employment payroll/lifecycle/agreements/undertakings/review | **198 passed in 91.66s** |
| Construction menu, ship life and support, ship model | **77 passed in 24.50s** |

Total scoped baseline: **395 passed**. This is not a full-suite result. Existing worktree changes in combat, simulation, authored scenario, economy/voyage and reports were preserved.

Readiness audit finding (resolved in P4): `readiness_rows` was read-only but omitted hull/rig/load/support/access, enabled labor and tools, unfinished work, medical supplies and ammunition. These now use the existing ship/economy/job authorities in the same projection and are included in voyage review. They remain advisories; existing route/scenario rules are unchanged, and no new departure gate was added.

### P1 construction/function result (2026-11-19)

The direct adjacent-order UI and blueprint editor previously generated different keys for the same construction tile/deck. That allowed two live jobs to reserve materials and race on one site. Both entry points now use the same site key and also reject an active construction job at that coordinate regardless of its saved legacy key; older kind-qualified cancellation cooldown keys are honored. The saved active blueprint-job regression reloads and still rejects a competing direct order. Construction copy now says it uses the existing `Patch hull (repair)` labor; it no longer promises a separate carpentry labor that the scheduler does not provide.

Existing behavior was retained where already supported: access/geometry validation, physical plank hauling, real work time, and blocker recovery after an occupied work tile clears. Sleeping/galley/cargo/medical/brig effects remain tied to actual fixtures, stores, routes and work; workshop stays partial. The room report explicitly says designations grant no passive bonuses. No new unsupported room effects were added.

Post-change verification: construction, blueprint, construction menu, storage, navigation, spaces, fixtures, relocation, physics, support, ship model/life, logistics and hazards: **198 passed in 40.67s**. The test includes a red-before-green direct-vs-blueprint duplicate reproduction, post-save/load duplicate rejection, and actual existing material-backed build/block-recovery coverage. This is focused coverage, not the full suite.

### P2 stock/hauling result (2026-11-19)

| Quantity | Canonical owner and transition |
| --- | --- |
| Purchased merchandise | `Economy.cargo` catalog units; active contracts may reserve fungible quantities. |
| Allocated supplies | At the hold, exact catalog yield is removed from trade cargo and added once to canonical `Ship.stores` / spatial piles; containers and issued gear follow their own registries. Fees and one-minute handling time remain real. |
| Available spatial stock | `SpatialStorage.piles[*].contents`; `Ship.stores` is a mapping projection. Claiming a job subtracts from the source pile into its allocation record, then the worker physically carries it in `sailor.cargo`. Reservations are not extra quantities. |
| Manual transfer | A transfer reserves its source amount and destination capacity, then a worker collects and delivers it. Cancel/interruption returns to the original source; unavailable return capacity keeps the worker bundle intact and reports a wait rather than overfilling or losing goods. |
| Equipment / satchel / ground | Remain in separate gear, inventory and ground-item owners; they are not silently merged into store totals. |

Floor stockpiles now persist allow-resource profiles and a P1–P5 source priority. Incoming physical transfers honor the chosen destination filter; automatic supply collection orders source piles by priority, then route length, then stable pile ID. Existing stock remains usable after a filter changes, returns bypass new-put-away filters, and the starter hold/water fixtures remain universal-resource stores. Capacity can be selected at designation and adjusted only when current plus reserved incoming mass fits. Old stockpile records without these settings migrate to accept-all/P3.

Crew loads remain limited to 50 kg. Spatial supply piles enforce measured mass only; they have no canonical volume metadata. Catalog trade cargo separately enforces catalog mass and bulk, so M2 does not invent bulk values for converted supply units.

The new capacity authority counts outstanding transfer deliveries and unreturned job allocations before admitting new incoming trade allocation, water decant or physical transfers. Cancellation return rechecks capacity, including for an older save that already contains an overbooked return; it waits without dropping the carried bundle. The allocation path remains finite and conservation-preserving.

Post-change verification: ship storage, economy, provisioning, ship life/physics, authored-scenario and voyage-save suites: **168 passed in 57.57s**. This includes filter/priority UI/API behavior, old schema defaults, filtered transfer save/resume, actual preferred-source collection, cancellation and capacity-overflow regressions. Changed-file compilation and `git diff --check` passed (Git emitted only existing CRLF-normalization warnings). This is focused coverage, not the full suite.

### P3 crew-preparation result (2026-11-19)

Kept labor, gear, officer and payroll state in the existing ship/economy/voyage owners. The Work Compliance report now includes read-only enabled-labor coverage, matching issued tools and active officer posts; it explicitly says officer posts do not reserve workers. The same coverage helper is used in readiness. Regression evidence covers an appointed navigator still performing an assigned repair, a carpenter tool changing real construction progress, a cook physically collecting finite ingredients and producing a meal, and recruitment/berths/wages/arrears/undertakings through their existing tests. The authored walkthrough also buys finite rope, issues it to a sailor and shows its rigging work bonus.

### P4 readiness/departure result (2026-11-19)

`provisioning.readiness_rows` is the single read-only projection for supplies plus ship/crew preparation. It now reports the existing hull caution and rigging condition; low-order load/freeboard/GM and support caveats; living-crew access to essential stations; labor/tool/officer coverage; blocked and unfinished jobs with remedies; shipboard-only medicine/bandages; loaded guns and reserve shot; and an explicit no-new-gates policy. `VoyageSystem.role_report` embeds those findings. A paid 5 L potable-water purchase/allocation fixes a measured shortage; the authored departure test then sails with hull 40/100 and the existing warning. Regression coverage verifies review purity, warning-only departure, unstaffed access warnings, and unchanged voyage rules. Existing ship-life chunking, one-hour save/resume equivalence and employment day-boundary/idempotence tests cover canonical-time consistency; this is not a broad economy/performance claim.

Post-change focused verification: **457 passed in 162.74s** across the 27 ship construction/storage/navigation/space/physics/life/hazard/authored/economy/voyage/provisioning/crew/employment suites listed in §11. This is not a full-repository test run.

| Area | Minimum M2 proof |
| --- | --- |
| Sleeping | Reachable supported berth/fitting affects the existing sleep arrangement; show staffing/berth limitations. |
| Galley | Actual installed/reachable cooking fixture, ingredients, fuel/water and assigned work produce food through existing recipes. Cook-only ingredients are not ready meals. |
| Cargo | Valid accessible storage accepts finite compatible goods and exposes mass/bulk/free capacity. |
| Workshop | Supported tools/fittings/material access enable an existing repair/build job; do not invent a room bonus. |
| Medical | Show available supported supplies/access and explicit current limitations. Do not imply campaign wound treatment exists if its combat bridge is absent. |
| Brig | Verify supported enclosure/locking/access prerequisites without adding M6 custody/bounty mechanics. |

An unsupported function must be labelled and deferred explicitly, not represented by decorative success text. Do not block all M2 work on missing combat-specific medicine or legal custody.

Construction UI must explain a rejected placement or stalled order: missing material, reserved elsewhere, unreachable source/work position, obstructing fixture, broken stair/access, insufficient worker/tool, or unsupported plan. Show queued/in-progress/blocked/completed as distinct states.

## 6. Stock organization and hauling

- Reuse stockpile/transfer rules; first document supported resource filters, priority ordering and capacity semantics.
- Add only the missing MVP controls. If a filter or priority is displayed, actual job selection must honor it deterministically.
- Distinguish unallocated trade cargo from supplies available to galley/builders; disclose purchase porter fees/time and destination before commitment.
- Provide a useful source → destination → quantity → capacity/reservation review; cancellation before commitment is free.
- Revalidate source, destination, worker route and capacity at execution—not only when an order is created.
- Blocked jobs retain an inspectable cause and can recover after access/stock/staffing is repaired. Do not silently duplicate/recreate jobs each frame.
- Moving fixtures or rebuilding a port must not erase ship stock, carried bundles, allocations or unfinished jobs.

## 7. Crew, equipment and staffing

Connect the existing roster, gear, labor and officer interfaces into preparation; do not add a second assignment system.

- Show who can perform critical work, their assigned priorities, and relevant tools/equipment.
- Missing gear, absent/incapacitated personnel and unreachable workstations produce actionable explanations.
- Crew recruitment consumes the existing signing payment, berth allowance, wages and provisions; preserve agreed terms and arrears.
- Assigning an officer must not silently remove a needed worker without making the resulting coverage understandable.
- Verify a real worker completes hauling/construction/cooking rather than merely changing a UI assignment label.
- Keep supported practical work/learning intact; new competence effects and broad balance belong to later phases.

## 8. Readiness and departure without bureaucracy

Extend `readiness_rows` and existing actual departure review with supported hull/rig, load/access, ready/cookable food, water/fuel, essential jobs, medical supplies, crew coverage and ammunition information.

Each finding needs: condition, evidence, consequence and remedy. Separate:

- **Hard blocker:** the requested action is physically/legally unsupported under an existing authoritative rule. Explain why and where to fix it.
- **Warning:** sailing is possible but risky (unfinished optional work, low reserves, weak coverage). Permit informed departure after a single clear acknowledgement where appropriate.
- **Information:** routine status, no blocking click.

Do not invent new hard departure gates without a source-backed rule. Do not require every room type, perfect hull condition, a delivery contract, a daily checklist, or an arrival settlement. Jobs/materials stay real when departure occurs; unfinished work is not magically completed or refunded.

## 9. Persistence and unhappy paths

Round-trip an authored save with queued/blocked work, reserved stock, a carrying worker, moved fixture, room/storage designations, gear/labor/officers and the readiness state. Continue both original and loaded worlds for the same controlled horizon and compare meaningful results.

Reject malformed references/quantities/capacities safely before swapping the current world. Any schema addition requires a narrow documented migration/default and legacy fixtures. Preserve scenario identity and selected save slot. Failed load must never overwrite its source.

Required negative cases: insufficient stock/funds, full storage, incompatible filters, source/destination removed, lost access, worker interrupted/unavailable, cancellation after pickup, repeated submission/retry, invalid placement, broken multi-deck route and save/reload mid-transfer. Use supported APIs/events to construct fixtures; do not bypass validators to fabricate a passing state.

## 10. Implementation sequence

P0–P5 are complete for this scoped M2 objective. First inventory existing behavior; avoid rebuilding working systems.

| Phase | Work | Exit gate |
| --- | --- | --- |
| P0 — baseline and authority audit | **Complete (2026-11-19).** Preserved concurrent work; traced UI and stock/job/save owners; ran baseline tests; classified room support. | **Verified:** source-backed authority/room matrix and 395-test pre-change scoped baseline recorded above. |
| P1 — construction and function | **Complete (2026-11-19).** Unified direct/blueprint site identity and corrected construction labor disclosure; retained already-supported function bindings. | **Verified:** focused 198-test construction/function/ship regression command passed; real construction consumes and hauls planks, invalid geometry/access is rejected, occupied work recovers, and duplicate order/save-load regression passes. |
| P2 — storage and movement | **Complete (2026-11-19).** Added persistent resource filters/source priority, player capacity control and centralized incoming reservation checks while retaining physical hauling. | **Verified:** focused 168-test storage/economy/provision/life/physics/authored/save command passed. Old setting migration, actual prioritized pickup, filter-respecting saved transfer, cancel return, and overbooked-return wait passed. Supply piles are mass-only; trade cargo separately retains catalog bulk rules. |
| P3 — staffing and equipment | **Complete (2026-11-19).** Connected existing labor/gear/officer and recruitment authorities to real jobs and coverage. | **Verified:** actual equipped construction, finite cooking/hauling, officer/non-reservation behavior and employment regressions; see P3 evidence above. |
| P4 — readiness and departure | **Complete (2026-11-19).** Extended the canonical summary/review; separated advisories from existing route/scenario rules. | **Verified:** paid potable-water fix followed by departure at hull 40/100 with warning; no new gate, checklist, or instant work. See P4 evidence above. |
| P5 — integrated verification | **Complete (2026-11-19).** Authored launch→purchase→build/haul→fix blockage→staff/equip→inspect→sail→reload and scoped evidence gates passed. | **Verified:** 457 focused tests; actual connected-deck capacity change and conserved transfer; saved mid-carry construction/haul plus underway voyage; 33 native captures at all three sizes; compile, whitespace and plan/skill sync complete. |

Do not mark a phase complete because a helper returns rows. Verify actual work and the live input path.

## 11. Acceptance and evidence matrix

| Gate | Required evidence |
| --- | --- |
| Useful arrangement | Changed geometry/fixtures/access cause a supported practical difference; materials and elapsed work recorded. |
| Blocked work | A real blocked job shows its cause; fixing the cause lets the same valid order proceed without duplicated stock/jobs. |
| Physical organization | Stock actually moves with workers and obeys filter/priority/capacity; merchandise/supplies/reservations remain distinguishable. |
| Larger-hull capacity | Controlled small/expanded valid geometry comparison demonstrates actual supported capacity/access changes and conserved load. |
| Staffing | Actual construction/hauling/cooking execution responds to gear/labor/coverage; recruitment/wages/supplies remain finite. |
| Readiness | One authoritative summary gives useful remedies; warning-only departure remains possible, real impossibilities explain themselves. |
| Time/purity | Browsing never advances/mutates work; controlled stepped/bulk elapsed time yields equivalent state with interruptions accounted for. |
| Save safety | Mid-job/mid-carry resume preserves identities/quantities and deterministic continuation; old saves and invalid-source protection remain. |
| Authored boundary | Preparation and departure do not call generated/prehistory/strategic simulation or change scenario/slot identity. |
| Real UI | Native captures of construction, blockage, storage/transfer, crew/gear, readiness, departure warning and resumed work at all three target sizes. |

Native sizes: 1280×800, 960×700 and 640×600. Inspect scrolling, clipping, modal backgrounds and actionable text. Screenshots are scripted presentation evidence, not human comprehension or gameplay acceptance.

### Verification workflow

Working directory: `E:/Pirates of Miska`.

```powershell
python -B -m pytest -q tests/test_ship_building.py tests/test_ship_blueprint.py tests/test_construction_menu.py tests/test_ship_storage.py tests/test_ship_navigation.py tests/test_ship_spaces.py tests/test_ship_fixtures.py tests/test_ship_relocation.py tests/test_ship_physics.py tests/test_ship_support.py tests/test_ship_life.py tests/test_ship_logistics.py tests/test_ship_hazards.py tests/test_authored_scenario.py tests/test_economy.py tests/test_trade_expansion.py tests/test_voyage.py tests/test_voyage_save.py tests/test_provisioning.py tests/test_crew_recruitment.py tests/test_crew_lifecycle.py tests/test_crew_detail.py tests/test_employment_agreements.py tests/test_employment_payroll.py tests/test_employment_undertaking.py tests/test_employment_review.py tests/test_employment_lifecycle.py
python -B tools/preview_ship_preparation.py --output C:/Users/Warp/AppData/Local/Temp/opencode/ship-preparation-m2
python -B -m py_compile pirates/management.py pirates/provisioning.py pirates/ship_storage.py pirates/voyage.py tests/test_crew_detail.py tests/test_provisioning.py tests/test_ship_building.py tests/test_ship_life.py tests/test_ship_storage.py tools/preview_ship_preparation.py
git diff --check
```

The focused command above passed **457 tests in 162.74s** (not a full-suite result). The authored walkthrough passed with **33 SDL captures** at all three native sizes; its `verification.json` records authored scenario identity, underway save/reload, stockpile invariants and the scripted-not-human-playtest boundary. Captures were inspected for compact scrolling, actionable text, tool/labor visibility, readiness warnings, and modal contrast. Changed Python files compiled; `git diff --check` exited successfully (Git printed only line-ending normalization advisories); a direct trailing-whitespace/final-newline scan passed for the new plan, preview tool and global skill. Parent plans and the global skill now record scoped M2 evidence and remaining limits. Do not launch expensive broad headless-simulation runs to certify player preparation.

Measure a controlled prepared passage before tuning shortages or interruptions; record simulated horizon, crew, stores, jobs, wages, encounter controls and actual wall time. No universal quiet-passage, economic-balance or performance claims from one fixture.

## 12. Completion ledger

| Phase | State | Evidence |
| --- | --- | --- |
| P0 | Complete | 395 focused baseline tests; source-backed authority diagram and room-support classification. |
| P1 | Complete | 198 focused tests passed in 40.67s; direct/blueprint duplicate-site regression is red before fix and green after save/load. Existing build tests verify physical plank consumption, actual time, blocked occupancy retention/recovery, access and geometry. |
| P2 | Complete | 168 focused tests passed in 57.57s; filter/priority/capacity UI and APIs, legacy stockpile settings migration, actual worker source choice, filtered transfer save/resume, transfer cancellation, return-capacity reservation and fail-safe wait verified. Supply piles have no canonical bulk metadata; trade cargo mass/bulk rules remain separate. |
| P3 | Complete | Real carpenter/cook/hauling work; enabled labor/tool/officer coverage; finite recruitment/payroll/employment regressions in the 457-test scoped run. |
| P4 | Complete | Canonical readiness and voyage review; paid water-shortage fix; deliberate departure with hull warning; warning-only/no-new-gates regressions. |
| P5 | Complete | 457 focused tests passed; authored walkthrough passed with 33 native PNG captures at 1280×800/960×700/640×600; captures inspected; construction/transfer/voyage reload and conservation verified; compile/whitespace checks passed; MVP/master plans and global skill synchronized. Scripted evidence is not unaided playtesting. |

M2 completion is not full-MVP completion. M3 economic validation, M4 traffic/interaction, M5 authoritative campaign combat, M6 lawful rewards/prizes, M7 salvage/competence and M8 release acceptance remain separate gates.
