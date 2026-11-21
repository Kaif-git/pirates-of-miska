# M3 — Free-Trade Information Loop: Implementation Plan

Date: 2026-11-20
Status: P0–P4 scoped-verified; M3 free-trade information loop complete within the evidence limits below (2026-11-20). Human acceptance and M4–M8 remain separate unfinished work.
Parent: `LAWFUL-TRADER-MVP-PLAN.md` §§14–16, especially M3. Preserve the scoped-verified authored foundation and M2 preparation loop.

## 1. Objective and playable outcome

Make independent trading understandable and economically viable in the authored `lawful-trader-mvp` scenario: gather useful but imperfect information → choose goods and destination → buy finite cargo → prepare and sail → sell to a real buyer → freely choose the next activity.

The player must not need a delivery contract, prescribed route, mandatory arrival report, voyage grade or settlement button. Several measured circuits should sustain the captain's actual operating costs; bad choices may lose money. This is not a promise that every route, cargo or crew arrangement is profitable.

Creating this plan does not authorize gameplay implementation, create a goal, or complete M3. Campaign combat remains M5; full-MVP acceptance remains M8.

## 2. Scope firewall

In scope:
- One coherent chart/information entry path using existing UI and voyage authorities.
- Current local quotes, dated remembered observations and explicitly sourced reports.
- Public port/product guides, finite markets and honest transaction previews.
- Measured free-trade circuits, optional financial history and ordinary arrival.
- Persistence, unhappy paths, information privacy and focused native UI verification.

Out of scope:
- Attaching strategic society, prehistory, `sim/`, autonomous production chains or evolving world economy.
- Sea-traffic motion/consent/at-sea trade (M4), campaign fights/results (M5), rewards/prizes/reputation unlocks (M6), treasure/salvage (M7).
- New daily chores, mandatory contracts, automatic cargo sales or compulsory profit settlements.
- Invented appraisal bonuses, remote live-price feeds, guaranteed-profit recommendations or free replenishment outside disclosed existing market rules.

Preserve legacy/generated modes, separate scenario save slots, concurrent combat/simulation work and finite resources. Make scenario-specific changes explicit rather than rebinding shared registries.

## 3. Source-backed starting points

These are inspected entry points, not a claim that the complete M3 loop already passes.

| Authority | Existing behavior | Implementation audit |
| --- | --- | --- |
| `pirates/economy.py:270-297` | Bounded text history; daily stock replenishment/consumption and contract expiry. | Quantify stock sources/sinks, day catch-up, merchant purse exhaustion and waiting behavior. Do not describe markets as closed conservation systems: existing restocking is an explicit source. |
| `pirates/economy.py:382-450` | Prices, quantity quotes, load, merchant proximity and transaction entry. | Verify stock-sensitive totals, spread, delivery costs, capacity and atomic failures through actual UI. |
| `pirates/economy.py:484-519` | Contract-reserved versus available cargo, removal and allocation. | Preserve reservations and cargo/supply separation; sellable quantity must reflect availability, not merely total cargo. |
| `pirates/economy.py:750-836` | Economy persistence and bounded history validation. | Add narrow defaults/migrations only if information records require schema changes. |
| `pirates/trading_ui.py:29-83` | Local merchant lists, bid/ask, quantity confirmation, delivery fee and time disclosure. | Trace normal input path and free cancellation; prevent remote access from becoming remote knowledge. |
| `pirates/trading_ui.py:86-142` | Cargo facts, public product origins/buyers and port guide; guide explicitly avoids remote live quotes. | Distinguish public authored facts from observed changing data; audit capacity copy against actual authority. |
| `pirates/trading_ui.py:277,329` | Port and contract menu entry points. | Keep contracts optional and the authored free-trade path directly discoverable. |
| `pirates/voyage.py:147-174,318-402` | Route selection and read-only arrival report; authored arrival discloses no settlement requirement. | Reuse route/arrival state and verify closing or skipping reports never gates control. |
| `pirates/voyage.py:1088,1229,1439-1452` | Persisted sighting reports with restore validation. | Reuse information conventions where appropriate, but do not equate vessel sightings with market observations or expose hidden live state. |

Relevant existing suites: `tests/test_economy.py`, `test_trade_expansion.py`, `test_harbor_navigation.py`, `test_authored_scenario.py`, `test_voyage.py`, `test_voyage_save.py`, `test_provisioning.py`, and employment suites. Reconfirm current source locations and test collection before editing; concurrent work may shift lines.

### Initial read-only audit — 2026-11-19

- `Economy` already owns finite local market stock and purses. Starting authored MVP state: 600 Marks; six legacy starter sailors with **no employment records**, so their wage cost is zero by explicit legacy arrangement, not representative of later hired labor. Day-1 market purses are 1,500 Marks each.
- Local quote math is already stock-sensitive and region-sensitive: base price × regional factor × scarcity × 120% ask / 65% bid, clamped to the existing 80–150% scarcity band. Quantity totals recalculate per unit and add the 2-Mark delivery order fee on buys. Example only: initial one-unit bluegrain quote at Ada costs 20 Marks including the fee; Dara's initial one-unit bid is 54. This is not a circuit-profit result and ignores other costs and depleted stock/purse.
- `Economy.trade` revalidates merchant proximity, quote, money, stock, mass/bulk capacity, unreserved sellable cargo and merchant purse before mutating; it then records history and advances canonical time (60 seconds buy, 10 seconds sell). The native menu already exposes local ask/bid, stock, funds, quantity confirmation, cancellation and disclosed delivery fee/time. Verify these atomic paths in P0 regression tests before altering them.
- Daily market updates explicitly restock export stock to at most twice authored starting stock and consume two imported units per day. These are modeled source/sink rules; purse behavior and long-horizon repeat-circuit consequences still require measurement.
- The authored port guide provides static public descriptions and merchant locations, and explicitly says current quotes are local/not remote. Merchant access is local-only. No commercial quote-observation store exists in `Economy`; the bounded 40-string history is visible in the port/cargo flow but is not structured quote provenance. Persisted `NpcFleetSystem.sighting_reports` concern vessel sightings and are a separate authority.
- Authored chart routes are free of the legacy 15-Mark `Economy.travel` passage charge. Verdant→Cinder currently plans at about 4.1 hours and Verdant→Freeport about 6.0 hours; both include Navy Lane and currently disclose a low-confidence 30% storm forecast. These forecasts and normal passage costs/interruptions must be represented in circuit evidence rather than replaced with instant travel.
- Initial six-person forecast: 8 kg immediately ready food (about 1.37 crew-days), 45 kg projected food including cookable stock (about 8.82 crew-days), and 60 L clean water (about 4.55 crew-days), using existing 0.85 kg food and 2.2 L water per sailor-day. Circuit sustainability must show ready versus projected/cookable provisions, crew arrangement and elapsed days; never value a reserve as cash profit.
- Arrival already has read-only departure/arrival checkpoints and explicitly says authored trade/sail can continue without settlement. The checkpoint measures money, trade cargo, hull, rigging and contract ledger, not complete provision/employment cashflow; optional structured transaction history is a design choice to resolve, not assumed missing behavior.

Initial regression baseline after M2: authored foundation/economy/trade/harbor/voyage/save/encounter suites: **177 passed in 96.45s**. This is a focused baseline, not a full-repository run.

Controlled actual-time/API circuit baseline (new authored world, day 1, normal `world.move`, `Economy.trade`, `VoyageSystem.confirm`, `enter_sea` and `world.advance`; direct UI selections were scripted, not human-played): starting captain cash 600 Marks, aggregate captain+treasury+seven merchant purses 11,100 Marks, six legacy sailors/no wage agreements, 30 kg food stores, 60 L clean water, 16 planks, 16 shot, hull 72, rigging 62. Bought 8 bluegrain at Ada for 162; sailed Verdant→Cinder (14,672s planned; 16,613s actual, 2 passed sightings); sold to Dara for 396; bought 4 emberpepper at Dara for 116; sailed Cinder→Verdant (13,423s planned; 16,831s actual, 1 passed sighting); sold to Ada for 312. Full loop: 9.369 in-game hours, end captain cash 1,030 (+430), aggregate silver unchanged, cargo empty; food fell 3,250 g, clean water 3.5 L, planks 2, shot 2; hull rose 28 and rigging 34.43 through real automatic repair, which means initial stock was also consumed and repair benefit was not a recurring-trade result. Wages posted 0 because the authored starter crew have no agreements; no midnight market update or storm occurred. This N=1 baseline establishes a working path and accounting surface only—not repeated solvency, balance, safety, human playability or post-starter sustainability. Sightings were passed; no campaign combat/result was resolved.

Deliberately poor same-merchant choice on a fresh authored world: buy one bluegrain from Ada for 20 Marks including delivery; immediately resell it to Ada for 10. Cash loss 10 Marks, cargo returns to zero, Ada's stock returns to 24, and aggregate silver is unchanged. The 70 seconds of disclosed trade time was charged; the captain remains able to trade/sail. This is an example loss case, not a multi-day exploit audit.

Repeated hired-crew/day-boundary baseline: fresh authored world; walked to Ada and hired Pippin Ash through the employment authority (35-Mark signing payment, 1 Mark/day). Repeated the same 8-bluegrain Ada→Dara and 4-emberpepper Dara→Ada route twice using actual sailing and sales. First loop cash 565→995 (**+430**); second loop 995→1,282 (**+287**, including one wage paid on day 2). Total elapsed after world start: 18.727 game hours; the run crossed the day boundary once, paid 1 Mark and advanced market stock/consumption. Across the two loops plus hiring, aggregate silver fell by exactly the 35-Mark signing payment plus 1 Mark wage (-36); market trading itself transferred funds between the modeled purses. Across the measured horizon: food -3.75 kg, clean water -12 L, planks -3 and shot -2; hull 72→100 and rigging 62→91.82 after autonomous repair and one actual “press on” storm choice; three separate vessel sightings were passed, with no combat result. Repeat trades changed quotes/cash yield (cycle two bluegrain buy/sell 197/316 and pepper buy/sell 129/298 versus cycle one 162/396 and 116/312). This is better accounting coverage than the N=1 baseline above, but still only one authored setup/repeated route strategy. It exposes use of starter repair/provision stock and a single storm outcome; **it does not demonstrate several solvent strategies or long-run viability**.

P0 baseline gate is met: scoped regression baseline, authority map, actual untuned repeated circuit with one recruited wage, day-boundary market update, and a genuine losing transaction are recorded. P3 now has three distinct repeated quiet-control strategies and an opening-quote replacement-cost proxy; see §3.2. Broader UI/save/native integration remains in P4.

### P1 knowledge contract decision (2026-11-20)

Use `Economy` as the single commercial-knowledge owner because it owns local markets and is already included in world persistence. Store one latest observation per stable merchant ID (at most seven): source merchant/port, `world.clock.total_ticks`, merchant purse and the visible stock plus one-unit ask/bid for each displayed good. Do not store computed remote “profit” or executable quantity quotes.

The explicit observation action is selecting **Inspect price list and merchant funds** while near that merchant. It records the same local information shown by the menu without advancing time. The pure `market_rows` renderer, quote board, chart, port guide, and load path never refresh observations. Existing authored port/production descriptions remain public static facts; vessel `sighting_reports` remain a separate authority. One saved snapshot stays historical after prices/stocks/purses change; the quote board must label source, observation time, age and non-live status. No record means unknown, not a query against current remote state. Keep behavior absent from legacy/generated menus unless implementation evidence requires a compatibility-safe generalization.

### P2 current transaction and freedom implementation (2026-11-20)

- The authored transaction confirmation now follows a read-only preview that gives direction, selected quantity, quantity-sensitive total, goods versus per-order delivery charge, captain/merchant purse deltas, current→projected combined cargo/issued-kit mass and bulk, canonical time, and a commit-time revalidation warning. Buy capacity/purse and sale merchant-liquidity problems are called out before confirmation; actual `Economy.trade` still authoritatively rejects stale/invalid states atomically.
- The sell picker displays only `available_cargo`, excluding quantities reserved for contracts in legacy/non-authored modes. Authored free-trade mode has no contract reservation authority. Cancellation remains an immediate no-op; trade validation/time are unchanged.
- Keep the existing bounded string transaction history as an optional cargo-menu screen; keep `VoyageSystem.arrival_report` read-only and optional in authored play. Existing authored integration covers purchase→free chart passage→sale before opening the report→another voyage, with no arrival settlement, acknowledgment or report gate. No second history ledger or new arrival mechanism was justified by P0.
- Focused P2 regression after the preview and reservation-picker edits: economy/trade/authored/employment agreement-payroll-undertaking-review-lifecycle suites **134 passed in 37.82s**. New tests cover preview purity/details, sellable unreserved quantity/free cancel, and market depletion between preview/commit rejecting without partial transfer or hidden time. Arrival optionality remains covered by the authored integration and voyage report tests.

Combined post-edit verification, including foundation, encounter, provisioning, authored launch/save, trade, voyage and employment suites: **248 passed in 102.00s**. The authored Freeport walkthrough now sells before opening the report, then reviews it read-only and freely sails again; a separate test opens trade history without changing state or time.

Latest expanded P4 regression command (economy/trade/harbor/authored/voyage/save/encounter/foundation/provisioning/employment suites): **253 passed in 147.86s**. The new authored mid-passage reload assertion compares post-resume economy, voyage and ship snapshots after another canonical time step.

Native authored UI walkthrough: `python -u -B tools/capture_free_trade_m3_ui.py` completed with **60 scripted native UI states × 3 window sizes = 180 PNGs** and `screens.json` at `C:/Users/Warp/AppData/Local/Temp/opencode/free-trade-m3-ui-verified/`. It goes from public guide and unknown/chart notes through local quote inspection, free cancellation, a real buy, a same-merchant buy/sell loss, charted travel, sale at Freeport before the optional report, report review, and another committed voyage; a separate depleted-purse fixture shows a rejected sale with no partial transfer. The capture asserts actual 1280×800, 960×700 and 640×600 SDL output sizes. For a bounded presentation run it removes NPC vessels and sets the committed voyage duration to 1 second after capturing the underway UI; it still uses `World.advance`, canonical ship upkeep and arrival, but is not a normal-duration player passage or hands-on acceptance. Reviewed guide/chart, buy/sell preview, arrival, remembered-note and failure panels in compact and larger captures. Narrow panels paginate; long arrival detail lines are clipped horizontally at the smallest size, while destination, no-settlement, and continue-freely text remains visible. Scripted captures prove rendering/state-path wiring only, not unaided legibility or human acceptance.

### P3 actual circuit matrix — controlled quiet-passage comparison (2026-11-20)

Method: each strategy used a fresh authored world, ordinary player walking, Ada-local Pippin recruitment, `Economy.trade`, `VoyageSystem.confirm`, `World.enter_sea` and repeated `world.advance(6000)` until actual arrival. Started circuit accounting after Ada-side setup/hire: captain 565 Marks (35 signing already paid), aggregate captain+treasury+seven merchant purses 11,065, seven crew, Pippin at 1 Mark/day, 29.75 kg `food_stock_g`, 59.75 L clean water, 15 planks, 14 shot, hull 72%, rigging 86.95%. The scripted *quiet control* removed NPC vessels/report records and set the already-created forecast storm to its non-triggered outcome; route geometry, wind, sailing duration, crew upkeep, repairs, regional markets and time remained active. This is not ordinary-risk/player-acceptance evidence. One separate P0 actual-time route already passed three sightings and pressed through one storm, preserving its current cargo.

- **A — Verdant↔Cinder, bluegrain / emberpepper, repeated twice:** Loop 1 bought 8 bluegrain from Ada for 162 and sold to Dara for 396; bought 4 emberpepper at Dara for 116 and sold to Ada for 312: captain cash **+430**. Loop 2 quotes changed with stock: buy/sell bluegrain 197/316 and pepper 129/298; gross trade difference +288, one 1-Mark day wage paid, actual cash **+287**. Across 18.716 game hours: cash 565→1,282 (**+717 after the paid wage**), aggregate silver -1 (the wage sink), empty cargo, food stock -3.50 kg, clean water -11.75 L, planks -1, shot unchanged, hull 72→100, rigging 86.95→92.82, day 1→2. End stocks included Ada bluegrain 16 / emberpepper 6, Dara bluegrain 14 / emberpepper 8; Ada purse 1,245, Dara 1,029.
- **B — Verdant↔Cinder, lantern-moth silk / reef porcelain, repeated twice:** Loop 1: silk buy/sell 144/389, porcelain 128/344; cash +461. Loop 2: silk 160/351, porcelain 141/328; gross +378, wage 1, actual cash +377. Across 18.726 game hours: cash 565→1,403 (**+838 after wage**), aggregate silver -1, empty cargo, food -3.50 kg, water -11.75 L, planks -1, shot unchanged, hull 72→100, rigging 86.95→92.78, day 1→2. End stocks: Lina silk 5 / porcelain 6, Petra silk 6 / porcelain 5; Lina purse 1,128, Petra 1,025.
- **C — Verdant↔Freeport, grain / sea-glass beads, repeated twice:** Loop 1: grain buy/sell 45/45, beads 89/197; cash +108. Loop 2 after changed stock: grain 55/36, beads 89/187; gross +79, wage 1, actual cash +78. Across 27.958 game hours: cash 565→751 (**+186 after wage**), aggregate silver -1, empty cargo, food -5.75 kg, water -17.25 L, planks -1, shot unchanged, hull 72→100, rigging 86.95→99.65, day 1→2. End stock: Ada grain 24, Nella grain 16 / beads 8, Lina beads 6; Ada purse 1,596, Nella 1,593, Lina 1,116. First grain leg itself broke even; return beads leg carried most of this route's margin.

Each repeat run took 247.43s / 246.86s / 426.13s wall time respectively on this machine (including factory/setup and normal simulation). These are scripted API selections, not human-played routes. Each loop paid two 2-Mark delivery-order fees, already included in the buy totals; fees moved into treasury. All final cargo was zero; the actual trade flow transferred money among merchant/captain purses and delivery-fee treasury. Across each pair only the one wage Mark reduced aggregate silver; signing was paid before the route baseline.

Replacement-cost cross-check (opening day-1 stock-sensitive asks, quote includes order fee; a **shadow basket**, not an actual resupply transaction): grain quantity 1 = 7 Marks for 4 kg; grain quantity 2 = 12 Marks for 8 kg; Bo's water cask 1 = 9 for 10 L, refill 1 = 7 for 5 L, water cask 2 = 17 for 20 L; Lina planks 1 = 12 and yields 2 planks. At those opening quotes, measured resource replacement baskets are A/B: 1 grain + 1 cask + 1 refill + 1 plank bundle = **35 Marks**; C: 2 grain + 2 casks + 1 plank bundle = **41 Marks**. Subtracting those conservative full-item baskets from actual route cash deltas leaves +682 / +803 / +145 Marks; A/B/C cash deltas already include one actually paid wage Mark each. This coarse mass replacement treats mixed food as grain-equivalent and buys whole lots (including a surplus plank/water); it does not price crew sign-on again, repair condition gain as an asset, future price changes, or an actual allocation trip. The 35-Mark Pippin signing advance is a one-time setup expense already excluded from route deltas; net captain cash versus the original 600-Mark start after these proxy baskets is +647 / +768 / +110.

Interpretation: three distinct two-leg strategies have positive measured cash after their own repeated stock/purse response and a conservative opening-quote replacement proxy, with the third much thinner. A/B/C are each repeated twice and each crossed a payroll/day boundary once. This is a small deterministic quiet-control sample with one recruit, one market-restock horizon, no triggered weather/contact, and initial hull repair from 72→100; do not claim universal profitability, long-run solvency, low risk or human usability. Same-market loss and split/bulk, reload, purse, waiting, repeated-sale and time-chunk probes are covered in `tests/test_economy.py`.

## 4. Information contract

Classify every displayed fact:

| Kind | Disclosure and limits |
| --- | --- |
| Public authored fact | Port identity, general exports/imports, merchant categories and known route geography. Label as general guidance, not current availability or guaranteed profit. |
| Local quote | Actual reachable merchant, current observation time, bid versus ask, quantity and applicable fee. Revalidate at commit; observation is not a price lock. |
| Remembered observation | Persist observed port/merchant/item, quote direction/value or explicitly supported quantity basis, observation time and provenance. Display age; never silently refresh remotely. |
| Report | Source, observed/reported time where known, scope and uncertainty. Do not mint reports or trusted contacts solely to populate an empty panel. |
| Unknown | Clearly say not observed/unknown; no hidden-state fallback. |

Only meaningful observation actions acquire changing information. Rendering, browsing, opening a chart and loading a save must not refresh knowledge or advance time. Old observations remain historical after market changes. No arbitrary expiration threshold or skill-based precision rule without an explicit design decision and tests.

Use the P1 `Economy.market_observations` contract above; do not maintain competing quote caches in UI, chart, or NPC fleets. The latest-per-merchant records must be bounded, validated, keyed by stable catalog/merchant identities, and safe on old saves. Optional history may summarize actual transactions; do not reconstruct exact historical profit from insufficient text records.

## 5. Transaction and economic rules

Retain `Economy.price`, `quote`, `trade`, cargo reservations, physical supply allocation and canonical time as authorities. A preview states direction, quantity, total cost/proceeds, included fees, load impact and elapsed time. Stock-sensitive quantity totals must not be replaced with unit-price multiplication.

Commit rechecks proximity, funds, merchant liquidity, stock, reserved cargo and actual mass/bulk capacity. Failure/cancel leaves money, goods, reservations and time unchanged unless an existing explicitly disclosed action legitimately spends time. Test repeated input and stale previews; no replayed or partial transfer.

Do not invent docking fees, taxes or passage fares for the sake of an expense model. Measure only implemented charges, including delivery fees, signing costs, recurring wages, provisions, fuel and repairs when actually incurred. Avoid charging allocated supplies twice: cash flow and economic resource consumption are separate measures.

No live remote-stock queries through guide, chart, search, history or recommendations. Public regional demand descriptions may guide exploration without revealing current purse or stock.

## 6. Controlled economic evidence

Before tuning prices, record baseline circuits using normal supported purchases and sailing, not instant travel or fabricated profit:
- At least three distinct multi-leg cargo/route strategies across the authored ports, including repeated circuits to expose market and purse depletion.
- For each: starting money/cargo/supplies, crew and agreements, scenario, routes, elapsed game time, quantities and actual fills, gross sales, purchases, delivery fees, wages, provisioning, repairs, ending cash/assets and market state.
- Separate first-circuit setup costs from recurring costs; report how many circuits and simulated days were measured. Record actual wall time.
- Include a lawful quiet-passage control for interpretable trade economics, explicitly documenting how encounters/weather are controlled; separately verify ordinary interruptions preserve cargo/time. Do not turn controlled quiet travel into a frequency/safety claim.
- Count consumable depletion and outstanding arrears. A captain ending with cash while exhausting unpaid wages or free starter supplies has not demonstrated a sustainable circuit.
- Include a deliberately poor purchase/destination or excess-quantity choice that loses money without softlocking the player.

Anti-exploit probes: same-merchant buy/sell cycles; split versus bulk orders with legitimate per-order fees; repeated same-day trades; waiting across one/multiple day boundaries; depleted buyer purses; buy/reload/sell; stepped versus bulk elapsed time. Waiting may legitimately restock goods under existing rules, but cannot duplicate funds, bypass upkeep or reset markets on load. Do not impose an arbitrary anti-trading cooldown to hide a broken price/purse model.

If sustainable trade is not achievable with current numbers, record the actual deficit and cause before a narrow tuning proposal. Do not delete upkeep, award subsidy money or replace free trading with delivery rewards to manufacture a pass.

## 7. Implementation phases

| Phase | Work | Exit evidence |
| --- | --- | --- |
| P0 — baseline and authority audit | Preserve worktree; trace normal launcher→guide/chart→merchant→sail→arrival→sale; identify knowledge/save owners and existing gaps; baseline relevant tests and circuits. | Source-backed authority map, current UI paths, concrete defects, measured untuned economics and exact baseline results. |
| P1 — trustworthy information | Implement only missing observation/report persistence and local/remembered/unknown distinctions; integrate with existing chart/guide. | Remote market mutations cannot alter unobserved panels; local observation updates deliberately; age/source persist; browsing is pure. |
| P2 — transaction clarity and freedom | Connect previews, cargo/reservation/capacity disclosure, optional histories and ungated arrival using existing owners. | Real buy/sail/sell through native input paths; free cancel/atomic failure; no required contract, grade or settlement; no automatic sales. |
| P3 — measured economic viability | Run the circuit matrix, investigate cost/market failures, tune only justified values and retest exploit probes. | Several repeatable self-chosen strategies cover measured recurring costs over stated horizons; a bad choice can lose; waiting/load/quantity invariants pass. |
| P4 — integrated verification | Save/reload all new states; regress legacy/authored isolation; run walkthrough, native captures, compilation and whitespace checks; update plans/skill. | Full scoped evidence ledger, honest limits and exact commands/results; no unsupported full-suite or human-playtest claim. |

P0–P4 are implemented and scoped-verified. Reuse already-working features rather than rebuilding them to match phase headings.

## 8. Acceptance matrix

| Gate | Required verification |
| --- | --- |
| Information usefulness | Player can identify plausible goods/buyers/routes from public guidance and actual observations without a prescribed guaranteed route. |
| Remote knowledge | Change unseen remote prices, stock and money; remembered/unknown projections remain identical until supported new observation/report. |
| Purity | Chart, quote inspection, histories, cancellation and report dismissal do not unexpectedly mutate world/knowledge or advance time. |
| Real trade | Actual merchant UI buy→canonical passage→arrival→sale changes finite money/goods exactly once with valid costs/load. |
| Reservation/capacity safety | Reserved, equipped and allocated goods cannot be sold twice; full cargo and insufficient stock/purses reject atomically. |
| Sustainable choice | Documented repeat circuits account for wages, consumable depletion and fees; show multiple strategies and a losing choice. |
| Persistence/time | Mid-passage and remembered-quote reloads preserve facts, quantities and meaningful deterministic continuation; day-boundary chunking agrees. |
| Freedom | No contract acceptance, report acknowledgement or arrival settlement required before trading/sailing again. |
| Boundaries | Authored scenario/slot identity persists; generated/sim entry points are not called; legacy behavior remains compatible. |
| Native presentation | Inspect chart/guide, local quote, remembered/unknown quote, confirmation/cancel, failure, history and ordinary arrival at 1280×800, 960×700 and 640×600. |

Scripted native captures verify presentation, not unaided comprehension. Human release acceptance remains M8.

## 9. Verification workflow

Working directory: `E:/Pirates of Miska`.

```powershell
python -B -m pytest -q tests/test_economy.py tests/test_trade_expansion.py tests/test_harbor_navigation.py tests/test_authored_scenario.py tests/test_voyage.py tests/test_voyage_save.py tests/test_sea_encounters.py tests/test_foundation.py tests/test_provisioning.py tests/test_employment_agreements.py tests/test_employment_payroll.py tests/test_employment_undertaking.py tests/test_employment_review.py tests/test_employment_lifecycle.py
python -u -B tools/capture_free_trade_m3_ui.py
git diff --check
```

The authored/economy/trade/harbor/voyage/save/encounter/foundation baseline passed 177 tests before P1. The expanded foundation/encounter/provisioning/employment integration command passed **248 tests in 102.00s**; the latest M3 regression passed **253 in 147.86s**; `tests/test_economy.py` separately passed **59 in 41.83s**. In-memory `compile()` passed for the six changed M3 Python files (`pirates/economy.py`, `trading_ui.py`, `management.py`, `tests/test_economy.py`, `tests/test_authored_scenario.py`, `tools/capture_free_trade_m3_ui.py`). Direct trailing-whitespace/final-newline scan passed for the six new/updated plan, test, harness and skill artifacts; `git diff --check` passed (Git emitted only its repository-wide LF→CRLF advisories). MVP/master plans and the global project skill were updated. P4 evidence is audited; M3 is scoped-complete, not human-accepted or a claim about M4–M8/full MVP.

## 10. Decisions to resolve during P0

1. **Resolved in P1:** economy-owned latest local market snapshot, explicitly recorded by local price-list inspection; no duplicate society/sighting authority.
2. **Resolved in P1:** existing authored public port descriptions are static; absent market snapshots remain unknown until explicit local inspection. No new report source is needed.
3. **Resolved in P1:** persist per-unit ask/bid and observed stock/purse with source and timestamp; explicitly label as stale/non-live, not an executable remote offer.
4. **Resolved in P2:** existing bounded string trade history is sufficient for the optional display; arrival checkpoint/report is read-only and optional. No separate transaction ledger is justified.
5. **Measured in P3, bounded:** the tested starter + one Pip (1 Mark/day) configuration covers three repeated two-leg strategies against opening-quote whole-lot food/water/plank replacement proxies. One tested route was materially thinner; starter conditions, horizons and quiet-control limits are in §3.2; not a broad solvency claim.

Record decisions and evidence before implementation. Ask the user only for genuine design choices that cannot be resolved from parent scope/source behavior.

## 11. Completion ledger

| Phase | Status | Evidence |
| --- | --- | --- |
| P0 | Complete | Source-backed authority map; 177 focused tests; actual-time two-loop hired-crew circuit across a day boundary with market, provision, repair, wage and storm deltas; and a same-market -10-Mark choice recorded. This does not prove long-run solvency or balance. |
| P1 | Complete | `Economy.market_observations` stores one bounded latest local snapshot per merchant; only authored local price-list inspection records it. Read-only chart/guide/note UI shows source, age and non-live status or unknown; remote mutations do not leak. Round-trip, old-save migration, malformed source/time/item rejection, explicit refresh and authored/legacy boundary tests pass. Scoped suites: 181 before final test-only additions; current authored suite 26 passed. |
| P2 | Complete | Confirmation preview discloses exact quantity-sensitive cost/proceeds, fees, time, cash/purse and capacity deltas; sell UI excludes reserved cargo; core failures remain atomic. UI tests cover free cancel and stale-stock revalidation. Optional history is read-only and authored arrival accepts sale before report, then further sailing. Combined scoped suites passed 248, then latest **253**. |
| P3 | Complete | Three distinct two-leg authored strategies, each repeated twice under a documented quiet control, crossed one day/payroll boundary, and reported real cash/stock/purse/wage/provision/repair/time/wall-time deltas. Opening-quote whole-lot resource replacement proxy leaves +682/+803/+145 across the three two-loop runs; a real same-market choice loses 10 Marks. Split/bulk, same-day repeat, day-boundary restock/payroll idempotence, buyer-purse failure, buy/save/reload/sell, repeated-sale replay, and stepped-vs-bulk boundary tests pass. No tuning justified by this sample. Scripted controlled data only, not a long-term risk/balance/player claim. |
| P4 | Complete | `test_authored_midpassage_reload_preserves_observation_trade_and_continuation` verifies saved observation, cargo, merchant stock, underway route and identical continuation. Latest combined scoped regression: **253 passed in 147.86s**. The scripted authored walkthrough captured 60 UI states / 180 PNGs at actual 1280×800, 960×700 and 640×600 SDL output sizes and was visually reviewed; it exercises free cancellation, a same-market loss, underfunded-sale rejection, sale before report, report review and a second voyage order. The first attempt's repeated arrival-report choice caused an infinite menu loop; the harness now sequences report→new voyage→exit and has a choice cap/fail-fast walking. For presentation only, it disables NPC vessels and shortens the first passage to 1 second after capturing underway; canonical `World.advance`, upkeep and arrival still run. In-memory compilation, direct trailing-whitespace/final-newline scan, and `git diff --check` passed. Parent/MVP/master plans and project skill were updated. Narrow 640 panels paginate and long arrival details clip horizontally; key destination/no-settlement/freedom text remains visible. Scripted evidence is not unaided comprehension or M8 acceptance. |

M3's scoped information-loop gate is complete based on the evidence above. This does not complete M4–M8, human release acceptance, or the combat-inclusive MVP.
