# M4 — Sea Traffic and Interaction: Implementation Plan

Date: 2026-11-20
Status: P0–P6 implementation and scripted integration/native evidence are complete: bounded traffic/repeat contacts, signals/parley and local risk/neutral-marine safeguards, consented physical transfer, and finite at-sea trade. An unaided authored player acceptance run remains unverified. M5 outcomes and the combat-inclusive MVP are not complete.
Parent: `LAWFUL-TRADER-MVP-PLAN.md` §§9–11, 15–16. Preserve scoped-complete M1–M3; M5 campaign combat remains a separate required gate.

## 1. Objective

Deliver the full M4 slice: bounded relative ship motion and observable intent; distinct, repeatable contact instances; signals/parley; local pirate risk/fear and neutral marine response; consent-based peaceful deck transfer; and finite at-sea trade. Use the existing voyage, encounter, economy, ship, society and save authorities where they fit. Add no hidden-state omniscience, forced encounters/fights, free/duplicate transfers, or combat outcomes.

The M4 scope comes from the parent plan's phase table and §§9–10. The earlier `SEA-ENCOUNTERS-AND-COOPERATION-PLAN.md` delivered the release-one encounter-action/handoff foundation; its documented limits are inputs to this plan, not claims that M4 is already complete.

## 2. Verified starting point and authority map

- `pirates/voyage.py:194-206` maps the player's chart position along the current route leg and cross-track offset. `:409+` advances voyages; `:541+` owns the rudder order. The chart has connected nodes/lanes, not a shared relative-ship geometry or validated ocean physics.
- `pirates/game.py:260-272` advances the NPC fleet and forwards a sighting into the voyage interrupt path. `pirates/voyage.py:1083-1237` owns the persistent fleet. NPCs move between chart nodes on an hourly pulse; sighting uses chart distance ≤1.25. `sighted` is a persistent set, and `advance_second` only emits the first sighting for a hull.
- `pirates/sea_encounter.py:35-57,84-146,149-263,272-442` owns encounter-local `distant`/`close` state, read-only lookout rows, strict contact validation, signals, deterministic approach/evasion and close parley. This is not relative motion: approach/evasion are coarse deterministic checks, not ship-to-ship course physics.
- `pirates/voyage.py:707-820` owns encounter actions and stable cannon/boarding handoff requests. A declined or unclear crossing becomes `combat_pending`; combat itself, ammunition consumption and outcome resolution remain outside M4.
- `pirates/management.py:1142-1289` owns the encounter menu. A welcomed personnel transfer is displayed as pending; no NPC deck or physical transfer consumer currently moves anyone.
- `NpcFleetSystem` has finite per-vessel `cargo` and `silver`; `:1239-1342` performs NPC port activities, not a player-facing at-sea transaction. M3 `Economy.trade` is the port transaction authority; no at-sea ship merchant transaction is present in the traced path.
- Fleet persistence is `pirates/voyage.py:1513-1700` (`sighted`, sighting reports, instance-scoped player contacts, hysteretic contact tracks, lane motion, roster and ledgers); `game.py:731` passes scenario identity and current clock into restore. Legacy saves without motion/contact-track fields migrate without invented travel/meeting history; encounter identity is validated by `sea_encounter.py` during voyage restore.
- `pirates/society.py` owns organizations/public identity, but there is no captain-to-faction stance. Do not synthesize a relationship from faction labels. User resolved lawful hostility at `LAWFUL-TRADER-MVP-PLAN.md:201-207`; `pirates/sea_conduct.py` now persists bounded evidence, and only the preserved legacy cannon exchange currently emits a witnessed NPC return-fire event. There is no verified-warrant or committed-NPC-boarding producer, so those paths remain fail-closed.
- Existing regressions: `tests/test_sea_encounters.py`, `tests/test_voyage.py`, `tests/test_voyage_save.py`, plus `tests/test_authored_scenario.py` and M3 economy tests. Existing sea-encounter native harness: `tools/preview_sea_encounters.py`.

## 3. Scope boundaries and unresolved contracts

### In scope

1. One bounded relative-motion model shared by lookout, approach/escape, contact lifecycle and any future M5 entry snapshot. Show crossing, head-on approach, overtaking, moving away/following with source/confidence; do not claim high-fidelity naval physics.
2. Stable vessel identity plus distinct encounter-instance identity. One continuously visible hull is one contact; after visibility separation/cooldown, the same hull may be encountered again. Old permissions/hostility do not silently transfer to the new instance.
3. Bounded signals and parley, non-omniscient local pirate risk/fear and mostly neutral/helpful marines. Distinguish observed threat, explicit hostile commitment and verified warrant; no attack permission from appearance/faction alone.
4. Peaceful personnel transfer only with explicit permission and a physical receiving/departure path. Conserve named people and never teleport; refusal/uncertainty follows the existing M5 request boundary.
5. Finite at-sea trading against disclosed vessel-owned stock and purse, with preview, range/permission checks, cargo capacity, canonical time, cancellation and exactly-once commit.

### Out of scope / hard boundaries

- Do not implement or fabricate campaign combat, cannon resolution, boarding fights, injuries, captures, surrender, loot, bounties, prize title, or M5/M6 rewards. Preserve the stable pending-request boundary for hostile commitment.
- Do not build strategic simulation/faction autonomy, omniscient pursuit, unlimited fleet management, new port economies, or a guaranteed profitable trade route.
- Do not treat a neutral marine as hostile based only on a flag, or pirates as lawful targets based only on appearance. Do not overwrite old wanted/conduct state during migration.
- Do not silently alter M1–M3 authored/legacy/generated launch, save-slot, market, cargo, voyage or report behavior.

### Decisions required before dependent implementation

- **P1 chart-motion contract (chosen from current source):** use existing `SEA_NODES[*].xy` chart units and `LANES`; keep player coordinates from `VoyageSystem.map_coordinate()`. Each NPC follows one legal shortest-route lane hop per existing hourly fleet pulse, but interpolate that hop linearly over exactly `360_000` clock ticks (one hour), instead of teleporting at the pulse. Persist `{from,to,start_tick,paused_at_tick}` per active NPC edge; `node` remains the last reached node and `destination` remains the strategic goal. The deterministic hourly destination/port-activity cadence is unchanged. A reserved current encounter ship pauses on its sampled location and its edge clock shifts by the pause duration on release; unreserved ships continue. Evaluate the existing 1.25-chart-unit lookout radius every ship second while underway, but only after the existing 1,800-second minimum voyage age; never disclose NPC destination/private plan. Relative bearing/range and qualified closing/opening/crossing observations derive from the same player/NPC chart coordinates and successive public samples; first/paused samples say unknown. This is bounded chart motion, not combat geometry or hydrodynamics. Process skipped hour boundaries in order so final state is independent of `World.advance` chunking.
- **Lawful-hostility policy (settled with user):** block unsupported aggression. Hostile commitment requires a witnessed actual attack/explicit committed boarding or fire by the other vessel, or a separate verified warrant. Pirate appearance, faction/threat estimate, parley refusal or proximity is not legal evidence. `SeaConductLedger` is versioned/bounded and handoffs cite a matching evidence ID. The preserved legacy artillery path records an opponent's actual return shot; no verified-warrant or NPC-boarding producer currently exists, so unsupported requests fail closed. Preserve M5 as handoff/result authority when evidence exists.
- **Peaceful transfer contract (implemented):** one temporary connected NPC deck occupies the bounded open-sea map area west of the Wayward, joins through a walkable gangplank, and accepts at most six named living participants including the captain. One transfer may complete per contact; visitors can walk back or use an explicit return action. Save/load reconstructs the deck only for a validated active visit; voyage continuation is blocked until all visitors return. This is not M5 boarding/combat or a persistent second vessel.
- **Finite trade contract (implemented):** only a close, publicly assessed merchant can return an explicit offer, at a one-minute request cost. The returned whitelist is grain/beans/iron only; ask is 125% and bid 70% of catalog base price, with no remote-market/scarcity query. Offer quantities disclose only those commodities and the finite vessel stock/purse limits. Quotes cap each order at 20 units, persist a contact-local sequence/transaction ID, and commit once after rechecking encounter/range, current stock/purse, player funds, cargo load and unreserved sale cargo. Cancellation is free; a committed retry returns its receipt without another mutation or time charge. M3 port `Economy.trade` remains untouched.
- Define local pirate risk/fear evidence and decay/expiry. Keep it local, witnessed and bounded; do not create M6 progression/unlocks as a side effect.

## 4. Acceptance matrix

| Area | Required evidence |
| --- | --- |
| Relative motion | Deterministic crossing, head-on, overtaking, following, moving-away and escape fixtures; visible bearing/range source and uncertainty; no overlap or impossible node jumps. Player and NPC stepping in chunks produce equivalent state. |
| Contact lifecycle | Same continuously visible hull does not interrupt repeatedly; loss and later reunion create a new contact ID; a prior contact's consent/attack request does not leak; save/reload preserves history and does not reroll a contact into a free reward. |
| Signals/parley | Answer/refusal/no-answer, range/time cost, action eligibility and consent are explicit; no private mission, stock or intent leakage. No relationship/faction mutation without its source authority. |
| Pirate risk / marines | Pirate choices respond to bounded observable wealth/condition/crew/credible local evidence and can shadow, demand, disengage or attack only through the agreed legal gate. Marines are neutral/helpful to lawful traders; inspection/pursuit uses jurisdiction, observations and explicit stop/lost-contact conditions. No forced contact or strategic war loop. |
| Peaceful transfer | Permission is instance-local and explicit; captain/escorts are living named people; transfer has reachable paths and capacity; accepted commit moves each identity exactly once; rejected/cancelled/reloaded retries do not duplicate or lose people. |
| At-sea trade | Preview is read-only; stock/purse, price, range, consent, money, cargo/capacity and time revalidate at commit; cancel/failure is atomic; retry/reload cannot replay; no hidden inventory is offered. |
| Compatibility | Old saves migrate without invented history or permanent consent; authored slot remains isolated; legacy/generated modes remain compatible; M1–M3 tests remain green; M5 combat result is still unconsumed. |
| Presentation | Live UI uses the same pure rows/actions as tests/capture; inspect 1280×800, 960×700 and 640×600. Scripted screenshots are not player acceptance. |

## 5. Phases and gates

### P0 — Authority and contract audit (complete)

Preserve concurrent work; trace current M4 code/tests and M1–M3 boundaries; run baseline tests; record motion, contact, consent, neutral response, transfer, trade and save gaps. Resolve each design contract before its dependent phase; do not let an unresolved policy block independent work. Produce a source-backed evidence ledger.

### P1 — Bounded relative traffic

Implement a shared chart-level relative-motion/intent model with bounded updates, deterministic time chunking and visible uncertainty. Cover crossing/overtaking/following/escape, contact separation and safe return to route. Do not implement detailed tactical sailing physics.

### P2 — Repeat contact lifecycle

Replace permanent first-sighting suppression in authored MVP mode with bounded visibility/cooldown and encounter instances. Preserve stable hull identity/history; create fresh consent and hostile-request state per encounter; avoid interrupt spam while one hull remains visible. Migrate/validate saved state.

### P3 — Noncombat intent, risk and marine response

Integrate signals/parley with bounded NPC intentions. Add only source-backed, local pirate risk/fear and neutral marine behavior after the lawful hostile-commitment contract is agreed. Verify privacy, jurisdiction, stop/lost-contact behavior and no forced combat.

### P4 — Peaceful physical transfers

Build the minimum NPC deck/transfer authority required for an explicitly welcomed crossing. Use named identities, capacity and reachable paths; support transfer and return/withdrawal without combat. Keep refused/unclear cases at the durable M5 request boundary.

### P5 — Finite at-sea trade

Use vessel-owned stock/purse and the M3 transaction principles. Preview, validate range/permission/capacity, then commit money/items/time exactly once. Cover empty stock/purse, interruption, save/retry and cancellation; do not expose unoffered private cargo.

### P6 — Integration and evidence

Run focused M4 and M1–M3 guard suites; exercise all acceptance rows in authored mode; capture and inspect the three native sizes; compile/whitespace/diff-check; update this plan, parent/MVP/master plans and the global project skill. State remaining M5/M6/M8 and human-acceptance gates explicitly.

## 6. Verification workflow

Working directory: `E:/Pirates of Miska`.

```powershell
python -B -m pytest -q tests/test_sea_encounters.py tests/test_voyage.py tests/test_voyage_save.py tests/test_harbor_navigation.py tests/test_authored_scenario.py tests/test_economy.py
python -B tools/preview_sea_encounters.py --output C:/Users/Warp/AppData/Local/Temp/opencode/m4-sea-traffic
git diff --check
```

Expand tests/capture coverage as M4 surfaces are implemented. Do not report a full-repository pass unless it is actually run to completion; separate unrelated concurrent failures from M4 regressions.

## 7. Evidence ledger

| Gate | Status | Evidence |
| --- | --- | --- |
| P0 authority audit | Complete | Source-backed map above; focused baseline `python -B -m pytest -q tests/test_sea_encounters.py tests/test_voyage.py tests/test_voyage_save.py tests/test_harbor_navigation.py tests/test_authored_scenario.py tests/test_economy.py` passed **170 tests in 116.74s**. Runtime probe moved `syndicate_1` from the player node to Deepwater and back: first contact returned the vessel, lost sighting returned none, and reunion also returned none because permanent `sighted` membership remained. Existing NPC record has node/destination but no progress/bearing; welcomed crossing test proves a saved pending permission request only, not a physical transfer. Motion, repeat-contact, physical-transfer, at-sea-trade and lawful-marine gaps are confirmed. Contracts in §3 gate their dependent phases. |
| P1 relative traffic | Complete | `pirates/sea_traffic.py` provides bounded interpolation and sampled relative-motion categories; `voyage.py::NpcFleetSystem` advances one lane hop/hour over 360,000 ticks, pauses/resumes reserved contacts, samples the 1.25-unit lookout against the player's interpolated chart position, and returns sanitized chart/bearing/motion observations; `game.py::on_ship_second` preserves the >1,800-second gate. Fleet save validation/migration covers lane motion. Tests cover head-on closing, same-course overtaking, crossing, following, escape/opening, interpolation, save-resume, reservation, chunk equivalence, private-field absence, age gate and a live encounter→pause→continue test proving the NPC lane clock and player route recover. Final encounter/voyage/save/authored/generated regression passed **169 tests in 131.39s**. Preview passed 7 screens × 3 sizes; 640×600 and 1280×800 lookout rows reviewed. The broader authored acceptance walkthrough remains in P6; preview/tests are scripted evidence, not human acceptance. |
| P2 repeat contact | Complete | Replaced hull-permanent first-sighting suppression with persisted `ship_id:sequence` instances, 1.25-unit acquisition, 1.5-unit separation for 60 seconds before loss, no duplicate interruptions during continuous visibility, fresh encounter-local parley/signals/crossing state, and instance-scoped hail/aid history. Legacy fleet saves without tracks/motion migrate with no fabricated history. Authored lawful-trader contact churn, reunion after hysteresis, fresh permission/hail, in-contact save/load, legacy migration and malicious-state validation tests pass in the **169-test** focused run. M1–M5 existing boundaries remain unchanged. |
| P3 risk / marines | Complete for M4's bounded encounter scope | `pirate_risk_assessment` uses visible player crew/cannons and observed opponent crew/condition; deterministic warn-off/stand-down results stay contact-local and do not authorize violence. Neutral-marine reports explicitly avoid inferring hostility/warrants. Hostile attempts without witnessed scoped evidence fail closed; the legacy return-fire evidence and M5-only new handoff are covered. `python -B -m pytest -q tests/test_sea_encounters.py tests/test_voyage.py` previously passed 83; the latest larger integration run below also covers the policy. This does not implement M6 standings/unlocks, marine pursuit/jurisdiction or a new attack producer. |
| P4 peaceful transfer | Complete | `pirates/sea_transfer.py` provides a bounded open-sea deck/gangplank; `VoyageSystem` validates permission, named living manifest, six-person capacity and reachable paths before moving anyone. The crossing is once/contact, walk-back and explicit return are supported, active visits save/load and restore the map overlay, and unsupported paths reject atomically. Tests cover blocked path, no partial mutation, manual gangplank return, UI completion/return, crossing save/load and oversize party rejection. |
| P5 at-sea trade | Complete | `pirates/sea_trade.py` and `VoyageSystem` implement explicit finite offers, fixed ask/bid, bounded read-only quote, transactional commit, saved idempotency receipts and cancellation; live UI exposes only the returned grain/beans/iron terms. Tests cover privacy-before-offer, both transaction directions, conservation, capacity/reserved cargo, empty/stale purse/stock, range revalidation, cancellation, save/resume and retry-without-duplicate. M3 `Economy.trade` remains the port authority. |
| P6 integration evidence | Complete (scripted scope) | Final expanded M4/NPC/authored/save/economy/harbor/M1–M2 guard regression passed **367 tests in 143.52s** (command in the global project skill). Native authored harness `tools/preview_m4_interactions.py` passed **8 screens × 3 sizes** and was visually inspected at 1280×800, 960×700 and 640×600. The merchant is acquired through the lookout authority; the harness requests/commits finite trade, crosses with explicit consent, saves/reloads on the NPC deck, returns and resumes the authored voyage. The separate `tests/test_sea_traffic.py` authored fixture covers actual contact loss/reunion and fresh identity. Evidence is scripted, not unaided player acceptance; keep that release-acceptance gate open. |

The M4 implementation/evidence scope is complete. Treat the unaided authored player run in the parent §16 acceptance matrix as an explicit release-acceptance follow-up, not as demonstrated by these scripts. M4 implementation completion does not complete M5, M6, M7, M8 or the advertised combat-inclusive MVP.
