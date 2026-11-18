# Shipboard Playability and Balance Plan

Date: 2026-11-17
Status: implementation in progress under the user's active shipboard-playability goal.

## Goal

Make the existing supply and daily-life systems understandable, reliable and enjoyable through a complete port-to-port voyage before adding more simulation systems.

Build on `SHIPBOARD-SUPPLIES-AND-DAILY-LIFE-PLAN.md`. Preserve unrelated shipbuilding, combat, NPC and headless `sim/` work. Recheck current failures before treating earlier test results or blockers as current.

## Phase 1 — Restore reliable harbor access

- Reproduce the trading-preview failure reaching Ada at `(30, 21)` from a fresh world.
- Establish whether the cause is authored geometry, actor occupancy, door handling, or the preview's route assumptions before changing anything.
- Ensure the Captain can reach valid interaction positions beside every merchant, the harbor office, the gangway and the ship hold in both ports.
- Use normal movement and door interactions; do not teleport, disable collision, or move merchants solely to make a test pass.
- Confirm passage/arrival and save/load do not strand the Captain or corrupt harbor state.

Acceptance:
- [ ] Native trading preview completes through both ports.
- [ ] Automated access tests cover each merchant and the port-to-ship return route.
- [ ] Trade, allocation, funds, stock and cargo remain conserved.

## Phase 2 — Exercise the complete voyage loop

Create a repeatable, save-free scenario using normal gameplay entry points:

1. Inspect crew, ship condition, money, available food and water.
2. Choose a route and inspect its supply forecast.
3. Buy finite supplies; pay normal prices and delivery fees.
4. Allocate supplies, fill vessels, issue gear and set galley policy.
5. Depart and advance through several game-days with ordinary crew work enabled.
6. Observe cooking, eating, drinking, rest, shared meals, freshness, weather exposure and fuel consumption.
7. Save/reload mid-voyage and continue without resetting supply ages or duplicating items.
8. Arrive, restock, repair/mend where needed and return.

Run separate scenarios for adequate provisioning, a recoverable food shortage, insufficient potable water, unavailable cooking labor, blocked station access, and a delayed voyage. Do not force unrelated tactical combat into the baseline scenario.

Record elapsed game-time, crew needs/health, completed jobs, missed meals, consumption, spoilage, fuel, spending and any repeated blocked actions. Reconcile input/output quantities, including waste and evaporation, rather than expecting unchanged total mass.

Acceptance:
- [x] Adequately provisioned crew can finish the loop without unexplained starvation, thirst or permanent job deadlocks.
- [x] Shortage scenarios fail honestly and expose a feasible recovery where one exists.
- [x] Forecast assumptions and observed consumption are compared over the same time horizon.
- [x] Save/resume and chunked advancement produce equivalent simulation state.
- [x] Native screenshots show departure readiness, at-sea supply status and arrival state.

### Implementation evidence (Nov 17, 2026)

- The native harbor route failure was reproduced on a fresh world and fixed; every merchant in both ports and the harbor office are reachable by ordinary movement. The 13-frame two-port trade preview and three harbor navigation tests pass.
- A fresh-world moored run from Day 1 08:00 to Day 2 08:00 started with 8 kg ready food and 60 L clean water. The forecast estimated 2.33 days of ready food and 4.55 days of water. By midnight (16 game hours), the crew had consumed 13 kg food and 58 L drinking water; the forecast budgets for that same horizon were 2 kg and 8.8 L. Water reached zero at 23:00; all six crew collapsed by 01:00 and died by 07:00 Day 2 from prolonged untreated hunger/thirst.
- A paid 9.43-hour Verdant→Cinder→Verdant round trip, with 5 water kegs and 20 biscuit units bought at Verdant and 3 kegs/8 biscuit units bought at Cinder, passed save/reload, arrival and restock. It spent 313 Marks total, returned with all six crew at full health, completed 29 work orders, and left ship geometry unchanged. Recorded counters: 7.25 kg food and 39.5 L drinking water.
- A capacity-aware variant bought 15 kegs and 30 biscuit units up front to leave room in the 250 kg water stockpile for the destination restock. The measured three-round-trip paid native run completed in 53.42 ship-hours: all six crew survived at 100 health, all 41 observed jobs completed, the captain returned to Verdant, geometry stayed unchanged, and order invariants were clear. It consumed 43 kg food and 227 L drinking water, spent 451 Marks total (253 at Verdant, 198 at Cinder), and returned with 3 kg ready food (2 kg immediately edible) but no potable water; thirst was 83.4–87.9. The third Cinder→Verdant return took 106,999 seconds against its 14,606-second estimate because the sustained light-wind state yielded only 0.10 m/s target speed. This explains the delayed voyage as a large forecast/actual weather-speed mismatch, not a stuck loop. A replay from the saved 15-minute mid-route state arrived deterministically in another 106,099 ship-seconds and 311 wall-seconds. Inputs reconcile over the whole scenario: 30 kg initial food + 3.8 kg paid biscuits + 13 kg clean water used in mass-conserving recipes = 43 kg eaten + 3 kg edible food + 0.8 kg food waste; 60 L initial + 150 L Verdant + 30 L Cinder clean water = 227 L drunk + 13 L cooked. Final questionable rainwater was 0.96 L and is not potable; lights/fuel were unused. Whole-hold mass fell from 54,998 kg fresh to 54,936.5 kg at paid departure and 54,698.8 kg at return; the departure→return change reconciles within rounding as +30.8 kg Cinder provisions +0.96 kg questionable rainwater −42.75 kg food eaten −226.75 kg drinking water = −237.74 kg, versus −237.7 kg observed. The five-round-trip attempt was stopped after trip 4 arrived with all crew dead and no food/water; the three-trip run is the completed shorter baseline, not evidence that the 5-trip loop is adequate. Before tuning, same-horizon consumption was ~3.22 kg food and ~17.0 L drinking water per crew/day versus forecasts of 0.50 kg and 2.2 L.
- Profiling 600 simulated sailing seconds found repeated component sorting/topology construction (31.1M calls / 17.14 s under cProfile); cached component/navigation/deck geometry reduced this to 7.46M / 5.77 s under cProfile. Replacing four candidate route searches with a single multi-goal BFS brought the unprofiled 600-second interval to 3.10 s. The route/path cache invalidation and first-upper-floor access regressions pass in the ship model, navigation, harbor, building and physics tests.
- Evidence-led need-rate tuning preserved the 250 g serving, 250 mL drink and 75 thirst-points/L relief. Thirst rose at 0.015/s in the old model; reducing it to 0.002/s predicts about 2.3 L/person/day at the 65-point drink threshold. Its isolated 24-hour run consumed 12.5 L drinking water (2.08 L/person/day; 0.95× the 2.2 L forecast) and 17.5 L clean water including 5 L cooking; all six stayed at 100 health. Hunger rose at 0.008/s in the old model; reducing it to 0.0015/s dropped 24-hour food use from 18.5 kg to 5.0 kg for six crew. Updating the forecast from 0.50 to 0.85 kg/person/day predicts 5.1 kg for the same horizon (actual/forecast 0.98); all six remained at 100 health, ending with 5 kg ready food, 46.5 L clean water, and no open orders. Current tested rates: hunger 0.0015/s, thirst 0.002/s; serving/relief constants unchanged. Final same-horizon report: `C:/Users/Warp/AppData/Local/Temp/opencode/pirates-provisioning-balanced-v2.json`; original thirst-only run: `.../pirates-provisioning-thirst-tuned.json`.
- The original 15/30 Verdant plus 3/8 Cinder paid plan also passed under tuned rates in 53.73 hours, consuming 11.25 kg food and 29.25 L drinking water with six crew alive at 100 health, 41 jobs completed, and return to Verdant. It spent 451 Marks but returned with 5.5 kg ready food (4.5 kg edible) and 208.75 L clean water; 1.05 kg spoiled. A same-horizon no-purchase three-round-trip run also passed: 53.42 hours, 11.25 kg food, 29 L drinking water, all six crew at 100 health, all 41 jobs complete, no food waste, and return to Verdant with 5.75 kg ready food (4.75 kg edible) and 28 L potable water. It made no market transactions and retained all 600 Marks; therefore the original 451-Mark provision purchase is unnecessary for this three-trip scenario on the fresh-world starting inventory. No-buy report: `C:/Users/Warp/AppData/Local/Temp/opencode/pirates-paid-supply-tuned-no-purchase-v7/verification.json`; tuned paid report: `.../pirates-paid-supply-tuned-v6b/verification.json`. Temporary verification folders were later cleaned; the recorded figures in this document are the retained evidence, and the runners can regenerate the reports.

## Phase 3 — Tune measured rates and costs

Measure first; change one connected balance group at a time. Record before/after rates and the reason for every adjustment. Suggested values are experiments, not fixed requirements.

| Area | Questions to answer |
| --- | --- |
| Drinking | Does the serving size and thirst growth produce sensible daily use without excessive repeat actions? |
| Cooking | Can available cooks maintain food while performing other duties? Are water reserves and recipe yields understandable? |
| Rain/treatment | Is collection useful but insufficient to erase provisioning decisions? Are fuel use and treatment losses worthwhile? |
| Freshness | Can fresh food be used or preserved before expiry? Are warnings early enough to act on? |
| Apparel | Does rain protection matter? Are wear rates visible over a practical voyage? Is a whole bolt of sailcloth per mend excessively expensive? |
| Light | Are fuel duration and visibility benefit worth the purchase and interaction cost? |
| Shared meals | Do pairs actually meet and eat? Are waiting times reasonable without delaying critical needs or work? |
| Hygiene/care | Are costs clearly justified by their actual effects without inventing health benefits? |

Prefer existing configuration/data conventions. Do not rebalance by creating free resources, resetting age/condition, bypassing capacity, or weakening conservation checks.

Acceptance:
- [x] Representative short and multi-day voyages have documented resource budgets.
- [x] Each tuned mechanic produces an observable decision rather than repetitive mandatory clicking.
- [x] Forecasts remain consistent with tuned runtime rates.
- [x] Conservation, migration, determinism and focused behavior tests pass after each balance group.

## Phase 4 — Make problems and remedies obvious

Provide a compact supply/readiness summary, integrated into existing reports before adding new menus:

- Ready food versus ingredients requiring cooking; protected reserves shown separately.
- Potable water versus questionable water, including vessel contents without double-counting.
- Estimated coverage for the selected voyage, with explicit assumptions and uncertainty.
- Earliest expiry and exposed spoilage risk.
- Galley status and the actual blocking cause: labor, access, ingredients, fuel, target or policy.
- Worn clothing and available repair requirements; current light fuel and burn state.
- A useful next action for each warning: buy, allocate, fill, assign a cook, unblock access, change policy, preserve or shorten the route.

Warnings should describe their scope and avoid repeated per-tick spam. Read-only reports must not advance time, change policies or mutate inventory. Revalidate warnings after supplies/policy/route changes.

Acceptance:
- [x] A player can identify the main departure risk and its remedy from one summary.
- [x] Unsupported effects are never implied: no temperature model, soap health benefit or flame ignition unless separately implemented.
- [x] Empty stores, protected reserves, stranded cargo and inaccessible stock are distinguishable.
- [x] UI values match authoritative state; menus remain time-frozen.

### Implementation evidence (Nov 18, 2026)

- `readiness_rows()` in `pirates/provisioning.py` builds one read-only summary from authoritative state. It is embedded in the existing `ship_rows` ledger under `SUPPLY READINESS (read-only)` and appended to `voyages.role_report` as `Next:` remedies, so no new menu was added.
- Rows separate ready food from cook-only ingredients and projected cooking coverage; potable water from questionable water and vessel contents (without double-counting); protected biscuit and drinking reserves; target-route margins with explicit per-crew-day assumptions and contrary-wind uncertainty; earliest cooked/fruit spoilage; galley mode, reachable cooks and a single cause/remedy pair; worn clothing for clothing slots only with sewing-kit/sailcloth steps; light type and remaining fuel; and unallocated trade cargo excluded from the provision totals.
- `forecast_provisions` now exposes `cook_only_g` and a structured `galley_status` with `cause`/`remedy`, replacing duplicated inline blocker wording. Galley blockers cover policy, missing/unavailable fixture, missing cook labor and unreachable cooks; there is no galley fuel model, and ingredient/cooking-water shortfalls are reported as food/water rows rather than invented causes.
- Read-only purity is enforced by a regression that snapshots stores, clock, policy, batches, fruit lots, containers, money, cargo and gear condition around the call. Additional tests cover water separation, galley access/pause remedies, spoilage/light/cargo rows, route shortfall that also appears in the voyage report, and ledger integration without advancing time. `tests/test_provisioning.py` passes 15 tests.

## Phase 5 — Final integration and handoff

- Run relevant supply, economy, voyage, storage, crew, fixture and shared-meal regressions.
- Run the full repository suite and report all current failures; separate unrelated failures with evidence rather than silently filtering them away.
- Run compile checks and `git diff --check`.
- Inspect native trading, ship and voyage previews, with normal visibility where possible; label developer-revealed captures.
- Record scenario evidence and balance decisions in this design document or an explicitly scoped project report. Keep agent memory in the global skill, not the project.
- Preserve existing saves and unrelated changes; no commit/reset/cleanup unless requested.

Starting commands from `E:/Pirates of Miska`:

```powershell
python -m pytest -q tests/test_shared_meals.py tests/test_ship_life.py tests/test_ship_storage.py tests/test_economy.py tests/test_ship_physics.py tests/test_provisioning.py tests/test_water_containers.py tests/test_voyage.py tests/test_trade_expansion.py
python -m pytest -q
python -m compileall -q play.py pirates tools tests
git diff --check
python tools/preview_trading.py --output 'C:/Users/Warp/AppData/Local/Temp/opencode/pirates-playability-trading'
python tools/preview_ship.py --output 'C:/Users/Warp/AppData/Local/Temp/opencode/pirates-playability-ship'
python tools/preview_voyage.py --output 'C:/Users/Warp/AppData/Local/Temp/opencode/pirates-playability-voyage'
```

### Implementation evidence (Nov 18, 2026)

- The nine-file focused command listed below passed 201 tests in 103.94 s under concurrent machine load (shared meals, ship life, storage, economy, physics, provisioning, water containers, voyage, trade expansion).
- `python -m compileall -q play.py pirates tools tests` completed cleanly, and `git diff --check` reported no whitespace errors. The working tree still contains many unrelated modified/untracked files from concurrent work; none were reverted or cleaned.
- Full-suite verification on the current tree, `python -m pytest -q --deselect tests/test_sim_lod.py::test_ten_year_default_run_meets_named_character_scale_target --deselect tests/test_sim_memory.py::test_multi_decade_world_produces_inherited_vengeance_after_mentor_death`: **1148 passed, 3 failed, 11 xfailed, 2 deselected in 2190.33 s**. Log and the matching `git status --porcelain` snapshot are in `C:/Users/Warp/AppData/Local/Temp/opencode/pirates-full-suite-current.txt`.
- All three failures are in the untracked headless `sim/` subsystem and were re-run on their own, failing again (3 failed in 866.73 s): `tests/test_sim_politics.py::test_world_day_loop_runs_and_snapshots_monthly_faction_politics` (faction-politics state assertion fails), `tests/test_sim_world_snapshot.py::test_one_year_cli_run_persists_weather_calendar_and_transition_events` (`sim/systems/battles.py:588` raises `ValueError: Legend character has invalid special traits` during `run_sim` save) and `tests/test_sim_world_snapshot.py::test_batch_harness_reports_annual_trade_and_balanced_ledgers` (3-year `sim.tools.batch_test` exits 1 with `major_plus_deaths_or_retirements: 0`). `sim/` and every `tests/test_sim_*.py` contain no `import pirates`/`from pirates`, so the shipboard changes cannot produce or fix them; they belong to concurrent simulation work that is still untracked in git.
- Earlier full-suite attempts that included the two deselected multi-year tests never reached a verdict: one passed everything up to 80% and then sat in the 10-year LOD test (day 1459 of 3650 after ~40 min, with `famine_level: 100` and 1224 consecutive shortage days in the sampled locals), and a 30-minute isolated retry also failed to finish. A fresh isolated retry measured day 770 of 3650 after ~9 minutes of wall time, so its own `<300 s` budget is already exceeded under this load (the same test measured 65 s on an idle box). The deselected 20-year memory test is the same class of multi-year workload. Neither imports `pirates/`; both are environment-blocked rather than shipboard regressions.
- Native previews ran under normal game visibility (no developer reveal flags): `tools/preview_trading.py` passed 13 two-island trading captures including merchant approach and trade screens; `tools/preview_ship.py` passed 13 ship-walkthrough captures; `tools/preview_voyage.py` passed 4 sea-voyage captures. Outputs are under `C:/Users/Warp/AppData/Local/Temp/opencode/pirates-playability-{trading,ship,voyage}`. These are scripted native captures, not claims of unaided human playtesting.
- The one-round-trip paid supply run passed with 4 captures: `01-departure-readiness.png` (paid departure forecast, 10.75 kg ready food and 209.8 L potable water after 15 Verdant kegs), `trip-01-cinder-arrival.png`, `03-cinder-restock.png` (3 kegs/8 biscuits paid and allocated) and `trip-01-verdant-return.png`, under `.../pirates-playability-supply-1trip`. The runner's summary report was later overwritten by the auxiliary at-sea capture; the captures and `mid-voyage-save.json` remain, and rerunning the runner regenerates the report.
- The saved mid-return at-sea state was re-rendered with `tools/capture_voyage_supplies.py` under the same output folder. `at-sea-supply-readiness.png` shows the live `readiness_rows` summary while underway: 9.05 kg ready food (1.8 crew-days), 46.05 kg with cooking (9.0 crew-days), 238.0 L potable (18.0 crew-days), protected reserve, positive route margins, fruit spoiling in 42 h, a reachable galley with six cooks and light state. `at-sea-verification.json` records the source save, remaining hours and rendered rows. The three-trip paid and no-purchase runs above remain the multi-day playability evidence.

## Deferred expansion — Crew relationships

After this playability pass, separately scope deeper shipboard interactions around existing meals, duties, personalities and grievances:

- Recurring companionship and disagreements arising from actual shared activities.
- Small, bounded relationship effects with visible causes and opportunities to recover.
- Captain responses to disputes, fatigue and unfair duty distribution.
- Persistent memories and understandable crew-facing reports.

Do not begin this expansion as part of harbor fixes or balancing. First audit existing crew/NPC relationship systems to avoid introducing a competing source of truth. Avoid magical friendship gains, off-screen invented meetings and relationship bonuses that override critical duty or needs.

## Completion gate

- [x] Harbor access works through normal interactions.
- [x] Complete multi-day provision → sail → maintain → arrive → restock loop verified.
- [x] Balance changes have measured evidence and passing regressions.
- [x] Supply summary explains risks, causes and remedies accurately.
- [x] Save compatibility, conservation and deterministic continuation preserved.
- [x] Full-suite results and native preview evidence recorded with remaining limitations.

Recommended first delivery: **Phase 1 harbor access fix plus its automated and native verification**, then the baseline voyage scenario before tuning anything.
