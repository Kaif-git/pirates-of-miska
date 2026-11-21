# Simulation Coherence Repair Plan

## 1. Purpose and status

Repair the world-state and behavioral defects exposed by the seed-0 first-year review before extending or performance-tuning the simulation further. A busy log is not evidence of a coherent, meetable world.

**Status: planning only; no repair phase is verified.** This document does not resume the interrupted implementation goal or authorize unrelated combat/campaign changes.

Authority: `MISKA_SIM_PATCH_SPEC_V2.md` extends `MISKA_SIM_IMPLEMENTATION_SPEC.md`. This plan records the latest review and proposed amendments below; unchanged P0–P10 obligations remain in force. Previous completed milestones retain their historical evidence, but their validators must not be treated as covering the newly identified gaps.

Evidence baseline:
- `C:/Users/Warp/AppData/Local/Temp/opencode/miska-one-year-seed0/events.jsonl.gz` — all 69,696 events.
- `C:/Users/Warp/AppData/Local/Temp/opencode/miska-one-year-seed0/first-year-chronicle.md` — filtered consequential trace, not the complete log.
- `C:/Users/Warp/AppData/Local/Temp/opencode/miska-one-year-seed0/acceptance/batch.json` — one-year gates; long-run gates are N/A.
- Canonical event SHA-256: `83e3aeb5105b902033e88516f5c358ae07b9f8be1076ec1faf32ed20cf4ae23e`.

Treat the review's diagnoses as hypotheses to reproduce, not as proof of every underlying cause. Absence from the filtered chronicle does not prove absence from the raw log.

## 2. Non-negotiable invariants

1. Deterministic behavior from world seed and persisted state; save/resume produces the same continuation.
2. Exactly one legal owner per extant ship. Ownership, operational control, captaincy and faction membership are distinct relationships; each has explicit transfer rules and historical intervals.
3. No action by terminal characters, ships or organizations. Dissolution does not kill members; revival/reformation requires an explicit new lifecycle transition and identity policy.
4. People never disappear or duplicate across named records, statistical pools, squads, prisoners, survivors and archives. Promotion/demotion transfers representation, not population.
5. Capture, rescue, recruitment, ransom, construction and replacement conserve people, goods and silver with explicit sources/sinks.
6. No ownership change, loss, promotion or replacement without an auditable causal event. Historical ownership and membership are evaluated at event time, not against final state.
7. Preserve every decision wake. Aggregating observations or exported routine rows must not coalesce decisions, suppress outcomes or alter RNG order accidentally.
8. No unrelated user work is reset or overwritten. Headless `sim/` repairs do not silently modify campaign or duel behavior.

## 3. Spec amendments to record explicitly

| Existing requirement | Proposed refinement from this review |
|---|---|
| A1 calls any encounter narrative | Encounter classification depends on consequential state change. Uneventful withdrawals/parleys are routine; changed relations, information, obligations or resources may justify a narrative outcome. |
| A1 permits routine aggregation | Default exported logs aggregate routine activity; debug mode retains raw rows. Preserve causal references through a resolvable archive/index. |
| A2 terminal guard focuses on ships/characters | Extend lifecycle validation to crews/factions and stale organizational commands; distinguish dissolution from member death. |
| A3 monthly recruiter | Keep periodic budget audits if needed; actual recruitment/acquisition follows opportunities, resources and persisted scheduling, not identical monthly creation batches. |
| A3 deed-backed promotions | Deeds must be qualifying achievements, not merely two creation/acquisition events. No fixed promotion quota. |
| A6 faction coherence | Ordinary crew creation does not found a faction. Losing one ship does not by itself dissolve a faction or erase a crew. |

Record accepted refinements in the authoritative spec before implementing conflicting behavior. Do not silently weaken original thresholds. The 80% log reduction is a serialized-size comparison over the required horizon, not a claim that routine rows must comprise less than 20%.

## 4. Delivery sequence and acceptance

### R0 — Reproduce and make evidence complete

**Deliverables**
- Save the baseline raw log and provenance; build focused reproductions from the reported histories.
- Add ownership/control/membership histories, population balances, per-type/class histograms, encounter-pair intervals and target-to-resolution chains.
- Report causes for every merchant loss, including explicit unknown/unresolved categories that fail the attribution audit.
- Include all event types in the histogram; distinguish initialization, simulation and exported rows/bytes.

**Regression cases to investigate**
- `ship_000003`: days 212/235; `ship_000012`: 271/293; `ship_000024`: 308/356; `ship_000009`: 354/361.
- `ship_000034`: day 281, reported events 41378/41400; resolve their canonical IDs from the raw log.
- Tarin's Gull: dissolution day 24 versus capture day 75.
- `ship_000012`/`ship_000050`: repeated weekly encounters; `ship_000039`: repeated targeting without visible contact.

**Gate:** each case has a minimal fixture or a documented raw-event explanation. Do not rerun a decade merely to reproduce a first-year defect.

### R1 — Atomic ownership and organizational lifecycle

**Deliverables**
- Establish authoritative legal ownership and explicit operational-controller links. Derived indexes must not become competing authorities.
- Capture validates current ownership/capability, transfers control atomically, resolves the old captain/crew, and invalidates or revalidates stale voyage/trade/combat orders.
- Define lawful dispositions for multi-ship crews, prize crews, recapture, abandoned hulls and successive same-tick claims. A second valid transfer must observe the first; stale duplicate claims must not mutate state.
- Extend the terminal-action audit to organizational actors and indirect commands, not just entities directly listed in event actors.

**Gate:** `T-OWNER-ONE`, `T-CAPTURE-TRANSFER`, `T-STALE-CONTROL`, `T-ORG-TERMINAL`, `T-OWNERSHIP-HISTORY`. Test capture→trade, capture→encounter, recapture and same-tick contention, plus save/load between transfer and next action. A dissolved organization cannot acquire or issue commands unless an explicit valid reformation occurred.

**First implementation checkpoint:** finish R0/R1 only, then publish a new one-year trace and ownership audit. Do not claim people/consequence repairs are complete at this checkpoint.

### R2 — Ship-loss disposition and population continuity

**Deliverables**
- Resolve ship loss into named/statistical survivors, drownings, missing persons and prisoners according to circumstances; not a forced death quota.
- Survivors retain identity, belongings/claims, injuries, ties and unresolved obligations. Assign a valid location or rescue/drift state; stranded captains can seek rescue, employment or replacement ships.
- Define missing-versus-dead transitions and prisoner custody transfers. Reappearance cannot create a second person.
- Keep crews alive without a hull when organization/resources permit; dissolution redistributes members rather than deleting them.

**Gate:** `T-SQUAD-CONSERVATION`: opening living population + births/arrivals − deaths/departures = closing living population, with an exhaustive non-overlapping disposition ledger. Check at each transition, not just year end. Cover named/L0 transfers, sinking, capture, rescue, desertion, dissolution and save/resume. No survivor trades before obtaining valid ship control.

### R3 — Crews, factions, names and historical identity

**Deliverables**
- Separate crew membership, faction allegiance and fleet control. Faction founding requires the actual A6 coalition/anchorage/purpose prerequisites.
- Dissolution requires organizational cause and accounts for assets, debts and members; one lost ship is not sufficient by itself.
- Validate temporal membership and command authority. Marines must have initial resources, commanders and actionable duties, not merely appear in late-year counters.
- Replace internal budget/vessel labels in display names with regional/organizational naming. Events retain stable IDs plus resolvable historical captain/crew references for chronicling.

**Gate:** `T-FACTION-DISTINCT`, `T-FACTION-TIMELINE`, `T-ORG-DISPOSITION`, `T-DISPLAY-NAMES`, plus existing A6 and marine gates at their applicable horizons. Renaming or captain replacement must not rewrite past event attribution.

### R4 — Meaningful narrative and bounded export volume

**Deliverables**
- Define a type-by-type classification policy and explicit consequential outcome predicates. A state change alone is not sufficient if it is bookkeeping churn (e.g. repeated rumor confidence updates).
- Count unchanged failed attempts, repeated grudge pressure and uneventful encounters as routine summaries. Preserve meaningful hostile acts even when repelled if they create material risk/cost or changed state.
- Stop feud amplification at its behavioral source: capability, reachability, travel, stakes and resolution/cooldown checks. Do not merely relabel an infinite action loop.
- Export per-type counts/bytes, narrative justification, repeat-pair distributions and meaningful-event gaps. Preserve debug raw logs and resolvable causes for aggregated exports.

**Gate:** `T-NARRATIVE-CONSEQUENCE`, `T-FEUD-NO-SPAM`, `T-CAUSE-RESOLUTION`; recalculate liveness/density/diversity using the tightened policy. Keep A1's 10-year ≥80% byte-reduction requirement and demonstrate the baseline/comparison method. Classification fixes may expose liveness failures; report them, do not restore spam to pass.

### R5 — Raider information, commitment and interception

**Deliverables**
- Raider estimates come from attributed observations/rumors with observation ticks, access limits, confidence, decay and seeded observer-specific error. Shared information may legitimately produce similar estimates; arbitrary noise is not a substitute for information boundaries.
- Target selection creates a persisted plan with target, last-known location, expected travel/cost, pursuit budget and expiry. End in contact, abort, escape, invalid target or failed interception, all causally linked.
- Spread targets through opportunity cost, distance, expected competition and commitments—not a hard arbitrary one-raider-per-target rule.
- Encounter eligibility follows actual route/time overlap. Raiders must be capable of following selected routes and attacking eligible merchants.

**Gate:** `T-RAIDER-INFO`, `T-TARGET-DISPOSITION`, `T-INTERCEPT-CAUSAL`, `T-TARGET-CONCENTRATION`. Controlled fixtures test uncertainty, stale information, competition, interception and abort; ordinary runs publish concentration and conversion distributions. No promise of a particular win rate from target-selection counts.

### R6 — Opportunity-driven scheduling and cadence audit

**Deliverables**
- Audit why pirates/privateers remain inactive early and why trade is sparse. Separate budget ramp-up, missing capability, liquidity, provisioning, route infeasibility and stale orders.
- Seed a viable initial world rather than waiting seven monthly recruitment cycles for minimum activity. All opening actors/assets still require explicit provenance.
- Trigger promotions on qualifying accomplishments; resource-backed recruitment responds to losses/opportunities. Persist staggered deadlines; use jitter only where uncertainty is meaningful.
- Report repeated pair encounters by interval, context and unchanged outcome. Legitimate patrol schedules are allowed; unexplained fixed-period rematches with no spatial opportunity are not.

**Gate:** `T-PROMOTION-DEEDS`, `T-RECRUIT-RESOURCES`, `T-EARLY-ACTIVITY`, `T-PAIR-CADENCE`. Include fixtures where no opportunity exists, so tests do not enforce a hidden monthly quota. Publish cadence thresholds and false-positive examples before treating the detector as acceptance evidence.

**Cadence audit policy (R6):** Include pairs with at least 3 encounters; report every interval in whole days, dominant interval share, encounter location sequence, outcome sequence, and stance sequence. A pair is an unexplained-periodicity *candidate* only when at least 80% of its intervals share one duration, all encounter locations are missing, every outcome is unchanged, and every stance signature is unchanged. Repeated encounters at one island or route are reported as observed spatial opportunity and are not flagged by frequency alone. Varying locations, outcomes, or stances are contextual repeats, not detector failures. False-positive fixture: four encounters every seven days at one island, even if the outcome repeats; it remains an observed co-location opportunity. Positive fixture: four seven-day encounters with missing locations and identical outcomes/stances; it is flagged for review, not asserted to be a proven simulation defect.

### R7 — Persistent consequences and productive economy

**Deliverables**
- Captivity has custody, provisioning, offers/demands and bounded next review; ransom transfers silver/prisoner ownership, while release/escape/execution changes actual state.
- Wounds affect capability and can heal, scar, disable or kill through configured severity/care; follow-up events reference their originating injury.
- Weather exposure affects actual routes, hulls, supplies and rescue risk. Disease affects people/squads through exposure, severity, care and population accounting. Not every storm or outbreak must kill.
- Measure merchant time spent trading, traveling, provisioning, repairing, stranded or blocked, with reasons. Attribute all losses and distinguish legitimate low-profit idleness from broken control.

**Gate:** `T-CAPTIVITY-RESOLUTION`, `T-INJURY-CONTINUITY`, `T-WEATHER-CONSEQUENCE`, `T-DISEASE-CONSERVATION`, `T-MERCHANT-UTILIZATION`, `T-LOSS-ATTRIBUTION`. Controlled causal tests establish mechanics; ordinary runs establish frequency without forced incidents. Preserve original coverage goals for long-run validation.

## 5. Validation ladder and stop rules

1. Minimal regression fixtures and state-machine/property checks for the current phase.
2. Snapshot round-trip and uninterrupted/resumed equivalence, including pending ownership/pursuit/custody transitions.
3. Same-seed deterministic replay. Behavior-changing repairs need a new documented baseline; exact equality to the buggy baseline is not required. Representation-only/export optimizations must preserve outcomes.
4. Seed-0 one-year raw log, readable trace and all applicable audits. Also run a predeclared small seed set to detect seed-specific fixes; record actual seeds and runtime.
5. **Review checkpoint:** ownership, headcount, organizational lifecycle, narrative quality and target disposition must pass before a decade run. Missing evidence is not a pass.
6. Only then profile representative early/late windows and attempt the unchanged long-run gates. No threshold relaxation, forced events, dropped wakes or unrelated process termination.

Stop to investigate if people/assets disappear, ownership diverges, terminal organizations command actors, causes become unresolvable, or meaningful activity is replaced by repetitive bookkeeping. Do not press ahead because aggregate counters are green.

## 6. Reports required at each checkpoint

- Run manifest: code revision/diff identity, config hashes, seed, director mode, horizon, commands, machine load caveats and elapsed/CPU/peak-memory measurements where available.
- Complete durable event log saved incrementally, including failure/interrupt preservation; canonical hash and record count.
- Readable timeline with named captains/crews, stable IDs in the audit appendix and an explicit filtering policy.
- Ownership/control ledger; crew/faction lifecycle and membership ledger; squad/population disposition balance.
- Per-type/class counts and bytes; consequential-event justifications; meaningful liveness/density; pair-cadence distributions.
- Raider selection→intercept/abort traces; merchant-loss causes; merchant utilization/blockers; consequences and unresolved obligations.
- Gate matrix: requirement → test/report → actual result → limitation. Use **pending**, **pass**, **fail**, or **N/A (horizon)**; never imply N/A means passed.

## 7. Integration with the original P0–P10 objective

- R0–R2 repair foundational A0–A3 behavior and add population-accounting prerequisites for squads.
- R3–R6 refine A1/A3–A7 and the decision/motivation infrastructure; they do not complete NPC, dialogue or game-interface milestones by themselves.
- R7 supports personal economy, consequences and A8 coverage. Full P0–P10 deliverables, meetable NPCs, handoff/write-back, original regression and long-run evidence remain required afterward.
- Record new evidence and any justified milestone revisits in the goal plan only when that goal is explicitly resumed. This Markdown plan is not a substitute for lifecycle state or completion evidence.

## 8. Current checklist

- [ ] R0 reproductions and baseline audits.
- [ ] R1 ownership/control and organizational lifecycle.
- [ ] First one-year ownership checkpoint published and reviewed.
- [ ] R2 survivor disposition and squad conservation.
- [ ] R3 crew/faction separation and identity.
- [ ] R4 narrative policy, behavior-loop repair and aggregation.
- [ ] R5 raider beliefs and pursuit resolution.
- [ ] R6 event-driven opportunities and cadence evidence.
- [ ] R7 consequences and merchant activity.
- [ ] Multi-seed one-year review gates pass.
- [ ] Representative profiles and decade acceptance, then remaining P0–P10 work.
