# Authored Archipelago — First MVP System Expansion

Date: 2026-11-19
Status: authored foundation implemented and scoped verification passed; the combat-inclusive MVP remains incomplete.
Parent: `LAWFUL-TRADER-MVP-PLAN.md` M0/M1 and the narrow free-travel requirements of M3.

## 1. Why this is first

Make the **authored world map, ports and scenario launch** a functioning foundation before expanding rewards or ship preparation. The player needs places to buy, sail and sell, while the world simulation remains disconnected. This slice must work without waiting for combat, but it is not the completed combat-inclusive MVP.

Concrete deliverable:

> Launch a clearly labelled authored MVP, inspect a connected chart, freely sail among at least three functioning differentiated trading ports, buy/sell using existing finite markets, meet stable sample identities, and save/reload without generated history or strategic world development.

Reuse the current ship, crew, time, markets, travel, encounters and saves. Do not build a second game world engine.

## 2. Scope and non-goals

### Included

- Explicit scenario mode, versioned authored data and safe save-slot selection.
- Three working ports, existing non-port chart locations and meaningful route alternatives.
- Static sample NPC/faction identities and bounded local vessel travel.
- A useful chart and port/service/route information, with honest knowledge limits.
- Finite merchants and ordinary free buy/sell; initial guides explaining regional trade.
- Travel/arrival that returns control without requiring delivery contracts or a voyage grade.
- Isolation, compatibility, conservation, determinism and live-launch/native UI tests.

### Excluded

- `sim/` attachment, generated society/prehistory, off-camera faction wars, lore, affinities or autonomous economic conquest.
- New combat mechanics, request consumption, at-sea merchant transactions, physical other-ship boarding, pursuit geometry or encounter recurrence redesign. Existing requests must remain intact and labelled unfinished where exposed.
- Prize ships, bounty payments, salvage, reputation unlock implementation and skill-system expansion.
- Shipbuilding/storage rewrites, new cargo-capacity formulas or unrelated balance tuning.
- Magic, land exploration, swimming, fleets, procedural island generation and universal NPC planners.
- Deleting legacy contract data or changing another running session's combat/simulation work.

Unfinished future features must not be advertised as available services. Do not quietly disable existing ship work/needs/wages merely to make the demo run faster.

## 3. Current source anchors — verify again before editing

| File | Actual role / integration risk |
| --- | --- |
| `play.py:1478-1508` | `--new` and ordinary fresh launch create `World.new_generated`; smoke mode uses authored `World()`. `--history-days 0` alone is not proof of authored isolation. |
| `pirates/game.py:49-113` | Existing authored starter construction, economy/fleet/society initialization. Keep ship initialization and crew fixtures. |
| `pirates/game.py:116-129` | Generated manifest/society/prehistory factory. Must not execute for the new mode. |
| `pirates/game.py:231-272` | Per-second NPC/society hook, canonical advance, payroll, commitments and arrival checkpoint. Freeze strategic society development only in the new mode. |
| `pirates/game.py:434-487` | Save envelope version 3; atomic temporary-file replacement; load constructs replacement world and checks economy port against `PORTS` before restoring the rest. Select scenario before port validation/reconstruction. |
| `pirates/economy.py:19-53,64-166` | Import-time catalog/rule/merchant validation and derived stock/acceptance. Current item regional factors must match the port set exactly. New ports require coherent item/merchant data, not only map labels. |
| `pirates/economy.py:190-220` | Harbor terrain and merchant fronts, with shared ship/pier boundary. Existing non-Verdant branch is Cinder-like; a new island needs deliberate terrain/style rather than accidental fallback. |
| `pirates/economy.py:232-277,369-469` | Mutable finite markets, stock updates, pricing, quotes and transactions. Preserve stock/purse ownership and disclosed porter fees/time. |
| `pirates/economy.py:709-756` | Harbor memory, legacy paid coastal passage and snapshot. Legacy passage bypass must not secretly replace canonical route travel in the MVP. |
| `pirates/voyage.py:17-30,145,270` | Existing nodes/links, pathfinding, travel confirmation and officer choices. Only two nodes currently bind to trade ports. |
| `pirates/voyage.py:63-112` | Officer reports contain Verdant/Cinder destination assumptions. Audit all binary-port assumptions before a third port. |
| `pirates/voyage.py:885-892,1070-1087,1187-1222,1325-1334` | Voyage/fleet snapshots, five authored ships, hourly movement and permanent first-sighting behavior. Keep current encounter boundary; do not mistake local fleet motion for permission to advance strategic society. |
| `pirates/society.py:92,997,1036` | Authored identity registry, strategic advance and snapshot. Read public identity without invoking strategic evolution. Audit restore requirements before changing faction IDs. |
| `pirates/management.py:582,860,1081,1396` | Chart rows, voyage menu and arrival-report UI. Shared live row helpers should also drive previews. |
| `pirates/crew_recruitment.py:8-58` | Finite authored candidates with stable origin ports and an optional Cinder undertaking. Do not move existing promises or regenerate candidates on arrival. |

Existing test families: `test_foundation`, `test_economy`, `test_trade_expansion`, `test_harbor_navigation`, `test_voyage`, `test_voyage_save`, `test_sea_encounters`, recruitment/employment tests and `test_play_inspection`. Read exact test paths/selectors before invoking them.

These anchors are a source audit, not a new passing-test claim. Line numbers may move under concurrent work; search symbols before editing.

## 4. First implementation step: authority and dependency inventory

Before changing behavior, record a compact implementation ledger in this document:

1. All fresh/resume/save paths, including hotkeys/autosave/exit flows and the actual default save filename.
2. All imports and mutable users of `PORTS`, `MERCHANTS`, `CATALOG`, `SEA_NODES`, `LANES`, authored society/fleet IDs and recruitment ports.
3. Binary destination assumptions, hardcoded port lists, market validation and historical save expectations.
4. Which local NPC activity calls currently mutate crew headcounts, captives, ships or organizations; explicitly separate movement-only work from those activities.
5. Existing arrival interrupts/modals, legacy coastal passage shortcuts and contract-office entry points.

Do not merely add a mode flag around `new_generated` and call the system isolated. Isolation must cover per-second updates and restore as well as construction.

## 5. Recommended data/authority shape

Use one validated scenario definition and existing runtime authorities. Suggested new names below are proposed files, not pre-existing APIs:

- `content/scenarios/lawful_trader_mvp.json`: stable scenario ID/version, port metadata, node/edge definitions, initial roster references and public guides.
- `pirates/scenario.py`: load/validate definitions, resolve read-only topology/identity metadata, instantiate fresh state and dispatch supported scenario versions.
- Small adapters at current construction/lookup sites; existing economy owns markets, voyage owns player passage, fleet owns local vessel positions, ShipLife owns crew/work, society supplies static identity display.

Avoid a provider framework with speculative plugins. A minimal typed definition/lookup layer is enough. Authoring data must not also become a second mutable market, cargo or faction authority.

### Runtime ownership

| Data | Owner |
| --- | --- |
| Scenario identity/version and immutable definitions | Scenario layer |
| Player money, merchant stock/purses, cargo, gear | Existing `Economy` |
| Clock, ship geometry/fixtures/needs/jobs, wages | Existing world/ship/employment authorities |
| Player location, route and encounter pause | Existing `VoyageSystem` |
| Local sample-vessel route progress and contact state | Existing fleet/encounter authorities, mode-scoped policy |
| Static names, faction descriptions, public associations | Scenario-backed registry, exposed through existing display interface |
| Harbor exploration/doors/ground items | Existing per-port harbor memory |
| Player knowledge/reports | Existing sighting history where supported; small versioned visited/known records if needed |

### Multi-mode safety

Prefer world-scoped scenario lookups over changing module globals at runtime. Old modules import constants by reference; rebinding `economy.PORTS` alone will leave stale references elsewhere. The same process must be able to construct/load an MVP world and a legacy/generated world without cross-contamination.

If static shared port definitions are extended instead, document how old strict market/catalog/save validation is preserved. Do not insert new markets into every legacy save as an undocumented migration. Pick the smallest safe approach after the inventory in §4.

## 6. Scenario content specification

### Three-port minimum

Retain Verdant Cay (agricultural/provisions/timber) and Cinder Quay (metal/tools/outfitting). Recommended third port: promote existing `freeport` to a real **lawful commercial anchorage**, preserving its chart identity/location where compatible. Its public text must not imply that trading there automatically makes the captain criminal. Final name/merchant identities are authored content choices; do not silently rewrite historical legacy faction records.

Give the third port a concrete economic distinction using supported catalog items—for example, an exchange/outfitting center with a different import mix and services—rather than uniformly better prices for everything. New fictional production claims must be labelled authored supply, not simulated manufacture.

Each port needs:

- Stable ID, name, chart-node link, description, harbor style and available services.
- At least one reachable named merchant with finite goods and purse, valid buyers and correct regional factors for every offered item.
- A short public trade/route guide, not a live remote-price oracle.
- Safe landing/boarding access and a reachable return to helm/ship.
- Saved exploration, doors and ground items independent from the other ports.
- No fake repair/bounty/shipyard service button that cannot complete its action. Existing shop materials/tools can support self-repair without inventing paid instant repairs.

Use at least two plausible trade circuits in the acceptance run. Do not force every journey through Cinder, guarantee profit or exhaust starting cash through a required quest. Market prices/stocks/fees are measured initial data, not a newly promised economy model.

### Chart and routes

Reuse the current graph and add only necessary edges/metadata. Every functioning port must be reachable, with at least one destination offering two meaningful alternative paths. Distinguish known distance/time/exposure/shelter information from conjecture. Preserve travel to supported non-port nodes; do not call all six current nodes islands with markets.

Validate edge endpoints, no self-loops/duplicate edges, positive finite distance/costs and connectivity. Graph queries must not mutate discoveries, time or stock. Actual passage uses the same topology shown by the UI.

### Sample people and factions

Retain compatible existing named contacts/vessels and add only enough to support ports. Stable IDs connect display, reports and fleet membership; a string colour/label is not a legal owner or alliance.

Three behavior families already exist: merchant, marines and pirates. Fourth-role design is deferred; do not manufacture a privateering system for this phase. Sample vessel inventories, crews and money persist in saves. No respawn-on-arrival, prehistory or faction expansion. Existing population/headcount validators must still pass without arbitrary constant changes.

Authored-fleet caveat from source audit: the current initial roster starts with one captive aboard `raider_1` and five people in `NpcFleetSystem.market`; `_activity` can raid, sell/buy captives and trade cargo at `freeport`. That is legacy authored behavior, not part of the lawful MVP. In the MVP-only factory initialize no captive/enslaved-market population, preserve total crew headcount, and make the mode-specific restore validator enforce that baseline. Keep legacy authored saves/behavior unchanged. In this mode, destination activity is suppressed altogether; only bounded route movement/sightings remain. The live chart must not display the captive-market ledger.

## 7. Launch, save selection and compatibility contract

### Proposed player-facing commands

Implement and test an explicit command such as:

```powershell
# Proposed after implementation; not an existing CLI promise
python play.py --scenario lawful-trader-mvp --new
python play.py --scenario lawful-trader-mvp
```

Retain legacy generated launch behavior unless a deliberate default change is approved. New explicit scenario mode should not accept generated population/history arguments; fail clearly on conflicting options. Show “Authored MVP — sample world; world simulation disconnected” in the new-game/help/status UI without a repeated disruptive popup.

### Save rules

- Separate MVP save path/slot from the existing default save; derive every save/load action from the selected session slot, not only the initial load.
- Persist scenario ID, content/schema version and mode. Select and validate the scenario before reconstructing markets/ports/terrain.
- No metadata means a legacy world, not automatically an MVP world. Preserve existing version 1/2/3 migrations and generated manifests.
- Unsupported scenario/version, malformed IDs, contradictory manifest/mode or wrong-slot content must fail before modifying the current world or save.
- Changing shipped definitions must not silently refill markets/reset people. Either support the old definition version or supply an explicit tested migration.
- Unknown/new `--new` slot behavior must not overwrite the old save until explicit user confirmation or a documented separate new slot. Do not autosave a replacement after a failed load.
- Keep atomic temporary write/replace. Save/load must preserve money, inventories, crew/work, travel/encounter state, contacts and harbor memory.

Freeze supported authored versions for this slice. Do not import `sim/` snapshots or migrate generated saves into the sample scenario as a shortcut.

## 8. What advances—and what must not

Allowed in authored MVP mode:

- Canonical clock, needs, consumption, work/repairs/construction, wages and disclosed optional commitments.
- Existing finite market daily restock/consumption rules, documented as bounded authored market behavior, not world simulation.
- Local vessel movement on authored routes, existing observation/contact timing and encounter reservation.
- Existing player-caused trades/aid and already-supported effects, each through its current owner.

Disallowed:

- Generated prehistory, historical news creation, strategic faction battles/recruitment/expansion, autonomous slave/prisoner trade or other off-camera society development.
- Headless `sim/` calls, regenerated rosters or changing affinities because time was accelerated.
- Freezing crew/work/markets to avoid implementing a proper society boundary.

Audit `NpcFleetSystem.advance_second` carefully: movement currently reaches `_activity` at destinations. A check only around `society.advance` is insufficient if `_activity` performs excluded strategic/economic/captive changes. Extract/reuse the smallest mode-specific local travel policy, without rewriting generated mode.

At the same canonical ticks, bulk and stepped updates must agree; do not discard elapsed hourly/day boundaries. Read-only map/guide inspection advances nothing. RNG, where retained, is scenario-seeded and saved; opening a menu must not reroll traffic.

## 9. Map, port and arrival UX

### Map

Display current location/leg, known ports, selectable destination, route alternatives, disclosed estimates and the selected route. Show sample names/colours as public registry facts, not live exact crew/cargo/strength. Known chart topology can be authored public knowledge; current vessel positions/remote prices cannot be omniscient unless explicitly observed.

Use the existing renderer and chart/row helpers. Make third-port navigation work from the actual keyboard/mouse path, not just a direct `confirm()` test. Queries/cancel are free; selecting a destination must not teleport or pay a legacy passage fee unexpectedly.

### Ports and guides

Keep walkable compact harbors; no land-content expansion. Reach merchants, make a finite purchase, allocate where supported, return aboard and sail. Guides explain locally known goods, fees/services and route character. Distant price history needs source/time; exact remote stock is not free public knowledge.

### Travel without official missions

No required contract office, “accept voyage” mission, formal profit grade or mandatory arrival-report dismissal before returning control. Keep a navigational destination confirmation and safety warnings. Existing internal voyage records/snapshots remain useful; present optional histories rather than redesigning their storage solely for terminology.

Hide new delivery offers in the MVP-facing UI without deleting legacy contracts or bypassing existing wage/undertaking consequences. Existing optional commitments can be inspected; they do not veto travel. Leave generated/legacy UI behavior compatible.

Disable/remove the legacy paid instant coastal-passage shortcut from this mode's normal route UI, or explicitly redirect it through canonical travel. There must be no instant-transfer bypass of distance, supplies or encounters. Do not remove the underlying legacy function if other modes/tests use it.

## 10. Implementation sequence

Stages 1–6 are implemented for the authored foundation; stage 7 has the scoped evidence recorded in §13. Re-run the relevant gates after later changes.

1. **Baseline and inventory:** git status, exact CLI/save paths/constants/validators, focused baseline tests, runtime hook diagram. Preserve parallel changes.
2. **Scenario definition and validation:** immutable data/lookup layer, existing authored seed represented without runtime mutation; reject bad references and test multiple modes in one process.
3. **Construction and update isolation:** explicit MVP world creation, static registry, local-route-only policy; generated/prehistory/strategic callbacks patched to raise in tests while real ship time progresses.
4. **Safe save/load and launcher:** mode-aware save envelope/restore order and slot dispatch; legacy fixtures and error paths before authoring a new port.
5. **Third working port:** coherent catalog/merchant/harbor/graph data, buyer/funds rules, safe arrival and harbor memory. Audit all binary-port assumptions.
6. **Chart/free-travel UI:** live destination/route selection, guides, optional histories; exclude contract funnel/legacy instant passage only in MVP mode.
7. **Integrated validation:** three-port actual route/trade/reload run, isolation/time/conservation negatives, native captures, performance observations and honest suite report.

Do not mark any stage complete because a JSON manifest parses or a screenshot exists. Each exit requires the runtime behavior and relevant unhappy paths.

## 11. Test matrix

| Gate | Required proof |
| --- | --- |
| Authored creation | Normal CLI factory produces expected scenario; patch generation/prehistory/strategic update entry points to raise and demonstrate they are never called. |
| Update isolation | Advance beyond hourly/day boundaries; clock, actual work/provisions/payroll/market updates run; static factions/relationships/history/roster do not evolve. Local vessel movement still occurs. |
| Import independence | Authored startup/restore/advance do not require importing/calling `sim/`; test missing/poisoned simulation modules where feasible. |
| Graph | All three ports reachable; alternative paths differ; selected route and actual route match; unknown IDs/malformed graph fail. |
| Economy | New merchants have valid finite stock/purses, price quotes and buyers; insufficient money/stock/capacity rejects without partial mutation. No new-contract requirement. |
| Harbor | Each landing/stall/helm path reachable; boarding hull/fixtures unchanged by port rebuilding; exploration/doors/dropped goods persist only at their proper port. |
| Save round-trip | New port, underway route, pending encounter and day-boundary state survive with same identities/stock/money/work. Failed load leaves source save/current world intact. |
| Version/mode | Legacy v1/v2/v3 and generated fixtures remain compatible; wrong metadata/version rejected; mixed-mode worlds in one process do not share mutable markets/topology. |
| Slot protection | New/resume/manual-save/autosave/exit-save all use the selected slot; no overwrite of legacy save; failed new/load does not create a fresh replacement silently. |
| Time equivalence | Same canonical horizon bulk versus stepped has equivalent meaningful state with controlled encounter interruptions; records do not depend on opening map/report menus. |
| Free travel | No mandatory contract, arrival grade or paid instant passage; voluntary commitment consequences still run without locking routes. |
| Public information | Third-port guides and map do not leak live remote stock, mission, exact hidden vessel state or strategic history that was never generated. |
| Actual launcher | Script the normal scenario selection/resume path, not only smoke-render `World()`; runtime header identifies the authored sample mode. |

Suggested new tests: `tests/test_authored_scenario.py`, `tests/test_authored_scenario_save.py`, `tests/test_authored_archipelago.py`. Names are recommendations; follow current repository conventions. Add regressions beside existing owners rather than duplicating all their tests.

### Verification commands

Working directory: `E:/Pirates of Miska`. Use verified existing files for baseline:

```powershell
python -B -m pytest -q tests/test_economy.py tests/test_trade_expansion.py tests/test_harbor_navigation.py tests/test_voyage.py tests/test_voyage_save.py tests/test_sea_encounters.py tests/test_foundation.py
git diff --check
```

After implementation add the new scenario tests, relevant employment/inspection tests, scoped compileall and a direct whitespace scan for untracked files. Use exact files; PowerShell does not expand pytest wildcards. No passing counts are claimed here.

Create a save-free native harness using real row/action helpers. Capture scenario identity, chart/route choice, each port, local market, third-port departure, arrival without forced settlement and resume at 1280×800, 960×700 and 640×600. Inspect text/clipping/background bleed. Then perform a launch→buy→sail→sell→third-port→reload→sail-again walkthrough. Scripted screenshots are not human playtest acceptance.

## 12. Stop conditions and dependency handling

- If constant/validator coupling makes mode-scoped ports unsafe, document exact callers and make a narrow lookup refactor before port data expansion. Do not mutate globals between tests.
- If society/fleet restore requires canonical identities, preserve those IDs for the first scenario or introduce an explicit schema adapter; do not weaken validation to accept arbitrary state.
- If separate combat code changes concurrently, preserve its delta and re-check shared world/save hooks. This phase must not fabricate combat results.
- Encounter requests may still stop at the known pending boundary until M5; document it. Completing this slice does not claim the full MVP is release-ready.
- Do not let unresolved fourth ship role, prize law or combat internals block authored ports. Those are later-phase questions, not prerequisites for a static map/free-trade foundation.
- Ask for user direction only for a genuine incompatible design choice (destructive save policy/default-mode change, conflicting geography or unsupported service promise); choose minimal compatible implementation details within this plan.

## 13. Agent completion ledger — fill with real evidence

| Stage | State | Evidence to record |
| --- | --- | --- |
| Baseline/authority inventory | Complete | Baseline: `python -B -m pytest -q tests/test_economy.py tests/test_trade_expansion.py tests/test_harbor_navigation.py tests/test_voyage.py tests/test_voyage_save.py tests/test_sea_encounters.py tests/test_foundation.py` → 155 passed in 85.89s. Initial `git status --short` recorded concurrent combat/sim/report/plan modifications and the two untracked plan files; no gameplay changes made. Confirmed default slot `saves/captain.json`; `play.py:1480-1508,1791-1802,1853-1862` has generated fresh launch plus save/quit/manual save/load entry points. Port/catalog authority and strict validators are in `pirates/economy.py:19-166,231-255,746-911`; canonical chart/travel and hard-coded officer suggestions in `pirates/voyage.py:17-27,63-132,145-190,270-289,457-472`; per-hour destination activity in `:1187-1300`; strategic authored society behavior in `pirates/society.py:975-1035`; live map/arrival panels in `pirates/management.py:582-661,860-1085,1396`; port-guide/instant-passage/contract funnel in `pirates/trading_ui.py:254-361`. Critical legacy behavior: initial authored roster has captive ledgers and authored `_activity` may transfer/enslave people at Freeport; MVP must explicitly isolate those actions and preserve legacy behavior. |
| Scenario/schema validation | Complete | Added `content/scenarios/lawful-trader-mvp.json` and `pirates/scenario.py`; strict supported ID/version, unique ports, entry port, false world-simulation policies, defensive return copies and optional port/chart reference checks. `tests/test_authored_scenario.py` schema cases pass. Port references are wired to actual registries after the third port lands. |
| Local update isolation | Complete for pre-port authored slice | `World.new_authored_mvp()` constructs static sample state without `new_generated`/prehistory; zeroes captive ledgers while retaining the five named crews and count 34. `World.on_ship_second` skips `NpcSocietySystem.advance` but keeps its clock aligned; `NpcFleetSystem.advance_second` retains lane movement while suppressing all `_activity`. Tests patch the generated factory, destination activity and society advance to raise; assert local movement, static relationships/news/cargo/population, ordinary clock and finite-market updates, multiple mode instances, and no `sim` import. `tests/test_authored_scenario.py`: 12 passed; generated NPC/society/predation/decision regression suites: 50 passed. Port-provider validation and final launch integration remain later gates. |
| Save/CLI | Complete for scenario/slot foundation | `python play.py --scenario lawful-trader-mvp` resumes its separate `saves/lawful-trader-mvp.json` slot or creates the first sample; `--new --scenario lawful-trader-mvp` refuses an existing slot unless explicit `--replace-slot`. Ordinary/default generated launch remains on `saves/captain.json`. Manual save, quit, combat-drill exit and F9 use the selected slot; F9 checks the current scenario identity. New saves use root version 4 with optional exact scenario ID/version metadata; versions 1–3 remain accepted. Missing/invalid/cross-mode scenario metadata fails before mutation. Focused authored/foundation/voyage-save/generated-NPC suites: 42 passed; `python -B play.py --help` shows both options. Economy port-set migration for the upcoming merchant remains pending. |
| Ports/markets/routes | Complete | Added **Freeport Exchange** to the existing `freeport` chart node, including one reachable finite merchant, sea-glass/chart-ink/salted-fruit regional goods, Nella's food buyer roster, three-factor catalog validation, distinct plaza map, and backward-compatible legacy market/harbor restoration. Authored tests cover all-port merchant reachability; lane and finite-coordinate integrity; connectivity of all three ports; both equal-distance Verdant–Cinder routes; legacy v3 market/harbor restoration; and finite trade/purse conservation. |
| Live UI/travel | Complete for authored free-travel slice | Actual `voyage_menu` tests select Freeport at no fare and select/commit the second Verdant–Cinder route unchanged. Destination rows exclude non-ports in this mode. `port_guide_rows` names all three harbors and local merchants, says quotes are local (not a remote live feed), and explains free chart travel; authored port menu omits contracts and paid passage. Arrival returns to control; the report states no arrival settlement is required, and the integrated trade test sells before reloading/sailing again. Legacy instant `Economy.travel` is rejected for authored worlds while legacy UI remains. `tools/capture_authored_archipelago_ui.py` captured 36 native SDL frames (12 each at 1280×800, 960×700, 640×600), including scenario status, guides, every harbor/local market, route choice, departure, arrival and resume. Representative compact/regular/wide frames were visually inspected. |
| Final checks | Scoped gates passed | `python -B -m pytest -q tests/test_economy.py tests/test_trade_expansion.py tests/test_harbor_navigation.py tests/test_voyage.py tests/test_voyage_save.py tests/test_sea_encounters.py tests/test_foundation.py tests/test_authored_scenario.py` → **177 passed in 91.58s**. `python -B -m py_compile` on the touched authored/runtime/test/capture files passed; `git diff --check` reported no whitespace errors (only repository LF→CRLF advisories). `python -B play.py --help` shows explicit scenario/new/replace-slot options; `python -B play.py --scenario lawful-trader-mvp --smoke-render` passed native SDL movement/modal smoke. No full-repository suite or unaided player test is claimed. |

Scope boundary: this completes the authored ports/chart/free-trading foundation, not the combat-inclusive MVP. New encounter requests still lack their integrated combat consumer/result acknowledgement, and the broader lawful prisoner/bounty/prize, reputation-unlock and treasure loops remain unfinished. Preserve the separately developed combat/simulation changes; do not treat this foundation's tests or scripted captures as release or unaided-playtest acceptance.
