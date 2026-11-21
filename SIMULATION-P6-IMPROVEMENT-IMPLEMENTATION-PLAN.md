# Simulation P6 Improvement Implementation Plan

## 1. Purpose and status

Translate the verified P6 evidence bundle into a bounded implementation and verification sequence. The immediate blocker is actor-band acceptance in both director modes. The evidence now narrows the likely repair to captured hulls awaiting lawful prize-crew resolution, plus a smaller number of onboard/controller mismatches; do not repeat already-completed generic diagnostics work.

**Status: P1/P2 implementation and P3 lifecycle reassessment are complete for the scoped follow-up; overall acceptance remains incomplete.** The current-source 20-seed screens still fail the actor-band gate in both director modes, and the three-year seed-0 screen also fails actor bands despite passing its trade-stability gate. No threshold, recruitment cadence, or story outcome was forced. P4 evidence review is recorded below; ten-year gates remain N/A pending R0–R3 coherence review. This plan complements `SIMULATION-COHERENCE-REPAIR-PLAN.md` and `SIMULATION-STORY-AND-GROUNDED-COMBAT-EXPANSION-PLAN.md`; it does not replace their acceptance gates.

**Scope boundary:** keep the simulation deterministic, conserved, and non-magical. Do not add supernatural combat powers or use combat stats to mask actor-population failures. Do not force battles, wars, treasure, recognition, crew employment, or other story outcomes to meet quotas.

## 2. Evidence baseline

Bundle: `C:/Users/Warp/AppData/Local/Temp/opencode/sim-p6-p4-off-final/98c9ad0610c243f7b3fa4e4d3f0bfff6/P6-evidence-bundle.json`.
Run ID: `98c9ad0610c243f7b3fa4e4d3f0bfff6`; bundle SHA-256: `ce4cf8f3487ce9376a5a28d3b441b0a134993e6540f4de2a391964b04ed1cc10`; source revision: `7d7b9156c90c4da3e6200cf786b678c2abe41e0c`.
Primary screen: seeds 0–19, 365 days, director off. Additional screens: seeds 0–19 for one-year director on; one seed for three-year director-off trade stability.

| Finding | Observed result | Interpretation |
|---|---:|---|
| Actor-band acceptance | Fail in 20/20 director-off and 20/20 director-on seeds | Current primary blocker in both modes; configured thresholds were not relaxed. |
| Director-off liveness/density | Pass | Applies to the one-year director-off screen only. |
| Director-on liveness/density | Pass | Separate one-year director-on screen; it does not cure the actor-band failure. |
| Ledger, terminal-action, diagnostic gates | Pass in all listed runs | Bundle reports no ledger or post-terminal violations and full diagnostic integrity. |
| Director-off ship bands | Commandable merchants 12–18 vs min 32; pirates 7–12 vs 12; marines 4–8 vs 8 | Per-seed actor counts reconcile with the bundled JSON diagnostics. Privateers, major captains, and legends meet minima. |
| Physical merchant hulls | 21–31 vs min 32 | Hull totals are also low; this is not only a classifier/census discrepancy. |
| Main uncommandable-hull reason | Captured hull awaiting prize-crew resolution; smaller number of onboard/controller mismatches | Existing per-hull census already identifies ship, reason, capture/controller/owner, onboard crew, and crew dispositions. Follow up on candidate/arrival blockers only where the current evidence is insufficient. |
| Actor lifecycle audit | 80 quarterly hull transitions; 232 ship-loss member partitions | Both sets reconcile. Recruitment opportunities are all filled: merchant 600, pirate 260, marine 218, privateer 28 across the primary screen. |
| Natural story events | Treasure in all 20 primary seeds; zero battles, legend duels/recognitions, treaty breaches, and wars | Descriptive one-year frequencies only. Zero is not proof of unreachability and is not a quota target. |
| Three-year trade/actor screen | Seed 0 trade stability passes at 1.808×, but actor-band acceptance fails: merchants 19/32, pirates 10/12, marines 5/8, legends 1/2; privateers 2/2 and major captains 15/12 pass | Same run ID `2ccaecd6a0dd413eb4bdaf9ffecc068b`, result SHA-256 `ba566137afd497b91a642ced4625ec8a1eb407a945319034a27e1307e6c4cb47`. Trade acceptance is one seed only; do not treat its pass as an overall screen pass. |
| Ten-year turnover, political lifecycle, set-piece frequency, LOD, formal log-byte reduction | N/A | Defer until the specified R0–R3 coherence review; do not count as passes. |

### P1–P3 current-source follow-up evidence

- Evidence manifest: `C:/Users/Warp/AppData/Local/Temp/opencode/sim-p6-followup-evidence.json`, SHA-256 `47c8c7df1dbb74b346f9b1a0d917eb732c540d3ed3b13764fb9a4bc7459ac8a9`. It records the three screen result hashes, all 82 linked diagnostic JSON/Markdown hashes, seed manifests, gate statuses/reasons, source identity, and limitations. All diagnostic run IDs, seeds, and source hashes matched their parent results.
- Current source identity: git revision `7d7b9156c90c4da3e6200cf786b678c2abe41e0c` plus working-tree source SHA-256 `231298bc1540d88b2b215c348edd66e21cc42a961756b75a019b7bc16276a2cb` (the implementation remains uncommitted; do not identify it by revision alone).
- Scoped regression command from the repository root: `python -B -m pytest tests/test_sim_diagnostics.py tests/test_sim_crews_ships.py tests/test_sim_world_snapshot.py tests/test_sim_economy_data.py tests/test_sim_encounters.py tests/test_sim_battles.py tests/test_sim_politics.py tests/test_sim_decisions.py tests/test_sim_coherence_audit.py tests/test_sim_chronicler.py -q` → **239 passed in 290.90s**.
- Director-off one-year screen, seeds 0–19: run `efbb98b913ab41a0986f1a2551b18da5`; actor gate **fail 20/20**. Per-band passing seeds: merchants 0/20 (12–20 commandable), pirates 2/20 (8–12), marines 2/20 (4–8), privateers 20/20 (2), major captains 20/20 (14–15), legends 20/20 (2–3). Director-off liveness/density, ledger, terminal-action, and report-integrity gates pass; other longer-horizon gates retain explicit N/A statuses.
- Director-on one-year screen, seeds 0–19: run `3c7a8a8a5f29416ba0dcc7886ebd7884`; actor gate **fail 20/20** with the same per-band ranges. Director-on liveness/density, ledger, terminal-action, and report-integrity gates pass; longer-horizon gates remain N/A.
- Both one-year modes reconcile physical hulls by band at every quarterly checkpoint: **320/320** checks each satisfy `closing physical − opening physical = sourced recruitments − sinks`; another **400/400** checkpoints per mode reconcile `physical − commandable` to the classified uncommandable reasons (all captured-pending-prize, no other reason). Across 20 seeds, resource-backed recruitments/sinks were merchant 600/110, pirate 260/38, marine 218/93, privateer 29/5. Every recorded recruitment opportunity was filled with no blocked opportunity. Mean year-end physical/commandable counts were merchants 27.5/16.5, pirates 12.1/10.05, marines 7.25/5.85, and privateers 2.2/2.0. Thus merchant and marine deficits include physical-hull deficits, not only misclassification or stale control.
- At year end, 293 hulls across the 20 seeds remained pending prize-crew assignment (9–21 per seed); every uncommandable hull was classified as captured-pending-prize. Blocker observations overlap: 286 had no same-location arrival observed since capture, 117 had no lawful captain candidate at the checkpoint, and one had a same-location arrival whose vessel was not currently commandable. There were **zero** `onboard_crew_is_not_controller` mismatches at year end in either one-year mode. Do not sum overlapping blocker counts as unique hull counts.
- Three-year director-off seed-0 screen: run `cfd68121a2324f85998c27f0830dfdd6`; trade stability **passes at 1.417×** while actor bands **fail** (merchants 23/32, pirates 10/12, marines 6/8, legends 1/2; privateers 2/2 and major captains 15/12 pass). Physical hull changes reconcile by band at all **48/48** checkpoints: merchant 3+91−40=54, pirate 1+38−27=12, marine 1+35−29=7, privateer 1+12−10=3; `physical − commandable` classification reconciles at 52/52 checkpoints. The final 35 pending-prize hulls have explicit blockers; no controller mismatch remains. This single-seed trade result is preliminary, and trade and actor gates are independent.
- The P2 multiple-ship-crew scenario remains unsupported by the authoritative model (`validate_state` rejects a crew with more than one ship ID). A regression preserves that existing invariant; no multi-ship authority semantics were invented. Address it only with a separate design basis.

### Evidence integrity

The bundle SHA-256 and all 44 linked outputs (bundled/external primary results, both additional-screen results, and 40 JSON/Markdown diagnostics) were verified; per-seed primary actor counts reconcile to their JSON diagnostics. The earlier stale shared-report concern is resolved for this bundle; do not carry forward the previous `reports/diag_0.md` mismatch as a finding against this run. **Reporting gap:** the `limitations` array mentions the one-year actor failures and three-year trade pass but omits that the three-year screen's own actor-band gate also fails; use the structured `acceptance_gates` records and linked result as authoritative, and require future bundle summaries to mention every failed applicable gate. Bundle-reported verification was 237 passed in 258.73s, with pending-deadline snapshot and three-year CLI regressions passing and `git diff --check` passing with expected CRLF warnings; these commands were not rerun during this review.

## 3. Existing implementation boundaries

- Actor ship-band bounds are in `sim/data/config.json:73-83`.
- The shared pure actor classifier is already present at `sim/core/actor_bands.py:15`; recruitment uses it at `sim/systems/crews_ships.py:1260-1276`. Do not spend the next phase rebuilding this classifier absent a new mismatch.
- Actor lifecycle census and transition/report-integrity evidence are emitted by `sim/tools/batch_test.py:239,401,879,1175` (line references are for the reviewed source revision).
- Actor recruitment is persisted-deadline-, island-, and provisioning-dependent at `sim/systems/crews_ships.py:1148-1232`; the active commandable-hull count is at `:1260-1276`. All listed opportunities filled, so blindly increasing recruitment cadence is not the evidence-led first fix.
- Real commanded arrivals call prize-crew installation at `sim/systems/crews_ships.py:1111-1120`; candidate selection, local co-location rules, and title/controller transfer are in `sim/systems/encounters.py:956-1025`.
- The coherence gates for ownership/control, ship-loss disposition, and crew/faction identity are R1–R3 in `SIMULATION-COHERENCE-REPAIR-PLAN.md:61-91`. Use those gates rather than inventing a parallel ownership model.
- Relevant tests include `tests/test_sim_diagnostics.py`, `tests/test_sim_crews_ships.py`, `tests/test_sim_encounters.py`, and `tests/test_sim_world_snapshot.py`.

## 4. Non-negotiable rules

1. Do not relax configured minima/maxima merely to turn the gate green. Any proposed target change requires a separate evidence-backed design decision.
2. Count operationally commandable ships consistently. Report physical hulls, living crews, active commands, legal owner, controller, and band separately so a low count has an explainable cause.
3. Every new ship, crew, captain, supply, and treasury balance has a valid source, cost, and causal event. Respect existing local resource/provisioning constraints.
4. Preserve persisted actor deadlines, deterministic replay, and snapshot compatibility. Do not add shared quota batches or perturb unrelated RNG streams.
5. Do not treat a zero frequency in a 20-seed one-year screen as proof an outcome is impossible. Keep controlled reachability fixtures separate from ordinary-run frequency.
6. Do not coalesce decision wakes, suppress consequences, or rewrite chronicles to improve output metrics.
7. No magic, supernatural powers, forced combat outcomes, forced recognition, or artificial event quotas.

## 5. Implementation sequence

### P0 — Lock the verified baseline (evidence task complete)

**Evidence:** bundle run ID, result copies, 40 diagnostic hashes, and per-seed actor counts reconcile. The lifecycle audit confirms all 80 quarterly hull transitions and all 232 ship-loss member partitions. The three-year linked result is also hash-verified and shows a separate actor-band failure despite its trade-stability pass. This closes file-integrity review for this bundle only; it does not close actor-band acceptance or the bundle's limitation-summary omission.

**Do not redo:** generic per-seed actor-count instrumentation, report run identity, or a shared classifier are already present in the reviewed source/bundle. Revisit only if a future output fails the report-integrity gate.

### P1 — Resolve captured hulls through lawful prize-crew opportunities

**Targets:** `sim/systems/crews_ships.py:1111-1120`, `sim/systems/encounters.py:956-1025`, actor-population census in `sim/tools/batch_test.py`, and encounter/crew/snapshot tests.

**Problem:** physical merchant hulls remain 21–31 and commandable merchant ships 12–18 against a minimum of 32. The bundle identifies captured hulls pending prize-crew resolution as the dominant exclusion. Existing resolution is tied to an actual commanded ship's island arrival and a lawful candidate; preserve that causality.

**Deliverables**

- Start with the existing per-hull census (capture event, legal owner/controller, onboard crew, reason, member dispositions). Extend it only for missing resolution evidence: location/transit status, eligible captain candidates, last real arrival opportunity, and exact reason no installation occurred.
- Break blockers down without collapsing distinct states: no eligible commanded arrival, different location, hull in transit, no lawful/qualified candidate, stale or missing crew linkage, invalid owner/controller, or installation attempted but rejected.
- Audit candidate selection among the captured crew, captor crew, and local candidates. Preserve identity, faction succession, wallet/resource handling, lawful title, and existing authority boundaries; do not generate free captains or crews.
- Repair only demonstrated missed transitions or opportunity visibility. Keep the ship pending if no lawful candidate/opportunity exists; never mark it commandable merely to satisfy the census.
- Add focused fixtures for successful installation, no-candidate pending state, location/transit exclusion, captain/faction succession, repeated arrivals/idempotency, capture then recapture, and a snapshot saved before the resolving arrival.
- Assert every install has causal links to the actual arrival and prize assignment, changes controller/title atomically, leaves no duplicate crew membership, and passes commandability through the shared classifier.

**Gate:** each pending hull has one explainable state; eligible opportunities resolve or carry a specific blocker; no duplicate acquisition/installation events; person/asset/ledger partitions reconcile; replay after snapshot matches uninterrupted execution.

**Follow-up:** implemented the per-hull blocker audit and focused successful-installation, pending/no-candidate, wrong-location, in-transit, repeated-arrival, capture/recapture, succession, and pre-arrival snapshot fixtures. The current one-/three-year evidence shows no checkpoint hull in the “eligible arrival and candidate observed, but still pending” state; unresolved hulls retain explicit arrival/candidate/controller/linkage blockers. The scoped suite passed 239 tests. The 20-seed screens still show naturally pending hulls; this is an explainable acceptance limitation, not a reason to force a later arrival or add a candidate.

### P2 — Repair residual onboard/controller mismatches

**Targets:** `sim/core/ownership.py` commandability/title transfer, `sim/systems/encounters.py` capture/recapture paths, `sim/core/actor_bands.py:15`, `sim/tools/batch_test.py` census, and R1 ownership tests.

**Deliverables**

- For each `onboard_crew_is_not_controller` case, capture ship ID, crew aboard, authoritative controller, legal owner, last ownership/control event, and whether the mismatch is intentional or stale.
- Trace each mismatch to the capture, title assignment, crew transfer, or ship-loss transition. Do not infer controller from crew-onboard or owner type.
- Fix the smallest inconsistent transition using the authoritative title/controller assignment path; preserve legal ownership when only operational control changes.
- Add regressions for capture→arrival/prize installation, recapture, multiple-ship crews, terminal/dissolved actors, and save/load between transition and next action.

**Gate:** no unexplained mismatch in the repeated 20-seed screens; R1 `T-OWNER-ONE`, `T-CAPTURE-TRANSFER`, `T-STALE-CONTROL`, and `T-OWNERSHIP-HISTORY` remain satisfied; actor census and recruitment count remain aligned.

**Follow-up:** the traced mismatch was a dissolved legal owner receiving the title of an asset hull while its already-valid onboard crew lost control. Employment asset transfer now preserves that living onboard authority while transferring legal title, with causal ownership evidence and a regression. Both 20-seed year-end audits report zero onboard/controller mismatches. Capture/arrival, recapture, idempotency, and snapshot regressions pass. Multi-ship crew semantics are explicitly not implemented: the current one-hull-per-crew invariant is regression-tested, and expanding it requires separate design approval.

### P3 — Reassess actor-band maintenance after lifecycle repairs

**Targets:** `sim/systems/crews_ships.py:1148-1232`, `sim/data/config.json:73-83`, transition census, recruitment tests.

**Deliverables**

- Re-run per-quarter physical/commandable counts after P1/P2 to quantify how many hulls return to lawful command and how many remain lost, sunk, or uncommandable.
- Reconcile opening population + sourced recruits + ownership transfers − losses/retirements with closing physical and commandable counts by band.
- Keep current staggered deadlines, local provisioning checks, and separate band accounting unless measured transition evidence shows a specific cadence defect. The bundle records 1,106 successful recruitment opportunities across the primary seeds and zero blocked opportunities; do not simply raise cadence or add quota batches.
- If a band remains below minimum after prize-crew/controller defects are fixed, diagnose physical loss and local candidate/resource constraints before proposing any narrowly scoped replacement opportunity.
- Keep all max bounds active; test that ownership changes cannot double-count one hull across bands and that no count includes archived/sunk/non-commandable ships.

**Gate:** both controller and diagnostic classifier agree at every checkpoint; every residual deficit/overage reconciles to named transitions; no configured band threshold is weakened and no asset/person/resource is minted without a valid source and cause.

**Follow-up:** controller/diagnostic endpoint integrity passed in every run. At every checkpoint, physical hull changes reconcile per band to sourced recruitments minus sinks (320/320 checks in each one-year mode; 48/48 in the three-year run). All recorded recruitment opportunities were filled. However, the actor-band acceptance gate still fails in all 40 one-year runs and in the three-year seed-0 run; year-end merchant and marine physical populations remain below their minima. No cadence change is justified by the observed filled opportunities, and this phase does not claim actor-band acceptance.

### P4 — Preserve story reachability and horizon-qualified acceptance

**Targets:** `sim/tools/batch_test.py` acceptance gates, set-piece eligibility diagnostics, controlled reachability tests, and `SIMULATION-COHERENCE-REPAIR-PLAN.md` R0–R3 validation ladder.

**Deliverables**

- Keep one-year natural-event counts descriptive. The current zero counts for battles, legend duels/recognitions, alliances, and wars are not grounds for event quotas or forced outcomes; retain controlled reachability fixtures as separate evidence.
- Keep treasure's observed 20/20 occurrence as a sample result, not a minimum-rate target. Do not increase treasure or conflict frequency to make reports look busier.
- Preserve explicit `pass`/`fail`/`N/A` statuses and reasons across every screen. The director-on liveness/density pass is mode-specific. The original one-seed, three-year trade result (1.808×) and current follow-up (1.417×) are both preliminary; each actor-band gate fails. Never summarize either run as passed based on trade stability alone. Ensure top-level bundle limitations enumerate every structured failed gate.
- Review R0–R3 coherence gates before any ten-year turnover, political lifecycle, set-piece frequency, LOD, or formal event-byte-reduction run. N/A remains N/A until its horizon and prerequisites are met.

**Gate:** controlled reachability remains intact, ordinary-run frequencies are not forced, and no N/A horizon gate is represented as a pass.

## 6. Verification ladder

Run from `E:/Pirates of Miska` after implementation, preserving unrelated working-tree changes:

1. Focused tests from `E:/Pirates of Miska`: `python -B -m pytest tests/test_sim_diagnostics.py tests/test_sim_crews_ships.py tests/test_sim_encounters.py tests/test_sim_world_snapshot.py -q`.
2. Ownership/coherence tests: `python -B -m pytest tests/test_sim_coherence_audit.py tests/test_sim_encounters.py tests/test_sim_crews_ships.py tests/test_sim_world_snapshot.py -q`.
3. Replay checks: compare uninterrupted execution with save/resume before a pending-prize arrival and before a persisted actor deadline.
4. Run seeds 0–19 for one year with director off and on, each into a unique `--output-dir` under `C:/Users/Warp/AppData/Local/Temp/opencode/`; verify full actor vectors, report integrity, hashes, and per-seed diagnostic reconciliation.
5. Review pending-prize blocker distributions, controller mismatches, and physical/commandable transition ledgers before any cadence changes.
6. Re-run the three-year seed-0 screen for trade stability and actor-band status; both gates must be reported independently, and the single-seed result remains preliminary.
7. Only after R0–R3 review, run unchanged ten-year acceptance gates; record runtime, machine-load caveats, hashes, and incomplete checks.

Do not claim full acceptance from focused tests, one-year frequencies, or output-compaction percentages alone.

## 7. Completion criteria

- [x] Bundle identity and all 44 linked output hashes reconciled; per-seed primary actor counts match diagnostics for run `98c9ad0610c243f7b3fa4e4d3f0bfff6`.
- [x] Captured-hull prize-crew opportunity blockers are explicit; tested installations retain causal arrival/assignment/title/controller links and deterministic pre-arrival replay. Naturally pending hulls remain explainable rather than being forced commandable.
- [x] The reproduced onboard/controller mismatch cause has regression coverage; no such mismatch remains at the final checkpoints of the repeated 20-seed screens. Multi-ship crew expansion remains outside current-model capability and is documented above.
- [x] Physical actor counts reconcile from opening through resource-backed recruitment and sinking by band at every sampled checkpoint; recruitment opportunities are filled and thresholds remain unchanged. Commandable counts still fail actor minima, so the acceptance failure is explicitly retained.
- [ ] Director-off and director-on 20-seed actor-band gates pass without forced recruitment or story outcomes; the three-year endpoint actor-band gate also has no unexplained failure. Current evidence: 20/20 failures in each one-year mode and a three-year seed-0 actor-band failure, despite its independent trade-stability pass.
- [ ] Conservation, snapshot replay, deterministic continuation, terminal-actor rules, and R1–R3 coherence gates pass.
- [ ] Zero/rare battle, legend, alliance, or war frequencies remain descriptive; controlled reachability is tested separately.
- [ ] `N/A (horizon/config)` remains distinct from pass/fail and longer gates run only after foundational review.
- [x] Final report, seed manifest, source identity, all 82 diagnostic hashes, gate outcomes, limitations, and test evidence are recorded under evidence ID `sim-p6-followup-231298bc1540` in the linked manifest and this plan.
