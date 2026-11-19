# Recruitment and Commitments — Expansion Plan

Date: 2026-11-19
Status: release-one scope implemented on 2026-11-19. §5A–§16 remain the audit and design record; §17 records shipped behavior, deliberate deferrals and the evidence actually collected.
Parent: `PIRATES-OF-MISKA-MASTER-PLAN.md`, especially §§6, 7, 13–15.
Predecessor: `VOYAGE-OPPORTUNITIES-EXPANSION-PLAN.md`.

## 1. Objective and player experience

Make employing people a meaningful, understandable commitment rather than a one-time purchase.

**Meet a candidate → review their needs and contribution → agree to clear terms → sail and honor obligations → review the relationship at port.**

Ordinary recruits should be straightforward to employ. An exceptional recruit can give the captain a new reason to sail, but not every sailor needs a personal quest or negotiation minigame. A cook or dependable worker must remain worth employing without combat or magic.

The first release must deliver one ordinary paid recruit and one authored voluntary candidate with a simple destination undertaking, integrated with the existing voyage loop. Keep personal goals bounded and optional. No daily payment clicking or reward collection.

## 2. Verified starting points and audit boundaries

Initial source inspection on Nov 19 established these entry points; recheck them before implementation because concurrent work continues:

| Existing authority | Verified entry point | Preserve / investigate |
| --- | --- | --- |
| Finite authored candidate identities | `pirates/crew_recruitment.py:8,42,67` | Candidate profiles, canonical recruit construction and available/recruited/declined pool states already exist. Do not generate another copy of an employed person. |
| Hire and captain funds | `pirates/economy.py:407–421` | Port-only eligibility, signing advance, ShipLife transfer and finite pool update. Audit transaction rollback across all mutations. |
| Active crew and berth installation | `pirates/ship.py:364–397` | Current hire installs a recruit-provided hammock on clear deck space and initializes relationships. Do not quietly replace this with a new capacity system. |
| Dismissal and retirement | `pirates/ship.py:399–417` | Existing port-only departure and former-crew authority must own employment exits too. Audit supply/equipment/job references before extending it. |
| Port recruitment UI | `pirates/trading_ui.py:235–265` | Extend existing candidate review/accept/decline rather than add an unrelated hiring menu. |
| Recruitment regression baseline | `tests/test_crew_recruitment.py:12–240` | Finite identity, save migration, hire, failed hire, release, former crew and UI tests already exist. |
| Wage disclosure gap | `pirates/management.py:772` | Current departure review explicitly says wages/economy are not simulated. Change only when a verified payroll path exists. |
| Voyage objectives and time | `pirates/voyage.py`, `pirates/game.py` | Reuse canonical advancement and arrival; do not settle obligations by rendering a report. Audit exact current functions before editing. |

No persistent wages, crew wallets or agreement implementation was established by this initial inspection. Treat those as audit questions, not proof of absence. The separate `sim/` world has its own identity, custody and financial work; do not import its money, people or ownership model into player crew without a designed bridge.

## 3. First-release scope

### Required

1. Explicit acceptance of a small, legible agreement with role, signing payment, wage, pay schedule, review and departure terms.
2. Automatic payroll accrual and payment through existing time and Economy funds, with exact conservation and visible arrears.
3. One finite voluntary candidate with a clearly specified destination undertaking that creates a voyage objective.
4. Read-only agreement/obligation review in existing crew and departure screens.
5. Port-based renewal, mutually accepted amendment, dismissal and voluntary departure through existing crew lifecycle APIs.
6. Deterministic persistence, legacy-save policy, negative tests and a native end-to-end walkthrough.

### Optional only after required gates pass

- One savings goal using earned personal funds, such as an authored retirement target; no real-world remittance recipient unless an accounting sink is defined.
- One non-monetary exceptional clause using a capability already supported and verified by the game.

Optional features cannot hold the ordinary agreement/payroll slice hostage. Explicitly record deferral rather than implying they shipped.

### Exclusions

- Profit/treasure shares, investment returns and undefined “net profit.” Design separately after voyage accounting has a precise cost/revenue basis.
- Auctions, arbitrary free-text contracts, bargaining agents or universal NPC planning.
- Unrestricted personal shopping, independent adventures, silent purchases from ship funds or automatic equipment creation.
- Captive recruitment, slavery, ransom, prisoner finance and political custody transfers.
- Fleet payroll, remote hiring, general faction diplomacy, magic, land expeditions and a procedural quest generator.
- A global reputation penalty or violent mutiny merely because a payment is late.

## 4. Non-negotiable invariants

- Economy remains the captain-funds authority; active sailors and former crew retain their canonical identities.
- One authoritative employment record per person; no separate conflicting terms in UI, payroll and voyage state.
- No retrospective wages, surprise deadlines or unilateral repricing of accepted terms.
- Inspection, preview and save/load do not accrue time, charge money or reroll preferences.
- Every transfer has a source, recipient or explicit accounted sink. No duplicated signing advances, negative balances, lost arrears or free equipment.
- Manual and accelerated advancement give equivalent accrual, payment and clause state at equivalent boundaries.
- People cannot disappear at sea to satisfy a departure rule. Existing essential-work, cargo and equipment cleanup remains authoritative.
- Declining a proposed term does not enforce it. Amendments require explicit consent before becoming effective.
- Personal feelings, hidden world facts and future consequences must not be exposed as exact omniscient forecasts.

## 5. Phase 0 — Ownership, lifecycle and affordability audit

### Tasks

1. Trace hire and departure mutation order, pool states, canonical IDs, former-crew retention and all equipment/work cleanup.
2. Trace `World.advance()` day ordering, Economy day updates, crew lifecycle and voyage arrival finalization. Define exact payroll timing relative to expiry, death and departure.
3. Search for existing wage, wallet, debt, social-concern and promise authorities. Extend them if present; avoid duplicate ledgers or emotional penalties.
4. Inspect legacy recruitment saves and roster validation, including starter sailors, deceased/former people and zero-active-crew worlds.
5. Measure starting funds, signing advances, ordinary delivery receipts and ship provisioning costs. Run matched quiet voyages before choosing wage/deadline numbers.
6. Identify actual roles, officer assignment and contribution interfaces. A title must not invent a new capability or override work priorities silently.
7. Record source/field ownership, event ordering, candidate content, economic assumptions and migration policy in this document before implementation.

### Gate

A source map and measured affordability table identify what exists, what changes, what remains deferred and who owns each mutation. No balance number is final before measurement.

## 5A. Phase 0 audit record (2026-11-19)

This section records what was actually verified before editing. Line references are implementation anchors, not claims that the code will stay unchanged.

### 5A.1 Confirmed authority map

| Concern | Authoritative code | Verified facts |
| --- | --- | --- |
| Candidate identity/content | `pirates/crew_recruitment.py:8–39` | Three authored candidates only: Pip Ash (Verdant, 35), Elira Voss (Verdant, 95), Brannoc Reed (Cinder, 120). Profiles are copied into a sailor, never generated per hour. |
| Canonical recruit sailor | `pirates/crew_recruitment.py:42–61` | Sailor is `hire:<candidate_id>` with authored name, labor priorities (`specialty` = 1, rest = 4), personality, mental, traits, age and learning potential. This constructor is also used during save restore. |
| Finite availability | `pirates/crew_recruitment.py:64–109` | Statuses are `available|recruited|declined`; `at_port` returns available candidates only. Snapshot is version 1 with exactly `{version,statuses}`. **Restore currently requires the status key set to equal the current `CANDIDATES` key set**, so adding a candidate breaks old saves until migration is added. |
| Hire, funds, pool marking | `pirates/economy.py:407–421` | Shore/port check, candidate/port check, funds check, then `add_recruit` → `mark_recruited` → `money -= asking_price` → Economy record + message. No agreement object exists. |
| Berth and roster installation | `pirates/ship.py:364–397` | Requires two clear deck tiles; installs a new `bunk:<n>` fixture via `world.fixtures.with_bunk`, binds the layout, appends the canonical sailor, seeds mutual relationships at 50 and logs an event. |
| Berth capacity | `pirates/ship_fixtures.py:104–117` | Bunks cap at 32 total (`next_id >= 32` raises). Bunk fixture IDs derive from current bunk count. |
| Departure/retirement/death | `pirates/ship.py:399–475` | Port-only for release/retirement. Validates active work and carried supplies; former-crew ledger capped at 32 rows with strict schema `{id,name,status,day,cause}`; archives open concerns; removes the sailor; clears relationships, voyage roles and crew order. **Bunks are never removed or reused**, so hiring capacity is consumed for the life of the save. |
| Former-crew restore | `pirates/ship.py:2611–2628` | Active and former rosters must be disjoint; historical hired names are validated against `CANDIDATES`; balances/equipment are not stored in the former-crew row. |
| Recruitment consistency | `pirates/economy.py:857–863` | Recruited candidates must equal active hired sailors plus former hired sailors, so a closed employment record must still agree with the pool. |
| Roster/zero-crew behavior | `tests/test_crew_recruitment.py:148–162` | Zero active crew is valid, saveable and can plan a vacant voyage. Payroll must accept an empty roster. |
| Canonical time | `tales_sim/clock.py:15–65` | 100 ticks/second; day = `total_ticks // 8_640_000 + 1`; day boundary is midnight; `ticks_to_next_day_boundary` targets it exactly. |
| Time stepping | `pirates/ship.py:2132–2145` | All normal advancement runs in ≤100-tick chunks and calls `ShipLife.step` once per game second. `ShipLife.step` already tracks day-scoped state and can host an idempotent day hook. |
| Post-advance economy | `pirates/game.py:247–263` | `World.advance` runs `ship.advance` first, then `economy.update_day(clock.day)`, then re-checkpoints an arrival snapshot. A large advance can cross several days before this call. |
| Market/contract day updates | `pirates/economy.py:258–282` | Returns when `day <= last_day`; markets use elapsed days; contracts expire when `day > deadline_day`; `last_day` is persisted and must be ≤ the loaded clock day (`economy.py:762`). There is **no wage settlement**. |
| Voyage departure | `pirates/voyage.py:269–288` | `confirm` writes route, destination, departure checkpoint, storm forecast and status `underway`. |
| Voyage arrival | `pirates/voyage.py:429–444` | On final leg: status `arrived`, `arrival_interrupt`, port switch, harbor restore, `at_sea = False`, arrival checkpoint, record/message. Contract settlement still happens later at the harbor office. |
| Arrival report | `pirates/voyage.py:312–370` | Read-only. Shows timing/funds/cargo, matching contract state and `voyage["outcomes"]`. |
| Coastal passage | `pirates/economy.py:720–730` | Abstract 15-Mark/30-minute trip that switches port and calls `world.advance(180000)`; it creates no voyage record. An undertaking satisfiable by "reach port X" must therefore settle from the post-advance port state, not only from voyage status. |
| Save envelope | `pirates/game.py:424–458` | World save version 3; economy and ship are inline objects with their own strict field validation. Load constructs a replacement world before swapping it in. |
| Recruitment UI | `pirates/trading_ui.py:232–266` | Existing prospect list, profile panel, accept/leave/decline choice; shows only the one-time asking advance. |
| Crew/departure UI | `pirates/management.py:157–220, 756–803` | Crew profile, release/retirement, voyage review. Departure review still states "ship wages/economy are not simulated" (`management.py:772`). |
| Existing hardship concern | `pirates/ship.py:477–506` plus concern validation `ship.py:2569–2595` | Concerns are hardship-only with signals `stress|hunger|thirst|injury`; they are not a wage/notice ledger and must not be repurposed as one. |
| Existing money-payment precedent | `pirates/ship.py:631–652` | Retention bonus charges `Economy.money` directly, records the payment and advances one minute. No wallet or debt object exists. |

No wage, salary, payroll, arrears, crew wallet, personal savings or promise authority exists in `pirates/`. Searches found only sleep debt and unrelated wording.

### 5A.2 Hire mutation order and transaction decision

The only fallible operation after candidate/funds validation is berth installation: `with_bunk`, `bind_fixtures`, and the canonical sailor construction. `mark_recruited` cannot legitimately fail after `economy.py:411` succeeds in single-threaded play, but that is an invariant to enforce, not rely on.

**Decision:** keep `Economy` as the hire transaction owner. Before mutation, build and validate the agreement record and recheck deck capacity. During mutation, snapshot the old layout, crew list, money and candidate status. If any step raises (including an injected `bind_fixtures` failure), restore the layout through `bind_fixtures(old_layout)`, truncate the crew and relationships, restore money and pool status, remove the new agreement, and re-raise. No retry, no partial charge, no consumed candidate.

### 5A.3 Settlement-order decision

**Decision:** one idempotent Economy-owned day settlement, `settle_day(world, day)`, keyed by a persisted `settled_day`. It accrues and pays wages for each crossed day in order, then closes expired undertakings. It is called:

1. from the existing day-change block in `ShipLife.step` (`ship.py:2147`) so boundaries are processed in order with current crew state; and
2. defensively from `World.advance` after `economy.update_day`, so a missed or externally advanced boundary still settles.

The second call is a no-op when `day <= settled_day`. Settlement never advances time, never generates offers and never runs from a render path. Because all normal time flows through `ShipLife.step` once per game second, manual, fixed-watch and "until next day" advancement produce the same boundary state.

Departure and death settle through the current day before the employment record closes. Accrual is per completed day boundary, so there is no partial-day wage and no retroactive charge on load. Contract expiry keeps its existing rule (`day > deadline_day`), and the undertaking uses the same inclusive-deadline convention.

### 5A.4 Save and migration decision

- Persist an optional `employment` object inside the existing economy save; old saves without it load with an empty ledger whose `settled_day` is the loaded clock day (no retroactive wages).
- Employment records are keyed by canonical sailor id and store bounded integer terms, status, paid/arrears totals and at most one undertaking. `crew_history` keeps its strict four-field schema.
- `RecruitmentPool.restore` will accept a status map that omits known candidates, filling missing known IDs as `available`, while still rejecting unknown IDs, bad versions and bad values. `__init__` fills the same defaults. Adding one candidate therefore does not invalidate old saves; the new prospect simply becomes available at their port.
- Keep `recruited == active hired + former hired` validation intact. A closed agreement remains present so unpaid wages are not erased.
- Validate `settled_day <= clock.day`, non-negative integer money fields, known sailor IDs, known undertaking ports and monotonic day fields before swapping the loaded world in.

### 5A.5 Measured affordability (seed `World()`, day 1)

- Captain funds: **600 Marks**. Starter crew: **6** (`mara, ivo, nell, orin, suri, tavi`). Starter stores: 18 kg grain, 4 kg beans, 3 kg salted fish, 2.5 kg fruit, 1.5 kg biscuit (29 kg total) and 60 L clean water.
- Readiness at 6 crew: 7.00 kg ready food (1.4 crew-days), 45.00 kg with cooking (8.8 crew-days), 60 L water (4.5 crew-days). After one hire (7 crew) water falls to about 3.9 crew-days.
- Verdant purchase quotes measured: 6 bluegrain sacks = **119 Marks**; 6 brine-pear casks = 144 (5 = 119); 8 water kegs (10 L each) = **68 Marks**; 1 keg = 9.
- Top-up provisioning for 7 crew: 3 days ≈ **119 Marks** (4 sacks + 5 kegs); 5 days ≈ **187 Marks** (6 sacks + 8 kegs); 8 days ≈ 324.
- Day-1 Verdant offers: 6 bluegrain → 351 Marks due day 5; 5 brine pears → 355; 4 lantern-moth silk → 431. Purchase costs 119 / 119 / 144, so a single ordinary delivery grosses roughly 232–287 Marks before time and supplies.
- Signing advances remain 35 / 95 / 120. Hiring Pip plus five days of top-up provisions plus five days of wages leaves well over half the starting purse; hiring Brannoc instead still leaves roughly 250 Marks. Wages are therefore affordable at 1–4 Marks/day, and a failed delivery is a setback rather than an unavoidable debt spiral.
- Cinder voyage plan measured at **4.1 hours** (route Verdant → Government Patrol Lane → Cinder); Shoals 1.6 h; Freeport 6.0 h. A multi-day deadline is feasible but cannot be made tight by sailing time alone, so the first undertaking is deliberately a "do it within a few days" promise rather than a razor-thin race.

These are single-seed measurements, not balance proof. Defaults below stay provisional until the integration phase runs matched scenarios.

### 5A.6 Locked first-release defaults

- Wages are whole Marks per completed day: Pip 1, Elira 3, Brannoc 4, authored undertaking candidate 2. No fractional wages and therefore no fractional-money persistence in release one.
- The existing `asking_price` is the one-time signing payment; it never prepays wages. Starter crew keep an explicitly labeled legacy/standing arrangement with no invented back pay.
- Payroll is automatic at each day boundary from spendable captain funds. Unpaid earned wages become explicit arrears; no loan, cargo liquidation or escrow diversion.
- Underfunding uses integer proportional allocation with largest-remainder ordering by `(due × funds) mod total_due`, then sailor id. Deterministic, disclosed, with no list-order favouritism.
- One new authored voluntary candidate at Verdant asks for passage to Cinder by **day + 3**. Satisfaction means the captain is ashore at Cinder with that sailor alive and aboard by the deadline, via voyage or disclosed coastal passage. Late, released, dead or still-away closes the undertaking once with a specific cause.
- Role is a descriptive employment title (for example "Deckhand — rigging"). It does not auto-assign an officer role or silently override labor priorities. Officer assignment stays in the existing voyage UI.
- Release/dismissal remains port-only and routes through `_depart_crew_member`. No at-sea teleportation or automatic desertion. Former crew keep their employment record and unpaid wages until paid at a port.
- No savings goal ships in release one: there is no supported recipient or wallet sink. It stays documented as deferred rather than faked with an informational-only balance that implies spendable money.

### 5A.7 Carry-over constraints for later phases

- Bunks are never reclaimed. A save can therefore support at most 26 additional hires after the six starter bunks; the UI must fail cleanly when the cap is reached. Reclaiming/reindexing bunks is a separate refactor because `Sailor.bunk` indexes bunk order and is validated on load.
- The former-crew ledger holds 32 rows. Employment history must not depend on it for debt or terms.
- `crew_concerns` is hardship-only. Wage notices use Economy history, ship events/messages and the crew profile, not a new morale meter.
- Coastal passage can satisfy the destination undertaking; this is disclosed rather than silently ignored.
- Economy save data has no sub-version. New fields are optional-on-read and strict-on-write; malformed fields must reject the load rather than partially mutate the live world.

## 6. Phase 1 — Simple voluntary agreements

### Proposed record, finalized during audit

Use a bounded employment extension within existing crew/economy persistence, not a generic contract framework. Candidate fields:

- Stable agreement ID, sailor ID, accepted port and canonical acceptance tick.
- Explicit versioned terms: role, joining payment, wage amount, cadence, first accrual boundary, review rule and exit conditions.
- Status: active, review requested, notice, closed; closure includes a specific cause and tick.
- Payroll totals/checkpoints and arrears if the audited authority lacks them.
- At most one first-release personal undertaking; no unbounded nested clause language.

Separate a proposal from accepted terms. Display exact fees and first payment before confirmation. Acceptance must atomically validate port, availability, money and berth/placement, then transfer one identity and debit once. Any failure leaves pool, roster, fixtures, funds and agreement unchanged.

### Starter and old-save policy

Recommended default: existing sailors retain an explicitly labeled legacy arrangement with no invented back wages; new wage terms require review and acceptance. Do not silently grant a permanent free-labor advantage without explaining that it is a compatibility arrangement. Decide whether new games should start with authored paid agreements separately from old-save migration.

### Gate

One ordinary candidate can be reviewed, accepted once, assigned a supported role and reloaded with identical terms. Invalid hire paths are atomic. Existing hire/release regressions remain green.

## 7. Phase 2 — Payroll, savings and honest arrears

### Proposed policy to validate

- Use integer Marks or a single documented fixed subunit with persisted remainder; never accumulate binary-float money.
- Accrue from acceptance forward at canonical elapsed-time boundaries; display whether wages are daily service or another cadence.
- Automatic payment is preferred to mandatory daily clicks. Accrual and disbursement must be distinct if payment cadence differs.
- Pay from spendable captain funds only; no automatic loans, sale of cargo or diversion of delivery escrow.
- Decide stable underfunding allocation before implementation. Recommended first rule: proportional payment using integer arithmetic and stable-ID remainder allocation, disclosed to the player; no list-order favoritism.
- Retain unpaid earned wages as explicit arrears. A failed payment cannot charge again or erase debt on reload.
- Personal savings, if implemented, receive the actual transfer and persist for former crew; otherwise use an explicit cumulative paid-to-person balance, not a claim of freely spendable wallets.
- Add manual “pay outstanding wages” only as an explicit action with preview and the same accounting authority.

Define what happens when death, retirement, release or missing identity shares a payroll boundary. First-release arrears survive departure as a payable obligation; do not silently confiscate earned wages or automatically pay an invented beneficiary.

### Consequences

Late pay first produces a clear notice and existing concern evidence where supported. After an authored, disclosed grace period it can trigger a port-based review/notice. No instant global reputation loss, arbitrary skill penalty or surprise at-sea desertion.

### Gate

Matched manual/large/chunked advances and reloads conserve funds, paid balances and debt exactly, including insufficient funds, partial payment, fractional remainder and simultaneous lifecycle boundaries.

## 8. Phase 3 — One recruit-linked reason to sail

### Authored destination undertaking

A voluntary candidate asks to be taken to one existing reachable port by a disclosed deadline. The agreement specifies:

- Exact destination and deadline measured from acceptance.
- Whether the person joins immediately; recommended first slice is an active crewmate who contributes normally while aboard.
- What satisfies the undertaking: recommended arrival with this person alive and aboard, plus an explicit port confirmation if the story requires going ashore.
- Whether they remain employed, request review or depart afterward; no hidden interpretation.
- Exact consequence of late arrival, withdrawal, death or departure; no invented reward or moral judgment.

The candidate's motivation is authored and bounded. Do not manufacture a living relative, rescue victim, faction grievance or hidden pursuer without a persistent supported identity.

### Integration

- Candidate review shows the undertaking before acceptance.
- Departure review lists active promises alongside deliveries without merging their ownership/accounting.
- Route choice shows known time uncertainty and deadlines; do not promise that ETA guarantees success.
- Arrival calls one state-transition authority, not renderer logic. Persist satisfied/late/withdrawn/failed outcomes and their causal tick once.
- Arrival report and crew profile explain the outcome and any optional review/follow-up.
- Declining or failing must leave an understandable continuation, not a guaranteed bonus quest.

### Gate

Complete accept → depart → interruption/delay → destination → review. Cover early arrival, deadline boundary, late arrival, wrong port, dead/former/missing sailor and repeated arrival/load without duplicate completion.

## 9. Phase 4 — Review, renewal and departure

- Reviews occur at authored renewal, promotion, fulfilled undertaking or explicit request—not every small skill increase.
- Keep existing accepted terms effective until a mutually accepted amendment starts at its recorded tick.
- First release uses a small set of authored offers (accept, keep current arrangement where permitted, give notice, decline); no general negotiation solver.
- Disclose notice length, payable arrears, joining-payment refund policy and whether review requires being ashore.
- Voluntary exit/dismissal routes through existing former-crew logic and validates issued gear, carried supplies, reservations, jobs, officer roles, relationships and bunk references.
- At sea, record intent/notice; disembark at a valid port. Do not teleport the sailor or silently interrupt safety-critical simulation.
- Define whether a departed authored candidate can be rehired. Recommended first slice: closed and retained in former crew; no respawn or repeat signing-payment exploit.

### Gate

Employment closure preserves one identity and all owned balances, removes stale work/officer references safely, and does not erase a personal undertaking or debt from history. Save/load accepts active and former arrangements.

## 10. Phase 5 — Optional bounded personal goal

Only proceed after payroll and lifecycle gates pass. Implement at most one savings threshold as an authored reason for review (for example retirement readiness), not an automatic purchase or departure.

Goal progress uses actual received funds, not promised wage or captain wealth. Spending requires an explicit supported port transaction with recipient/sink and conservation tests. If those interfaces are absent, keep savings informational and defer spending/remittances. Do not duplicate existing retirement age/notice rules.

### 10A. Release-one decision — deferred

The savings goal is **deferred for release one**; it is not implemented anywhere in the shipped code and no UI claims it exists.

Reason: the audited authorities have no supported recipient or sink. `Economy.money` is captain funds, a wage payment is a one-way debit with no sailor-side account, and former crew keep only a factual cumulative `paid_total`. Implementing a threshold would either invent a second money store (duplicating the funds authority, with new conservation and save rules) or report a "savings" number that cannot be spent and would misrepresent a wallet.

What release one keeps instead:

- `paid_total` is a factual lifetime-earned figure per agreement, exactly the sum of transfers that already left captain funds.
- `arrears` is the opposite obligation and stays visible until paid.
- Retirement still uses the existing retirement age/notice rule; employment adds no second retirement path.

Re-entry condition: a later slice must first define one accounting sink (for example an explicit sailor-side account with a named counterparty) plus conserved transfer, save migration and negative tests. Only then can a savings threshold gate a review.

## 11. UI and information design

Extend existing candidate, crew profile, port and voyage screens:

| Screen | Required information / action |
| --- | --- |
| Candidate overview | Observable skills/background, contribution, asking terms, ordinary vs exceptional complexity |
| Agreement confirmation | Signing cost, wage/cadence, first due boundary, undertaking/deadline, review/exit terms and affordability |
| Crew profile | Accepted terms, actual paid/accrued/unpaid totals, bounded history, notice/review state |
| Departure | Total expected payroll exposure over estimated passage, arrears, promise deadlines and uncertainty |
| Arrival | Relevant payroll/undertaking outcomes and optional next action, without compulsory reward collection |
| Port review | Exact amendment, outstanding payment or departure consequences before confirmation |

Reports are read-only. Use existing social concerns/logs for sourced evidence; avoid adding duplicate morale/fame meters. Summaries should not reveal secret candidate intent or treat recorded reports as current knowledge.

## 12. Persistence and transaction design

1. Version/validate agreement and monetary fields with existing world-save conventions. Reject negative amounts, duplicate IDs, unknown people and impossible checkpoints.
2. Migrate old saves without charging retroactive wages, rewriting historical hire fees or duplicating former sailors.
3. Persist fractional remainders, last accrual/payment boundaries, accepted amendments and terminal undertaking outcomes.
4. Validate replacement worlds before swapping them into the live game. No autosave over invalid source data.
5. Transaction helpers must either prevalidate all fallible operations or roll back the entire mutation set. Test injected failure after berth/roster preparation as well as insufficient funds.
6. Keep accepted terms and factual outcomes durable. Bound narrative history separately; pruning text must not delete outstanding debt or reactivate closed promises.
7. No renderer objects, hidden wall-clock scheduling, unseeded preference rolls or remote service dependencies.

## 13. Verification matrix

| Case | Required evidence |
| --- | --- |
| Ordinary hire | Correct identity/berth/terms, exact debit, no repeated acceptance |
| Exceptional undertaking | Visible terms and destination integrated with a real voyage |
| Invalid hire | Wrong port, sea, no space, insufficient funds, unknown/declined/employed ID leave state unchanged |
| Payroll timing | Before/exactly/after boundary; join mid-period; no retroactive charge |
| Underfunding | Zero/partial funds, deterministic allocation/remainder, explicit conserved arrears |
| Funding conflicts | Delivery escrow, trade, provisions and payroll cannot spend the same funds twice |
| Manual vs accelerated time | Same accrual/payment/notice/undertaking state at matched boundaries |
| Deadline | On-time/late/wrong port, interruption delay, no retrospective reinterpretation |
| Person unavailable | Death, release, retirement, missing identity and zero crew have defined outcomes |
| Review/amendment | Old terms persist until consent; no second signing payment unless explicitly agreed |
| Departure | Work/equipment cleanup and former identity/balances survive load |
| Save/migration | Old/new, pending payment/review, at-sea notice and completed/failed undertaking |
| UI purity | Inspection cannot charge, advance time, generate offers or repeat settlement |
| Optional savings goal | Actual earnings only; no invented purchases, accounts or beneficiary |

Run focused recruitment/lifecycle/economy/ship/storage/social/voyage/save tests, then attempt repository checks with a stated time boundary. Long unrelated simulation runs are not a reason to block this slice indefinitely; report incomplete coverage honestly, and stop an owned run if the user directs. Never weaken acceptance tests or kill unrelated work.

Native walkthrough: review ordinary terms → failed hire → accept → payroll boundary → underfunding notice → exceptional undertaking/departure → arrival/review → departure confirmation. Capture supported sizes; scripted fixtures prove layout/behavior, not ordinary frequency or player comprehension.

Compile and whitespace checks must include new/untracked files. Record actual commands, counts, capture paths and any failures or stopped suites.

## 14. Measurement before tuning

Compare matched starting funds/crew/supply conditions over a quiet short voyage, a delayed voyage and a delivery round trip. Record signing expense, payroll accrued/paid/unpaid, captain funds, per-person paid balances, supplies, game time, interruptions, useful work, undertaking outcome and exit/review requests.

Questions to answer before choosing numbers:

- Can the modest starting captain afford an ordinary recruit and necessary provisions?
- Does adding one person create useful capacity rather than only a recurring tax?
- Can a normal delivery fund reasonable employment without requiring every voyage to be profitable?
- Does a delayed passage create a readable tradeoff rather than an unavoidable debt spiral?
- Is the destination undertaking feasible under measured travel uncertainty?
- Are ordinary terms simpler than exceptional ones, and are unpaid wages difficult to exploit?

One seeded route pair cannot establish wage balance, deadline fairness or review frequency. Keep defaults provisional until ordinary multi-run evidence exists. Tune one cost/rule group at a time.

## 15. Implementation sequence and completion gates

1. **Audit:** complete ownership/time/save map and affordability baseline.
2. **Simple agreement:** one atomic ordinary hire, explicit terms and migration.
3. **Payroll:** deterministic accrual/payment/arrears with conservation and UI.
4. **Personal undertaking:** one authored destination promise connected to voyage/arrival.
5. **Review and exit:** mutual amendments, notice and conserved departure.
6. **Optional goal:** savings-only first; defer unsupported transactions explicitly.
7. **Integrated verification:** focused suites, native flow, compile/whitespace, measured affordability and honest repository-suite status.

### Release checklist

- [x] Source ownership, event ordering and legacy policy documented before implementation.
- [x] Ordinary candidate can join under accepted, clear terms exactly once.
- [x] Payroll and arrears conserve funds and survive all time/save boundaries.
- [x] One exceptional voluntary recruit supplies a feasible, explicit voyage undertaking.
- [x] Arrival and review close/update obligations once without fabricated rewards.
- [x] Port departure preserves identity, assets, earned money and unresolved debt.
- [x] Ordinary sailors remain useful and do not need elaborate personal clauses.
- [x] Existing authority, access, finite resources, negative cases and information boundaries remain intact.
- [x] Optional savings goal explicitly deferred with a stated re-entry condition (§10A).
- [x] Focused tests, scripted UI captures, measurements and compile/whitespace checks recorded (§17.3).
- [x] Native rendering captured at 1280×800/960×700/640×600 (`tools/preview_employment.py`, 39 PNGs incl. refused hire and underfunded payday). Scripted, not unaided play; the 640×600 terms panel needs one page-down (§17.3–17.4).
- [ ] Repository suite completed with a summary. **Not done:** the earlier full run was user-stopped at ~85% + 20 tests and has not been rerun; compile and whitespace checks pass but are not a suite result (§17.4).
- [x] Plan and global skill updated; unrelated concurrent work preserved.

## 16. Decisions required before coding

Resolved by the Phase 0 audit in §5A for release one:

- Whole-Mark daily wages, accrual on completed day boundaries, integer largest-remainder underfunding allocation; no fractional-money persistence.
- Existing sailors keep a labeled legacy/standing arrangement; new hires accept explicit terms. No invented back pay.
- `asking_price` stays the one-time signing payment and never prepays wages. No refund in release one (no cancellation path after a successful join).
- Crew get a factual cumulative `paid_total`, not a spendable wallet. Payment leaves captain funds; no recipient account is invented.
- Arrears survive departure and death as a port-payable Economy obligation. Former crew retain the agreement record outside `crew_history`.
- New Verdant candidate: passage to Cinder by day + 3, satisfied by being ashore there with the sailor alive and aboard; voyage or disclosed coastal passage both count.
- Late pay produces a clear notice; no automatic at-sea departure and no reputation loss. Release stays port-only.
- Released hired candidates are not rehireable in release one (pool remains `recruited`, identity remains in former crew). Role is descriptive only; amendments are limited to the authored post-undertaking review.

Settled during release-one implementation:

- Post-undertaking review offer: "Ship's clerk — records and rigging" at 3 Marks per completed day, offered only to Maelin Orr on satisfaction, accepted or declined ashore, effective from the consent day, with no second signing payment.
- Arrears grace: there is no separate grace timer in release one. One factual notice is emitted on the first day wages cannot be paid in full; arrears then stay visible and payable at port, and late pay never causes at-sea departure or reputation loss. A port review/notice tier is deferred.
- Bunk reclaim: deferred. Bunks are never reclaimed, so lifetime hiring stays capped at 26 new sailors beyond the 6 starter berths (32 berth and `crew_history` rows). Reclaim/reindex needs its own migration and is not implied by employment.

Resolve remaining questions with existing code and measured scenarios, not silent assumptions. Success is a captain choosing whom to employ, understanding the commitment, and gaining a credible reason to sail—not a longer list of payroll chores.

## 17. Implementation record (2026-11-19)

Release one is implemented on the existing authorities. This section records what shipped, what was deliberately deferred, and what the evidence does and does not prove.

### 17.1 Shipped behavior

| Concern | Shipped implementation |
| --- | --- |
| Agreement ledger | `pirates/employment.py:12` is schema v2: one record per `hire:<candidate_id>` holding role, daily wage, signing payment, acceptance day, status/close cause, arrears, cumulative `paid_total`, `unpaid_since_day`, one optional undertaking and one optional `review`. Records live in `economy.employment`, not in the strict `crew_history` schema. |
| Atomic hire | `pirates/economy.py::recruit_candidate` prepares and validates terms first, then debits money, marks the finite pool, builds the canonical sailor, installs the berth and commits the record. Any failure restores money, pool, sailor, layout, events and record (`rollback_recruit`), and `ShipLife.add_recruit` rejects a duplicate identity. |
| Payroll | `EmploymentLedger.settle_day` (`pirates/employment.py:336`) accrues one whole Mark per completed day for each active agreement and pays automatically from captain funds. Short funds are split by integer largest remainder (`(due × available) // total`, remainder by sailor ID), so no list-order favouritism and no fractional money. `ShipLife.step` calls it on the day change; `World.advance` repeats the idempotent pass so a loaded or externally advanced clock cannot skip pay. |
| Arrears | The first day a sailor cannot be paid in full emits one Economy message. `pay_arrears` settles all debt or one sailor at port. Arrears survive release and death and remain payable from the former-crew ledger; no beneficiary is invented and no wage is confiscated. |
| Undertaking | Maelin Orr (`pirates/crew_recruitment.py:42`) asks for passage to Cinder Quay by acceptance day + 3. `settle_undertakings` closes each open promise once: `satisfied` requires being ashore at Cinder with the sailor alive and aboard on or before the deadline; late arrival, a passed deadline, death and release close as `late`, `failed` or `withdrawn`. Each closure is announced once through Economy history/messages and voyage `outcomes`. |
| Review | Satisfaction gives Maelin `review = {"status": "offered", "day": N}`. The port crew menu offers "Ship's clerk — records and rigging" at 3 Marks/day; accepting or declining requires being ashore, an accepted wage applies from the consent day, and no second signing payment exists. |
| Departure | Release stays port-only through `ShipLife._depart_crew_member`. It settles the completed day while the sailor is still aboard, closes the agreement and undertaking, writes the former-crew row, clears officer and crew-order references, and preserves debt. |
| Voyage and port information | `pirates/management.py::crew_commitment_rows` is a pure projection of daily payroll, current arrears, planned midnight crossings and open promises; `pirates/trading_ui.py` discloses role, signing payment, wage, exit terms, affordability and the undertaking before acceptance. |
| Saves | A world without `economy.employment` loads an empty ledger at the current clock day with no invented back pay. Employment v1 snapshots migrate to v2 with `review: null`. Missing candidate keys in the recruitment status map are added as `available`. Malformed ledgers, records, undertakings and reviews are rejected. |

### 17.2 Deliberate deviations and deferrals

- **At-sea notice is deferred.** Release and review are port-only. §9 sketched recording notice at sea; the shipped slice instead requires the captain ashore so no sailor is teleported or silently dropped from safety-critical work. This is a scope reduction, not an at-sea feature.
- **Savings goal deferred.** Nothing in §10 is implemented; the reasons and re-entry condition are in §10A.
- **One authored amendment only.** The post-undertaking review is the sole negotiation path; there is no general bargaining, counter-offer or promotion solver.
- **Legacy standing arrangement.** Starter sailors and pre-feature hires show an explicitly labeled standing arrangement; they accrue no back pay and are not retroactively charged or repriced.

### 17.3 Verification evidence

Run from `E:/Pirates of Miska`:

| Command | Result |
| --- | --- |
| `python -m pytest tests/test_employment_agreements.py tests/test_employment_payroll.py tests/test_employment_undertaking.py tests/test_employment_review.py tests/test_employment_lifecycle.py tests/test_crew_recruitment.py tests/test_crew_lifecycle.py tests/test_crew_detail.py -q` | 98 passed in 10.83s |
| `python -m pytest tests/test_economy.py tests/test_voyage.py tests/test_ship_life.py tests/test_trade_expansion.py tests/test_shared_meals.py -q` | 181 passed in 107.00s |
| `python -B tools/preview_employment.py --output C:/Users/Warp/AppData/Local/Temp/opencode/employment-native-preview` | native walkthrough passed: 13 screens × 3 sizes = 39 PNGs + `verification.json` |
| `python -m compileall -q pirates tests tools/preview_employment.py` | OK |
| Whitespace scan of every touched/new file (including this plan) | no CRLF, trailing spaces or tabs |

The focused files cover, per §13: ordinary and invalid hire with rollback, one-time acceptance, terms/save migration, daily accrual at before/exactly/after boundaries, zero and partial funding with deterministic allocation, arrears notices and manual settlement, death/release debt survival, undertaking on-time/late/wrong-port/at-sea/release/death/reload/idempotence, review offer/consent/decline/migration, rehire prevention, officer and crew-order cleanup, and the read-only voyage disclosure.

Scripted capture of the real menu functions with a text-only renderer (11 screens: port candidate → Maelin terms/undertaking → hire → satisfaction → crew review accepted → profile → voyage commitment rows → arrival report) is at `C:/Users/Warp/AppData/Local/Temp/opencode/recruitment-ui-captures.txt`; it verifies text and control flow, not layout or hitboxes.

Native rendering was then verified by `tools/preview_employment.py`, which draws the same shared row helpers (`prospect_rows`, `candidate_terms_rows`, `profile_rows`, `employment_rows`, `review_offer_rows`, `departure_rows`, `crew_commitment_rows`, `arrival_report`) through the real tcod renderer at 1280×800, 960×700 and 640×600 and writes 39 PNGs plus `verification.json`. It covers the plan's walkthrough list: prospect terms → hire → voyage commitment disclosure → arrival report (open and satisfied) → review offer → reviewed profile → release consequences, plus a second world for a zero-funds refused hire (state unchanged) and an underfunded payday (1 Mark paid against 3 due, both sailors' arrears and notices). At 640×600 the terms panel paginates — role and signing payment end page 1, while wage and undertaking are on page 2 — so the smallest window needs one page-down to read the whole commitment.

### 17.4 What is not established

- **No unaided native playtest.** `tools/preview_employment.py` is scripted: it drives the real renderer and real row helpers, but a person did not play the flow. The 640×600 pagination note above stands, and ordinary comprehension, hitboxes and menu timing remain unproven.
- **No complete repository-suite result.** The earlier full `python -m pytest -q` run was stopped by the user at roughly 85% plus 20 tests and produced no summary, and it has not been rerun. A whole-tree compile attempt briefly failed outside this slice while concurrent schema-13 duel work left `assert ...to_data()13` in five `tests/test_duel_system_*.py` files; those files were left untouched and the concurrent work has since corrected them — the final `python -m compileall -q pirates tests tools/preview_employment.py` passes. Compiling is not testing: this slice must not be reported as a green repository suite, and no unrelated failure was hidden or reclassified.
- **No balance claim.** The seeded day-1 baseline (§5A.5: 600 Marks, 6 crew; Cinder ≈4.1h; offers 351–431 with 119–144 purchase costs) bounded the defaults but cannot establish wage fairness, deadline generosity or review frequency. Defaults stay provisional until ordinary multi-run evidence exists; tune one cost/rule group at a time.
- **No bunk reclaim.** Lifetime hires remain capped as described in §16.

