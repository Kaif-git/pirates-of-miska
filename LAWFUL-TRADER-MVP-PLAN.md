# Lawful Trader in Pirate Waters — MVP Plan and Current-State Audit

Date: 2026-11-20
Status: the authored foundation, M2 ship preparation and M3 free-trade information loop are scoped-verified; the combat-inclusive MVP described here remains incomplete.
Parent: `PIRATES-OF-MISKA-MASTER-PLAN.md`; this plan takes priority for the next playable MVP. Other master-plan expansions are deferred as listed below.

## Current implementation status — 2026-11-20

The first authored-world foundation is implemented under `AUTHORED-ARCHIPELAGO-IMPLEMENTATION-PLAN.md`: explicit `--scenario lawful-trader-mvp` launch/resume in a protected `saves/lawful-trader-mvp.json` slot; versioned scenario/save metadata with legacy v1–v3 compatibility; three functioning ports (Verdant Cay, Cinder Quay and Freeport Exchange); finite local trade; selectable canonical port-to-port sailing with route alternatives and no fare; and no generated prehistory/strategic-society advancement in this mode. The focused economy/trade/harbor/voyage/save/encounter/foundation/authored suites passed **177 tests in 91.58s**. Native SDL evidence: `reports/authored-archipelago/ui/` contains 36 frames, 12 each at 1280×800, 960×700 and 640×600; capture harness: `tools/capture_authored_archipelago_ui.py`.

This is not the complete MVP in §1–§5. Combat handoff is not integrated into a playable combat/result loop, and lawful custody/bounties, captured-ship recovery, authored reputation unlocks and persistent treasure recovery remain unfinished. Scripted tests/captures are not unaided player acceptance; see the implementation plan's evidence ledger for exact scope and verification.

## M2 ship-preparation status — 2026-11-19

The scoped M2 preparation loop is implemented and verified in `SHIP-PREPARATION-IMPLEMENTATION-PLAN.md`: existing construction, spatial stock/hauling, labor/equipment/officers, employment and canonical readiness remain the authorities. The focused ship/preparation/economy/voyage/crew/employment set passed **457 tests in 162.74s**. The authored walkthrough buys and allocates finite goods, physically builds a connected deck extension, turns that geometry into extra displacement and a 750 kg stockpile, saves/reloads during construction and a physical transfer, preserves conserved stock, and sails after fixing potable water while accepting a hull warning. Its native SDL harness produced **33 captures** at 1280×800, 960×700 and 640×600; captures and `verification.json` are under `C:/Users/Warp/AppData/Local/Temp/opencode/ship-preparation-m2`.

This is scripted integration and presentation evidence, not unaided player acceptance. It does not complete M3–M8 or the combat-inclusive MVP: campaign combat/result integration, lawful custody/bounties/prizes, reputation unlocks, treasure/salvage and broader human release acceptance remain pending.

## M3 free-trade status — 2026-11-20

`FREE-TRADE-IMPLEMENTATION-PLAN.md` records scoped-verified P0–P4. The authored UI distinguishes locally observed, remembered/non-live and unknown commercial information; previews quantity-sensitive cost/proceeds, fees, cash/purse and cargo-load changes; filters reserved cargo from sales; keeps cancellation and failure atomic; and allows selling before the optional arrival report and sailing again. Three different two-leg circuits, each repeated twice under a documented quiet-passage control, remain cash-positive after conservative opening-quote resupply baskets (+682/+803/+145 Marks for the two-loop runs); this is a narrow scripted sample, not long-run solvency or player acceptance. The expanded M3 regression set passed **253 tests in 147.86s**. `python -u -B tools/capture_free_trade_m3_ui.py` captured 60 scripted UI states / 180 SDL screenshots at 1280×800, 960×700 and 640×600 under `C:/Users/Warp/AppData/Local/Temp/opencode/free-trade-m3-ui-verified/`; the voyage duration is shortened for presentation, so this is not a normal-duration trip or human test. M3 is scoped-complete; M4–M8, human acceptance and the combat-inclusive MVP remain unfinished.

## M4 sea-traffic status — 2026-11-20

`M4-SEA-TRAFFIC-IMPLEMENTATION-PLAN.md` records completed P0–P6 M4 implementation and scripted evidence. Shipped scope includes bounded hourly chart traffic/repeat contacts; encounter-local signal/parley and visible-fact pirate readiness; fail-closed lawful hostility and neutral-marine messaging; a six-person consented physical NPC-deck transfer; and finite explicit at-sea offers with save-safe exactly-once receipts. At-sea trade is restricted to disclosed grain/beans/iron terms at fixed catalog-based ask/bid prices; it does not query remote port markets or change M3 `Economy.trade`. The expanded M4/NPC/authored/save/economy/harbor and M1–M2 guard set passed **367 tests in 143.52s**. `tools/preview_m4_interactions.py` passed **8 native screens × 3 sizes** (1280×800/960×700/640×600); reviewed representative compact/medium/wide frames. The scripted authored walkthrough acquires a merchant via the lookout authority, saves/reloads a visitor on the NPC deck, returns and resumes sailing; it is not unaided player acceptance. Keep the §16 real-player acceptance run open; M5 campaign-combat outcomes and the combat-inclusive MVP remain unfinished.

## 1. The game this MVP must deliver

Run a modest trading ship in a dangerous, deliberately authored archipelago. Choose what to buy, where to go and where to sell. Build and arrange the ship, hire and equip people, assign their work, gather useful information, and prepare before accelerating ordinary passage. At sea, encounter independently moving traders, marines and pirates; communicate, approach, trade, assist, negotiate, evade or fight. Defend lawfully, accept surrender, take eligible pirate prizes, recover treasure and return to port with things worth keeping or selling.

Core loop:

**Learn → buy and outfit → build/organize/assign → sail freely → play sea situations → trade/repair/recruit/reinvest.**

- No delivery-contract-driven progression. Buying and selling are the player's own decisions.
- Sailing is travel, not a formal expedition that must be accepted, completed, graded or settled. An internal travel record is fine; mandatory voyage profit/loss/obligation screens are not.
- Optional quests and explicit crew commitments may provide motives, but must not seize route control or lock unrelated actions. Their disclosed consequences remain real.
- The intended role is a lawful trader with the ability to fight back and claim pirate ships/loot, not a supported criminal career in this release.
- Preparation and combat both matter. A combat-free merchant demo does not satisfy this MVP.
- Quiet ship life runs automatically. Routine hunger, boredom, learning and small grievances must not become constant player interruptions.
- No mandatory victory screen, fixed wealth quota or arbitrary final boss. Success is a sustainable, replayable sandbox with visible growth.

## 2. Rewards and reasons to sail

### 2.1 Prosperity and ship identity

Earn money through self-directed trade, lawful prizes, recognized bounties and salvage. Spend it on useful cargo capacity, construction, better tools/equipment/fittings, a sounder or larger ship, more capable crew and financial reserves. A ship's arrangement, maintenance and accumulated story should matter; growth is not just buying a larger inventory number.

### 2.2 Capability and crew development

Combat improves the captain and relevant crew through the separately maintained progression authority. Noncombat work must likewise improve practical capability, not merely reduce complaint frequency. Actual work earns quiet progress; no daily reward collection or infinite idle-practice exploit.

### 2.3 Reputation that changes options

Commercial standing, recognized victories and treatment of surrendered people should open concrete authored opportunities: better-stock merchants, wholesale access, useful reports, experienced recruits, introductions and verified bounty information. Stronger reputation/fear can deter small pirates. It must not be an invisible universal approval score.

### 2.4 Attractive prey, dangerous opponent

A larger, apparently cargo-rich vessel attracts attention; a record of defeating pirates makes attacking it risky. Pirates compare perceived prize and observed/reported capability, not exact hidden cargo or future outcomes. Small pirates can back down; stronger pirates can take interest. Do not automatically replace every avoided weak pirate with a stronger attack—safer passages are part of the reward.

### 2.5 Surprise and acquisition

Rare fittings, equipment, useful charts, floating treasure, salvageable abandoned vessels and captured pirate hulls provide memorable gains beyond cash. No swimming, diving or land adventure is required.

## 3. Scope firewall: pause other master-plan features

These are deferred from this MVP, not deleted or declared obsolete:

| Master-plan area | MVP boundary |
| --- | --- |
| §4 persistent world; background faction/NPC growth | Do not attach `sim/`, prehistory, off-camera lore generation, autonomous wars, population/affinity simulation or evolving political economy. Use saved authored sample identities and bounded local encounter behavior. |
| §5 political relations | Only local lawful conduct, named contacts, bounty authority and explicit reputation unlocks. No treaties, wars, privateering career, global diplomacy or coalition mechanics. |
| §6 expanded agreements | Reuse wages/recruitment and optional explicit commitments. No profit shares, unrestricted personal shopping or elaborate contract negotiation. Delivery contracts are excluded from the MVP-facing loop. |
| §7 broad companion autonomy | A bounded encounter party and station/work assignments only. No universal planner, off-camera adventures or independent expeditions. |
| §9 broad encounter families | Ship encounters, bounded weather and ship-accessible salvage only. No monsters, broad exploration campaign or procedural lore discoveries. |
| §10 defeated-enemy outcomes | Surrender, secure custody, release and verified bounty delivery; lawful loot/prizes. No slavery/forced labor, torture, humiliation, prisoner recruitment or ransom network. |
| §11 magic | All Fusion/Floren acquisition, mastery and power hunting paused. |
| §12 fleets/delegation | Ship replacement and bounded prize recovery are included; independent fleet missions, remote command and automated fleet trading are paused. |
| §13 later expeditions | No swimming/diving, excavation, island dungeons, large land content, fishing chores or production-chain expansion. Existing harbor walking/shops remain. |
| New systems generally | No work merely to increase feature count. Every addition must serve preparation, free trade, sea action or a tangible reward. |

This is a scheduling boundary for this MVP, not an instruction to stop another session/process or discard concurrent combat/simulation work. Existing modules and legacy saves must remain intact. Separate combat development continues; integration is an explicit dependency, not permission to rewrite that work.

## 4. Audit method and evidence limits

This is a source audit of the current worktree, not a fresh runtime certification. References below identify actual code, not proof of a complete playable feature. Tests listed are existing coverage to reuse; none was rerun for this planning task. The prior encounter slice recorded 80 focused passing tests and native captures; those historical results do not certify this larger MVP.

The previous broad repository run ended without a final summary after a server restart; failure markers were present. Do not call the repository green or assume those failures belong to this MVP without reproduction. Concurrent changes exist in combat and simulation and must be preserved.

## 5. What exists, what is partial, what is missing

| Area | Verified foundation | Remaining MVP gap |
| --- | --- | --- |
| New game | `pirates/game.py:49-113` creates an authored starter world; `:116-129` creates generated society/prehistory. | `play.py:1478-1508` normally launches generated worlds, while authored `World()` is used by smoke mode. Need an explicit durable authored-MVP mode, separate save identity and no prehistory/simulation dependency. |
| World map | `pirates/voyage.py:17-27` has six chart nodes and nine links. | Only Verdant and Cinder are ports (`pirates/economy.py:27-32`). Freeport is currently a chart node, not a complete market harbor. Need a few functioning differentiated ports, usable map UI and route/knowledge choices. |
| Free trade | `economy.py:232-277,369-469` owns finite merchant stocks/funds, regional/scarcity pricing, daily stock changes, cargo and paid porter delivery. Catalog/rules are JSON. | Expand authored ports coherently; test sustained speculative trade without contracts, merchant liquidity and waiting exploits. Current fixed trade mass/bulk caps (`:58-59`) need reconciliation with physical ship upgrades. |
| Contracts/reputation | `economy.py:286-366` implements delivery contracts; `:246,362-363` has contract-earned trader reputation. | Do not use contracts as the new loop or delete their old-save data. Non-contract trade reputation and useful unlocks are not supplied by that delivery reward path. |
| Recruitment/wages | `economy.py:409-436`, `crew_recruitment.py`, `employment.py` and their tests provide candidates, atomic hire, wages/arrears and commitments. | Tune authored recruitment availability, affordability and later recruit unlocks; verify practical berth/cap limits and no mandatory commitment funnel. |
| Ship construction | `ship_building.py:13,122,211,250,396` provides previews, access checks, queued construction/blueprints and completion. `ship_model.py`, `ship_blueprint.py`, fixtures/relocation/spaces/navigation provide geometry and rooms. | Make the real player workflow clear and reliable; prove a materially different fit-out and larger ship can be built/obtained. Room names alone must not grant unexplained bonuses. |
| Storage/logistics | `ship_storage.py:136-277,326-427` provides physical piles, capacity/reservations and hauled transfer jobs; stock placement feeds mass calculations. | Trade cargo is a separate economy inventory; do not assume all merchandise already occupies accessible physical piles. Design one ownership/placement bridge, usable filters/priorities and blocked-access explanations. |
| Loading/seaworthiness | `ship_physics.py:130-206` estimates displacement, load distribution, freeboard and stability; `:210` sailing forces. Hazards/support modules exist. | These are provisional low-order models, not validated naval physics. Reconcile cargo, structure, fixtures, people and supplies; tune predictable warnings/effects and ship-size growth. |
| Assigning crew | `ship.py:1568-1709,1789-1893` claims/assigns jobs, sets labor priorities and executes actual movement/work. `voyage.py:28-30,63` has officer/free-time orders. | A legible pre-sailing staffing view; coverage warnings, bounded priorities and no single worker trapped in competing essential jobs. Do not introduce a second scheduler. |
| Noncombat learning | `ship.py:1763-1764,1773-1787,1870-1883` grows labor skills/XP, improves work pace and supports nearby actual-work mentorship. | Not all discussed specialties have real effects. Navigation estimates, cargo protection, appraisal, salvage, medicine and unlock descriptions need explicit audited capability bindings. |
| Provisions/care | `provisioning.py:12,132` owns forecasts/readiness; `ship.py` includes galley policies, water containers, food spoilage, rain treatment, bandages and infirmary access. | Calibrate ordinary upkeep for accelerated play. Show ready versus cookable food, fuel/tools, potable water and access; avoid routine interrupt spam. Medical bridge from new combat remains required. |
| Equipment | `economy.py:506-596,646` allocates supplies and equips/mends gear with labor bonuses. | Audit captain versus crew weapon/armor/ammo ownership and combat import/export. No duplicated free kit when entering/leaving combat. |
| Travel/time | `VoyageSystem` owns route progression, storms, interruption and arrival; `game.py:251-272` uses canonical world time and payroll. | Keep internal voyage records but remove mandatory mission framing, contract office funnel and formal arrival settlement. Cancel/redirect/anchor behavior needs explicit rules and tests. |
| Existing runtime NPCs | `voyage.py:1070-1087` has five authored vessels; `:1187-1222` moves them hourly. `game.py:239-245` also advances society. | Authored mode is not automatically disconnected: freeze strategic society developments while retaining local travel/encounter movement. Keep identities stable and mark sample data. |
| Encounter UI/state | `sea_encounter.py:84,117,272,310,357,394,445,481,560` provides reports, local range, signals, approach, evasion, parley, manifests and requests. | No real relative course/bearing/separation. Deterministic coarse transitions are not a complete crossing/overtaking/pursuit model. No completed at-sea merchant transaction or other-ship deck transfer. |
| Recurring contacts | `voyage.py:1208-1222` records sightings but returns only the first sighting per vessel. | Permanent `sighted` suppression cannot support a continuing sandbox. Need distinct encounter-instance IDs, cooldown/lost-contact rules and persistence without reroll farming. |
| Pirate response/legal standing | Existing threat categories, organization reputation and bounded NPC behavior exist. | No verified captain-specific lawful hostility proof, wealth-attraction/fear-deterrence progression or complete marine response/unlock loop. Labels are not legal evidence or actual player reputation. |
| Combat | `combat.py:201` supports legacy boarders on the player's ship; isolated `duel_system/` and legacy combat coexist. New encounters save `combat_pending`. | Separate combat is still being developed. No complete new-request consumer/result acknowledgement, NPC deck scene, named campaign party bridge or authoritative post-fight asset/medical settlement. No automatic legacy fallback. |
| Prisoners | `ship.py:1941-1973` validates an equipped/locked brig and captures/releases legacy boarders. | Small captive records are not a full wanted-person custody ledger; identity, care, ports, warrant eligibility and exactly-once bounty payment remain missing. |
| Captured ships | Headless simulation has its own ownership/prize mechanics. | Those are not attached player gameplay. The playable world lacks a completed multi-hull asset, transfer/prize crew/recovery/sale loop. Do not borrow simulated ownership mutations directly. |
| Treasure/salvage | Existing item inventories, work jobs, storage and encounters are reusable foundations. | No complete player-facing sea-cache/wreck assessment/recovery/persistent-depletion system was found in the audited gameplay modules. |
| Progression/rewards | Labor growth, combat progression modules, trade history and society information exist. | Need visible noncombat benefits, authored reputation unlocks, prizes, bounty and salvage rewards connected to the same save. No invented broad balance claims. |

## 6. Authored world and simulation separation

### 6.1 Proposed content budget—not yet implemented or final balancing

- Start with three or four working ports; keep current Verdant/Cinder and promote/add only enough ports for meaningful choice.
- Each port has a clear economic identity, finite merchants, services, recruits, named contacts and an identifiable authority. Harbor content is functional, not a land-adventure backlog.
- Routes distinguish distance/exposure, patrol coverage, shelter and salvage possibilities. No route guarantees profit or a fight.
- A bounded roster of named vessels/captains with authored hull, crew, inventories, public identity, route schedule and encounter policy. Captured/sunk vessels stay changed; no unexplained immediate duplicates.
- Ship behavior roles: traders, pirates, marines. The requested fourth role is unresolved; do not invent an entire privateer faction to fill a count. A second trader archetype (cautious convoy merchant versus independent trader) is a possible low-cost option, to confirm before authoring.

### 6.2 Ownership rules

- Save a scenario ID/version and stable IDs for ports, ships, hulls, captains, crew, factions, items, reports and encounters.
- Static sample faction identity does not imply strategic simulation. Local affinity/contact changes can be direct responses to witnessed player actions with bounded rules; no autonomous off-camera relationships/lore.
- `sim/` remains unattached. Generated prehistory and strategic society advancement must not run in this mode. Explicitly distinguish necessary local NPC route movement, finite market updates and crew work from excluded world simulation.
- Author a narrow data-provider interface now; later simulation replacement must be an explicit migration, not two simultaneous authorities.
- Do not overwrite legacy/generated saves. A mode/version mismatch must explain itself rather than silently regenerate or auto-save over the source.

## 7. Preparation as the main noncombat play

### 7.1 Build and arrange

Use existing construction, fixtures, rooms, stairs, access checks and hauling. Support meaningful arrangements of sleeping, galley, cargo, workshop, medical and brig areas. Verify actual fixtures/resources/routes confer functionality. Construction consumes materials and worker time; queued/inaccessible work is not magically completed at departure.

### 7.2 Organize stock

Cargo, provisions, ammunition, tools and medical supplies need one clear owner/location at a time. Define designation/filter rules, priorities, capacity and reservations. A trade purchase may use disclosed porter service; rearrangement aboard uses jobs. Unloading or moving cargo cannot duplicate it across economy cargo, physical storage, carried bundles or combat loot.

### 7.3 Crew and equipment

Recruit, assign labor priorities/officer responsibilities, equip tools/weapons/armor and inspect coverage. No forced complex contracts for every sailor. An extra sailor costs wages, berth and supplies but can free a specialist or make prize recovery possible.

### 7.4 Readiness without bureaucracy

Reuse `readiness_rows`, expanded only where needed: hull/rig, load/access, food/water/fuel, essential jobs, medical capacity, crew availability and ammunition. Distinguish impossible actions from warnings. A dangerous but possible departure asks for informed acknowledgement, not arbitrary quest clearance. No manual daily collection or mandatory pre-flight clicking ritual.

### 7.5 Quiet passage

Needs, work, wages and learning advance normally. Show routine developments in inspectable logs. Interrupt for actionable danger, serious shortage, incapacitation, a requested arrival/stop or an encounter—not each meal, skill increment or minor mood change. Measure time-to-first-problem on a well-prepared ship before tuning.

## 8. Noncombat crew growth: required tangible effects

These are proposed gameplay bindings, not claims that all are implemented. Use existing skills where appropriate; do not create nine parallel XP meters before identifying actual work.

| Competence | Rewarding outcome | Limits / proof |
| --- | --- | --- |
| Navigation | More useful estimates, known route/hazard recognition, confidence in reaching markets with supply margins | Cannot reveal unexplored hidden state; estimate improvements must be tested against actual passage. |
| Seamanship/rigging | Better execution of handling and weather/escape preparation | Same ship/load physics; no invisible impossible speed bonus. |
| Cargo handling | Efficient stowage/hauling, securing fragile or awkward goods, fewer preventable losses | Cargo mass/bulk unchanged; no skill-generated hold volume. Start with one measurable fragile-cargo rule if needed. |
| Carpentry | Faster sound repairs/construction and a useful assessment of damaged/prize hulls | Finite materials, access and work time; no free hull restoration. |
| Cooking/provisioning | Reliable preparation/preservation and reduced avoidable waste | Preserve recipe mass/water accounting; do not secretly reduce human food requirements. |
| Medicine | Better triage/treatment/recovery from supported injuries | Supplies, time and anatomy limits remain; no automatic regeneration. |
| Lookout/recognition | Earlier useful warnings and better vessel/distress/wreck identification | Conditions and actual evidence constrain knowledge; no perfect private cargo read. |
| Salvage | Better assessment, lower breakage and access to recoveries supported by tools | No diving/swimming; finite sites and reachable recovery method. |
| Commerce/appraisal | Better quality/price interpretation, recognition of damaged goods, useful introductions | No guaranteed profitable omniscient buy/sell list or universal price multiplier. |

Each shipped effect needs an in-UI explanation and a controlled novice/skilled comparison. Report the difference in actual outcomes, not just XP gained. Learning happens through meaningful work and bounded instruction; cap repetitive low-risk farming. Keep ordinary crew valuable and avoid mandatory unique-person gates.

## 9. Sea movement and encounters

### 9.1 Readable course and intention

Show whether a vessel is crossing, approaching head-on, overtaking, moving away or following, with source/confidence. Use one approved bounded relative-motion model shared by observation, approach, escape and combat entry. Decide chart-level versus local distances and time scale before implementation; do not simulate a full ocean unnecessarily.

NPC behavior is a small state machine: pass/trade/seek aid/patrol/inspect/shadow/threaten/attack/disengage. Hidden intent can remain uncertain. Signals may fail or be unanswered; peaceful traders need not become surprise traps routinely.

### 9.2 Player options

- Observe, signal from afar, maintain course, change course, flee or close.
- Ask identity/intentions, trade/request terms, offer aid, negotiate separation and request permission aboard.
- Cross alone or with named available escorts, or commit everyone when physically viable; warn about leaving the home ship unmanned.
- Hostile commitment enters the separate combat authority, never resolves combat inside parley code.
- Support peaceful other-ship boarding/return as well as hostile boarding. Close range alone is not a physical crossing path or permission.

### 9.3 At-sea trade

An eligible vessel has a finite owned stock/purse. Quote quantity/price and transfer method first. Commit money, items, capacity and canonical time atomically; revalidate range/permission and available stock. Cancel before committing without cost. No use of hidden captain inventories as public offers. Save/retry must not repeat the transaction.

### 9.4 Repeat encounters

Replace permanent first-sighting suppression in this mode with encounter instances and bounded cooldown/visibility separation. One continuously visible ship is one contact, not an endless interrupt. Lost contact and later reunion are possible; old consent/attack permissions do not automatically carry into every new meeting. Stable vessel identity and history survive.

## 10. Lawful defense, reputation, fear and marine behavior

### 10.1 Conduct authority

Use one small versioned local conduct ledger. Record cause, witnesses/verified warrant, aggressor, target and encounter ID. Distinguish suspicious appearance, threat, committed attack, surrender and disengagement. Looking pirate-like or sharing a faction label alone is not enough to authorize violence.

**Decision (Nov 20, 2026; confirmed by user for M4):** block unsupported aggression. Hostile commitment requires a witnessed actual attack/explicit committed boarding or fire by the other vessel, or a separate verified warrant; appearance, faction/threat estimate, parley refusal and proximity alone are not legal evidence. UI explains lawful/prohibited/uncertain status before confirmation. The versioned M4 ledger currently records the actual NPC return shot in the preserved legacy cannon exchange; there is no verified-warrant or committed-NPC-boarding producer, so unsupported requests fail closed. New authorized requests still cross the separate M5 handoff/result boundary; an already-active legacy artillery exchange remains its own compatibility path.

This is not a criminal career release. Unprovoked attacks are blocked with an explanation. Existing criminal/wanted saves must not be rewritten into innocence.

### 10.2 Local standing and unlocks

Commercial standing grows from meaningful non-contract trade/recognized assistance; anti-pirate standing from verified eligible outcomes. Treatment history affects surrender trust. Store evidence and thresholds, not just a label. Unlocks should be visible with requirements and reasons; no farming buy/sell loops, repeated surrender payments or inflated fame from trivial repeated targets.

### 10.3 Pirate risk selection

Inputs: visible hull/armament/load clues, credible reports of the captain, authored pirate strength/temperament and finite local presence. Outputs: ignore, shadow, demand, flee or attack. More wealth can attract attention and victories can deter it simultaneously. Threat populations do not level-lock to the player; rewards include a measurable increase in backing-down outcomes by weaker pirates.

### 10.4 Marines

Mostly neutral/helpful to lawful traders, hostile to verified pirate/criminal targets. Inspection or pursuit is bounded by jurisdiction, local observations and a clear stop/lost-contact condition. No strategic war/bounty generation simulation. Friendly flag alone never overrides an actual attack record.

## 11. Combat integration boundary — critical dependency

Combat is being developed separately and may become ready later. Build the noncombat phases independently, but **do not mark the full MVP playable while combat ends at an unconsumed request**.

Required interface review with combat work:

1. Consume one stable request ID; validate live hulls, party, position/range, hostility evidence and equipment snapshot.
2. Create/resume the approved campaign encounter scene, including the other vessel's deck and physical entry/return routes. Do not silently select isolated duels or legacy boarders as a substitute.
3. Bound companion orders and map participants to real crew IDs. Off-duty/unassigned crew do not materialize as extra fighters.
4. Combat owns actions, ammunition, injury, surrender and battle outcomes. Define how time relates to canonical world time; do not allow hidden double advancement or hunger clocks running differently after load.
5. Return an authoritative result with the same request ID, participants, casualties/injuries, consumed/transferred items, target state and surrender/custody candidates.
6. Apply the result once to campaign state; save pending/active/resolved/applied stages and test reload at each boundary. No double loot, XP, death, bounty, reputation or ammunition charge.
7. Resume lawful trade/travel after nonterminal results. Captain death ends the run under the agreed policy; make warnings/retreat useful rather than arbitrary lethal rolls.

Tests must cover cancellation before commitment, interrupted save, scene restore, fleeing, surrender, mutual damage and a full returned-to-port case. A sandbox fight passing does not prove campaign integration.

## 12. Lawful loot, prisoners and captured ships

### 12.1 Pirate prizes and loot — confirmed direction

After defeating eligible hostile pirates, the player may take their cargo and claim the vessel. Use the legal encounter record and combat result; do not turn every unidentified trader's goods into lawful loot. Authored recovery ownership and valuables on abandoned wrecks need their own explicit rule.

One inventory transfer removes from source and adds to destination with capacity checks. Unique equipment, cargo lots, ship identity and remaining supplies persist. Taking a prize is a choice, not an automatic free fully repaired clone.

### 12.2 Prisoner and bounty loop

Surrender/capture → stable named identity → secure appropriate capacity → provision/treat → sail to the listed authority → surrender custody → receive payment once. Wanted notice names identity, authority, eligibility, alive/dead rule and reward. A hostile sailor without a valid notice is not automatically worth money. Capacity overflow must offer a lawful alternative such as release, not duplicate or vanish people.

Keep care bounded and automated; no daily cage clicks. Show extra supply burden. Releasing someone terminates custody and does not permit repeated bounty claims. Killing a surrendering person is not an extra lawful reward path.

### 12.3 Prize ship recovery and larger ships

Available choices: loot and leave; transfer to the better hull; assign a prize crew to accompany you to harbor; sell a recovered prize. Preserve home-ship and prize inventories separately, including people moving between them.

Require a seaworthy hull, reachable transfer, enough crew and supplies. Insufficient people/materials produce honest blockers; no remote tow or off-camera arrival unless a specifically supported tow mechanic is designed. Prize escort is bounded same-route recovery, not fleet automation. Harbor sale transfers title and settles money exactly once; a kept hull has real construction/storage geometry and fitting capacity.

Define minimum staffing, damage assessment, replacement-ship migration, boarding return, simultaneous ship control and save/load before implementing acquisition. A larger ship must actually improve usable capacity, not remain capped by the old fixed economy limits.

## 13. Treasure and salvage without swimming/land content

Start with floating cargo/wreckage, abandoned vessels and ship-reachable recovery sites. A clue/chart can reveal a location; the site persists after deferral. Conditions, tools, personnel, time and spare capacity determine what can be attempted.

Flow: sight/report → assess → approach → choose recovery scope → commit tools/work/time → transfer finite contents → leave/deplete/record unresolved remains. Include a low-risk entry example and a valuable recovery requiring preparation. Rare equipment/fittings must have supported use, or be clearly saleable—not magical feature placeholders.

- No teleporting the captain underwater, diving jobs or assumed swimming.
- Unstable vessel entry reuses deck/access safety, not a new land dungeon system.
- Salvage consumes or wears equipment only under disclosed rules; outcomes are deterministic across save/load, not rerollable by opening a menu again.
- Preserve surviving people and ownership claims where authored; treasure does not imply every wreck is ownerless.
- Depleted sites and sold unique finds stay depleted/sold. Recovery cannot be paid or looted twice.

## 14. Information, UI and freedom

One useful chart shows discovered ports/routes, current location, known sightings, leads and safe stopping options. Reports carry source, observation time and uncertainty; local prices are current quotes, distant prices are remembered/reported, not omniscient live stock.

Port guides explain costs, goods and tradeoffs without prescribing a guaranteed profitable route. Commercial information can reward skilled appraisal and trusted contacts. Optional quests/commitments are inspectable alongside readiness, not a mandatory mission board.

No forced end-of-voyage grade or profit settlement. Keep financial transactions, personal learning, cargo ownership and events in optional readable histories. Arrival restores free control at the selected port; closing a report does not gate trade or travel.

Readiness and encounter warnings must name actionable blockers. Inspect native UI at 1280×800, 960×700 and 640×600. Existing compact overlay background bleed needs a presentation check; screen captures are not evidence of user comprehension.

## 15. Implementation phases and gates

The authored foundation and M3 P0–P4 slices are implemented and scoped-verified as recorded above. Other phase gates are pending. Numeric prices, staffing, frequency, progression and thresholds are measured/tuned during their relevant phase.

First selected system: **authored archipelago, ports and scenario launch**. Agent implementation guidance: `AUTHORED-ARCHIPELAGO-IMPLEMENTATION-PLAN.md`. It covers the relevant M0/M1 foundation and narrow free-travel integration; it does not authorize or claim the later combat/rewards phases.

M2 — ship preparation and organization — is now **scoped-verified**; evidence and limitations are in `SHIP-PREPARATION-IMPLEMENTATION-PLAN.md`. The M2 result does not advance or satisfy M3–M8; no later phase is authorized or complete by this status update.

Current selected expansion: **M3 — free-trade information loop**, now scoped-verified through P4. Separate implementation guidance and evidence: `FREE-TRADE-IMPLEMENTATION-PLAN.md`. M3 completion does not satisfy M4–M8 or the combat-inclusive MVP; human release acceptance remains in M8.

| Phase | Work | Exit evidence |
| --- | --- | --- |
| M0 — lock rules/interfaces | Confirm outstanding decisions (§18), scenario/provider/save boundaries, combat result contract and inventories. Inventory the actual launcher, menus and tests, not just helpers. | Authority/state diagram, explicit law/prize rules, dependency list and stable acceptance fixtures; no implementation against guessed combat contracts. |
| M1 — authored playable archipelago | Explicit MVP launch/save mode; disable generated prehistory/strategic society hooks in that mode; functional differentiated ports, authored NPCs and route data. | Fresh launch works with generation/sim entry points made to fail; save/resume preserves sample identities; three-port minimum trade walk-through, no mandatory delivery mission. |
| M2 — ship preparation and organization | Connect construction, room functions, stock designation, merchandise placement/capacity, gear, labor/officers and readiness. | Build/rearrange a functional ship, actually haul stock, handle a blocked job, staff critical work and depart with disclosed risks; conservation and larger-hull-capacity fixtures. |
| M3 — free-trade information loop | Useful chart/quotes/reports/guides, finite port markets, no contract funnel, optional histories and ordinary arrival. | Several self-chosen buy/sail/sell circuits remain solvent under measured wages/provisions/fees; bad choices can lose money; waiting/arbitrage and remote-knowledge tests. |
| M4 — sea traffic and interaction | Bounded relative motion/intent, repeat contact instances, signals/parley, local pirate risk/fear, neutral marines, peaceful deck transfer and finite at-sea trade. Implementation plan: `M4-SEA-TRAFFIC-IMPLEMENTATION-PLAN.md`. | Crossing/overtaking/following/escape and repeat meetings; consent and atomic trade tests; no omniscience, forced fight or permanent first-sighting exhaustion. |
| M5 — campaign combat | Consume/request/resume/result bridge with approved deck/party controls, equipment and medical/progression mapping. | One full real encounter → fight/retreat/surrender → exactly-once campaign consequences → freely continue/sell/repair, with saves at all boundaries. Blocked on separate combat readiness, not replaced with a fake result. |
| M6 — lawful rewards and prize recovery | Hostility/warrant proof, surrender trust, brig/care, bounty delivery, loot, hull acquisition/transfer/escort/sale, visible standing unlocks. | Verified pirate defeated, loot conserved, named prisoner delivered once, recovered hull sold or used; wrong-target/neutral theft/duplicate reward/capacity tests. |
| M7 — salvage and rewarding competence | Finite sea treasure/recovery sites, deferred chart leads, practical noncombat skill bindings and useful rare items. | A recovery improves a prepared player's options; novice/skilled comparisons have real bounded benefits; no swimming, hidden free stock or XP/reward farming. |
| M8 — integrated sandbox release | Balance and legibility, quiet upkeep, meaningful deterrence, saves and actual user playthrough. | Acceptance checklist below, representative native captures, timed performance and honest suite report; no exposed dead ends. |

M1–M4 and preparation/content work can progress while combat is developed. M5 remains a required gate for the advertised MVP; M6's final rewards depend on authoritative results. Reputation data definitions and salvage preparation can be designed earlier without inventing fight outcomes.

## 16. Acceptance: what a working MVP means

### A. A real player run, not just isolated demonstrations

1. Start the authored MVP from its normal launcher with no generated history/world-sim dependency.
2. Gather useful but imperfect market/route information; choose cargo and destination without accepting a delivery contract.
3. Hire/equip crew; change labor priorities; build or rearrange a useful room/fitting and stockpile with finite work/materials.
4. Identify and address a real readiness issue; sail with adequate ready/cookable food, potable water, fuel, tools and working access.
5. Accelerate quiet passage without constant minor-need popups. The same elapsed time produces equivalent state under stepped/accelerated play.
6. Meet a peaceful vessel with readable course and choices; negotiate/trade or pass without forced violence.
7. Encounter hostile pirates; understand lawful commitment, choose fight or escape, and play actual campaign combat when committed.
8. Apply combat results once, recover lawful loot, accept surrender and transport an eligible prisoner for one bounty payment.
9. Recover a pirate hull with sufficient people/supplies; use or sell it, with a real capacity/ownership change and no duplicate ship/items.
10. Find or follow a sea-treasure lead, recover finite contents from the ship and retain a deferred lead without swimming or land content.
11. See a concrete crew capability improvement and a reputation unlock; observe a weaker pirate backing down without forced stronger replacement.
12. Dock, freely buy/sell/repair/recruit/rebuild, and set off again. Save/reload mid-travel, encounter, recovery and custody without reset or duplicated rewards.

### B. Required negative cases

- No free goods/money from buy/sell loops, retrying transactions, reloads, repeated captured identities or duplicated prize ownership.
- Poor stowage/access, insufficient crew, broken fixtures, overloaded hull and missing treatment supplies produce honest consequences/blockers.
- Unknown/stale reports do not reveal private cargo, exact enemy strength, hidden intentions or future pirate choices.
- Suspicious traders/neutral marines cannot be attacked and looted under a fabricated lawful-defense record.
- Surrender, escape, release, death and sold/sunk hulls remain terminal where appropriate; no bounty collection from someone no longer in custody.
- Old generated/authored saves keep their identity, existing optional commitments and data; no silent source-slot replacement.
- No hostile request stuck forever, silent simulation launch, mandatory contract/voyage-settlement modal or inaccessible prize ship in the release path.

### C. Verification workflow

Reuse existing suites first: `test_ship_building`, `test_ship_blueprint`, `test_ship_storage`, `test_ship_navigation`, `test_ship_spaces`, `test_ship_physics`, `test_ship_logistics`, `test_ship_hazards`, `test_provisioning`, `test_economy`, `test_trade_expansion`, recruitment/employment tests, `test_voyage`, `test_voyage_save`, `test_sea_encounters` and appropriate combat tests.

Add scenario-specific end-to-end tests for authored isolation, free trading, quiet passage, repeat contacts, lawful hostile commitment, at-sea transactions, combat consumption/results, custody/bounties, prize title/party/stock transfer and salvage depletion. Existing headless-sim tests do not certify player prize systems.

Run focused tests and compile/whitespace checks per phase. Native previews must use the same rows/actions as the live UI. Run an actual launch-to-return playthrough, then extended repeated sailing to expose depleted content, wage/liquidity deadlocks and encounter farming. Record performance, frequency, economic results and uncertainty; do not choose impressive numbers without measurements. Keep unrelated suite failures distinct, but report them honestly.

## 17. Prompt-to-artifact checklist for implementation tracking

| Agreed requirement | Evidence required before calling it done |
| --- | --- |
| Free choice of goods/routes/sales; no delivery contracts | Normal UI run and scenario tests with no required contract acceptance; independent market quotes and transactions. |
| Shipbuilding, rooms, organization and worker assignment matter | Changed geometry/fixtures/storage/jobs affect reachable work and preparation; conserved materials and staffing evidence. |
| Simulated routine, not daily crew trouble | Measured prepared passage and interruption logs; no hidden neglect from skipped time. |
| Few islands, routes and hardcoded NPC/faction samples | Authored scenario/version/save plus functioning map/harbors; generation/sim isolation test. |
| Sea action: intent/course, distant talk, close options, transfer/trade/flee/fight | Native scenario matrix and relative-motion/consent/transfer/transaction tests. |
| Lawful defense, bounty prisoners and pirate loot/hulls | Hostility/warrant proof, real combat result, custody/title/asset records and duplicate-proof settlement tests. |
| Wealth attracts pirates; victories deter small ones and invite some larger threats | Controlled attraction/deterrence comparisons, bounded traffic frequency and no omniscient/scaling-to-player replacement. |
| Noncombat growth is practically rewarding | Real work/XP plus measured novice/skilled capability difference exposed in UI. |
| Reputation unlocks content | Named threshold/evidence and usable stock/recruit/report/contact unlock, not only a number. |
| Treasure without swimming/land adventures | Ship-accessible finite recoveries and persisted/depleted chart leads. |
| Combat progression, gear and medical consequences | Approved campaign import/result mapping, actual continued injuries/XP/items and save/replay checks. |
| Freedom rather than formal voyage settlement | Arrival restores ordinary control; optional ledgers only, no mandatory profit grade or blocking quest funnel. |
| Other master-plan features paused | Master scope override links here; no new sim/magic/fleet/land work needed by this implementation. |

## 18. Decisions still to settle before their implementation phase

1. Fourth ship archetype: second trader behavior or something genuinely distinct? Three behavior families can support the first build; do not promise a fourth without a purpose.
2. Exact port count/content and initial named roster; how depleted traffic is replenished without resurrecting captured people/hulls or attaching world simulation.
3. Legal hostile-commitment threshold; verified-warrant attack permission; block unprovoked attacks in MVP versus a bounded violation/marine response path.
4. Which combat subsystem/interface is approved for campaign use, and when its request/result/deck/party contract is stable.
5. Relative-motion units/time, at-sea stopping/anchoring, pursuit termination and safe transfer geometry.
6. Prize-crew staffing, same-route escort limits, home-ship transfer and title/sale rules; no independent fleet automation.
7. Bounty identities/authorities and live-capture scope, simple prisoner provisioning/treatment, release and overflow policy.
8. Ownership and lawful recovery of abandoned/salvage goods; which actual tools/fittings and rare rewards are supported.
9. Which existing skill IDs back the first tangible specialty effects; progression rates and unlock/standing thresholds only after measured trials.
10. Captain death/save policy for a lethal but readable run; retain the master plan's no-assumed-resurrection direction unless explicitly revised.

None of these open numeric/interface details authorizes replacing the agreed MVP with a smaller contract demo or a noncombat trade loop. Resolve dependencies explicitly, implement the complete local sandbox, and keep the wider master vision deferred rather than accumulating new prerequisites.
