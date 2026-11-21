# Combat Injury, Mental Stats, NPC Response and Resolution Clarity Plan

**Status (Nov 21, 2026):** historical record of the Nov 20 stat and injury/mental planning. The user subsequently approved the universal threshold/anatomy behavior design in `COMBAT-MENTAL-BEHAVIOR-AND-ANATOMICAL-INJURY-EXPANSION-PLAN.md` §10 and started implementation. This document remains the source for previously approved mental-stat formulas, bleed tuning and unrelated Dash+Slash history, but its unapproved/personality-specific behavior proposals in §6 and later behavior gates are superseded by the newer controlling plan. Do not treat this historical text as the current behavior spec.

**Scope:** specification duel (`pirates/duel_system/`), its config/save migration, UI and tests. Keep it separate from legacy campaign combat. Preserve deterministic replay, one voluntary action per fighter per exchange, collision safety, shared-snapshot AI/privacy, and save compatibility.

## 1. User requirements

- Separate the existing **Dash+Light** (automatic forward approach followed by a Light attack) from a new **Dash+Slash**: retreat/backpedal while still able to land a Light slash on the way out. Do not relabel or replace Dash+Light. The UI should name the different tactical jobs accurately.
- One wound's continuing bleed rate is not itself a severity meter. A wound that bleeds continuously should be difficult to cause, have consequences proportionate to that seriousness, and drive blood loss/physical/mental consequences. Reconsider the current common CUT/DEEP rates; do not silently multiply every existing wound or treat `3/turn` as an escalating injury tier.
- Mental stats first: baseline fear relates to a fighter's pre-fight confidence; in-fight fear of death rises from repeated hits, pain and bleeding. Fear reduces effective Speed and Accuracy. Anger rises from being hit, attacks being deflected, an opponent repeatedly fleeing/circling, injury, bleeding and pain; anger tolerance controls the gain. Anger increases Speed/Strength, reduces Accuracy, and harms balance/footing. Nervousness is not a separate mental stat; fold/migrate it into the agreed model. Pain tolerance governs how much pain disrupts focus; pain is a source of fear and anger.
- After stats are designed and implemented/verified, specify how NPC behavior responds to injuries, pain and mental state, with personality-specific choices. No retreat/escape/fear/anger/nervousness behavior changes before the behavior phase is reviewed and explicitly approved.
- For ordinary hit resolution, explain why one fighter hit and the other was blocked, parried, clashed, interrupted or missed. Keep the readout compact: one headline number (resolved post-armor wound force) and one concise causal prose sentence that connects attack/defense to the actual result. It need not restate every rule or dump a ledger.

## 2. Source audit — pre-Phase-A mental model and gaps (schema21 snapshot)

### Existing structures
- `content/duel_system.json:5,8,11-13,102` has morale, anger, fear-from-morale settings, nervousness decay, pain severity/zone gains, and personality pressure weights. Current schema is 21.
- `model.py:284-315,386-433` has personality traits (`aggression`, `discipline`, `confidence`, `patience`, `adaptability`), `composure`, `courage`, and fighter `pain`, `anger`, `morale`, `nervousness`, and fear-break counters. There is no Pain Tolerance or Anger Tolerance stat.
- `stats.py:33-36` computes fear only as `(morale_center - morale) * morale_scale - anger_offset * anger`, clamped. It has no fear accumulation pool. `stats.py:48-88` makes pain and anger change effective Speed/Strength; Accuracy stays unchanged.
- `mental.py:21-33,36-65,68-88` changes anger, reduces morale once at blood thresholds, initializes morale from courage/level gap, and yields an NPC after three decisions at the fear threshold. `fear_decision` runs on actual NPC decisions.
- `wounds.py:374-384,449-519,610-615` increases pain/fatigue/anger on injury, changes anger after whiffs/parries/circling/unanswered hits, and changes morale on wounds/hits. `engine.py:397-417` decays pain and nervousness; there is no nervousness gain in the duel-system source search.
- `ai.py:310-321,394-535` mixes fear, pain and nervousness into decision control/pressure and gives fear/pain conditional action scores. It can score a visible bleeding opponent coarsely in group combat, but no pain-tolerance or injury-specific behavioral model exists. Never inspect hidden group pools/wounds to personalize AI.

### Exact mismatches to the user's requested stat contract
- Current config sets `stats.fear_accuracy=0`, `anger.acc_penalty=0`; `config.py:72-82` rejects nonzero fatigue/pain/fear/anger Accuracy effects to enforce the historical invariant. Fear has no Speed multiplier. This directly conflicts with the new request and needs an explicit spec/config migration; do not preserve the old invariant accidentally.
- Anger already gains from several causes and gives configured Speed/Strength bonuses (current 0.15/0.25 at the pool maximum), but has no tolerance stat, the Accuracy penalty is zero, and current gain sources do not cover every requested trigger. It also suppresses some pain penalty and has an anger-crash rule.
- Current pressure treats nervousness as an independent input, although it only decays and is restored from pre-fight state. A deliberate migration is needed; don't leave it double-counted after merging it into fear.
- Personality `confidence` exists as a 0..1 trait, but initial fear is presently derived from `morale` initialized with `courage`; don't stack confidence, courage, morale and fear into four redundant pressure meters.
- Current one-number-per-stat state, event recording and migration need review before changing save fields. Store only the minimal persisted mental pools/traits; derive effective multipliers without mutating state during rendering or AI scoring.

## 3. Approved stat design (Phase A; user approved Nov 20, 2026)

Keep stable 0..100 combat pools, but make their meanings independent and observable:

1. **Confidence / baseline fear:** authored personality confidence is a pre-fight trait in [0,1]. Start combat fear at `100 × (1 − confidence)` (optionally blend a separately authored courage value only if the user confirms it is still wanted). This is starting fear, not a second morale loss.
2. **Fight fear:** one persisted fear pool, clamped [0,100], rises from discrete witnessed/experienced events: being hit, accumulated pain milestones, positive ongoing bleed / meaningful blood lost, dangerous severe injury, and credible imminent lethal threat. Avoid adding the same event twice (e.g. one HIT and its WOUND are one cause). Fear rises with repeated/serious causes, not with every idle tick. Safe time, successful defense/escape, opponent defeat or treatment may reduce it using bounded config values; agree on which before coding.
3. **Fear modifiers:** explicit tunable `fear_speed_mult` and `fear_accuracy_mult` apply monotonically as fear rises. Use a nonzero floor, so high fear impairs but does not hard-lock. Accuracy remains the authored base skill; effective Accuracy for hit probability is derived, displayed as condition-degraded, and never rewritten. Add mirror/monotonic tests from fear 0→100 and config migration tests.
4. **Anger and Anger Tolerance:** retain an anger pool. Add a 0..1 authored tolerance/calming trait. Candidate gain formula: `raw cause anger × (1 − tolerance × configured suppression)`, with explicit config/event sources for received hit, blocked/parried own attack, witnessed repeat fleeing/circling, injury severity, pain, and active bleed/blood loss. Avoid counting both pain and its originating injury as full duplicate anger. Anger declines slowly or after a clear calming/safe interval. Do not make tolerance itself grant free accuracy.
5. **Anger effects:** explicit bounded Speed/Strength buffs, Accuracy penalty, and Stability/balance degradation all scale from the normalized anger pool. At very high anger, the same stability/footing pressure increases fall risk; it must not bypass collision/legality. Keep any anger-crash rule only if it is still desired after the new curves are tested.
6. **Pain and Pain Tolerance:** pain remains a condition generated by actual contact, not a personality emotion. Add an authored 0..1 tolerance. Recommendation: preserve a raw pain burden for the HUD and injury record; derive `effective_pain = raw_pain × (1 − tolerance × suppression_cap)` for focus/capability and fear/anger stimulus. A severe wound remains severe and visibly painful even for a stoic fighter. Avoid double-counting by deciding whether pain penalties remain direct after fear/anger multipliers; compare (a) direct reduced penalties plus emotion stimulus, versus (b) pain only drives emotions. Prefer (b) or very small direct residual to prevent triple Speed/Accuracy loss.
7. **Nervousness:** retire as an independent duel stat. During save migration map legacy nervousness into starting fear using a bounded monotonic mapping, then serialize no second live pressure source. Keep a compatibility read for old sessions; preserve deterministic continuation and journal round trips.

The user approved this model; the initial tunings below are configurable starting values, not a claim of final balance. Run matched stat sweeps and inspect effects on hit rate, action timing, stability, bleed survival and fear-break. Do not claim balance from isolated formula tests.

## 4. Injury catalog — existing possible outcomes (not a claim of medical completeness)

Resolved broad zones are HEAD, EYE, TORSO, ARM, HAND and LEG (`model.py:17-23`); possible contact classes are CUTTING, PIERCING and BLUNT (`content/duel_system.json:27-31`). Severity classes are BRUISE, GRAZE, CUT, DEEP and SEVERED (`model.py:26-31`). Anatomical subsite catalogue (`content/duel_system.json:16`) includes scalp/forehead/skull/neck/face; left/right eyes; chest/ribs/abdomen/flanks/back; shoulder/upper arm/elbow/forearm/wrist; palm/fingers/thumb/wrist; hip/thigh/knee/shin/calf/ankle/foot. Side is persisted when applicable.

| Zone / case | Existing outcomes and configured effects |
|---|---|
| Any zone, net ≤ 0 | BRUISE / contusion. No continuing wound object or bleed. Pain uses `bruise_power × attack power`, capped by `bruise_cap`; impact can still affect stability only where configured impact rules allow. |
| Any zone, graze band 1–7 | GRAZE. No continuing bleed; one-off blood loss is currently 0.25 × contact bleed multiplier. Pain is 6 × zone multiplier. HEAD graze has DIZZY 40 ticks. |
| TORSO | CUT bleed 3/turn; DEEP bleed 6/turn plus WINDED and 20 stamina loss. Pain base CUT 14 / DEEP 28. Structures include skin, muscle, torso wall/ribs or contusion by contact class/depth. No torso organ-specific/internal-organ/crippling outcome is currently catalogued. |
| LEG | CUT bleed 2/turn, SLOWED ×0.75 for 120 ticks; DEEP bleed 4/turn, SLOWED_DEEP ×0.6 and no Dash. Heavy DEEP net ≥24 can cripple/incapacitate. Foot/ankle CUT adds painful footing/reduced speed; DEEP adds tendon/footing impairment and no Dash. Pain base 14/28. |
| ARM | CUT bleed 2/turn, weak grip/Strength ×0.8 for 100 ticks; DEEP bleed 4/turn, Strength ×0.65. Pain base 14/28. |
| HAND / wrist | CUT bleed 2/turn, Strength ×0.75, parry penalty +2 for 100 ticks; DEEP bleed 4/turn and may disarm; qualified clean Heavy CUTTING wrist net ≥22 can SEVER, bleed 8/turn, permanent missing hand and weapon drop if weapon hand. Supporting-hand loss differs from weapon-hand loss. Pain base 14/28/45. |
| HEAD | CUT bleed 3/turn, DIZZY/Speed ×0.85 for 60 ticks; DEEP bleed 6/turn, CONCUSSED for 100 ticks and stagger 12 ticks. Head net ≥24 causes unconsciousness. Qualified clean Heavy CUTTING NECK net ≥42 decapitates and kills. Pain zone multiplier 1.3. |
| EYE | CUT bleed 3/turn; DEEP bleed 6/turn. Only clean, undrifted eligible CUT/DEEP/SEVERED contacts add post-armor force to the persisted trauma meter; configured threshold 24 impairs one eye, overflow carries to the other. One-eye impairment currently does not lower Accuracy; bilateral impairment blocks precise Eye targeting. Pain zone multiplier 1.5. |
| Blood loss / terminal | Wounds sum to a configured bleed cap of 20/turn; bandage reduces each source by 60%. Blood below 50/25 applies morale thresholds; blood collapse threshold 15 and death/terminal checks live in engine. Graze's one-off loss and exertion-scaled continuing loss are separate. |
| Reflex / balance | Contact class/severity/zone derive shock; composure/decision-control contributes susceptibility. HOLD/FLINCH/RECOIL response uses configured thresholds; user confirmed FLINCH and RECOIL mean the same collision-safe one-tile backward response. Repeated heavy contact may stagger, impact reduces stability, and falls/ground actions exist. These are separate from anatomical wound severity. |

**Important injury gaps/questions:** no broken bones as their own persistent wound class; no organ-specific torso injuries, fracture-specific recovery, infection, long-term healing/scars, or treatment quality system. Armor currently subtracts zone protection before severity, bleed and permanent injury. Bleed rates are zone/severity constants multiplied by damage type and capped in aggregate, so high-force and repeated wounds may each show the same `3/turn`; do not infer that current rate alone conveys severity. Audit all WOUND/STATUS/SEVER/EYE_INJURY/DECAPITATION/INCAPACITATED/FIGHT_END event paths before deciding what to add.

## 5. Injury/bleeding redesign recommendation (Phase B; after Phase A)

- Make wound seriousness depend on resolved post-armor force, contact class, site/structure, and existing wound state—not simply on how many times the same per-zone rate was assigned. Keep the single-hit cause and cumulative wound burden distinguishable.
- Replace the flat, low per-wound bleed bands only after deciding a coherent small tier ladder: superficial/no continuing bleed; meaningful wound/limited continuing bleed; serious wound/high bleed plus direct function loss; catastrophic wound/critical blood-loss clock. Name units and show the rate as a causal consequence; don't add a hidden “injury score.”
- Use actual blood removed per turn, pressure/exertion and wound count to drive severe-state consequences. A serious bleed should materially shorten the time to collapse and have explicit treatment urgency, but never add an unannounced death timer.
- Keep pain proportional to wound/contact and zone; apply Pain Tolerance to burden/effect as defined in Phase A. Ensure force/armor, pain, blood loss, function, reflex, and mental-pool causes are emitted as structured events so compact prose can summarize them.
- Define whether repeated contact worsens an existing wound, creates a separate wound, or both. Avoid stacking unlimited duration/status multipliers; define wound caps/merge rules, treatment, save/load semantics, and deterministic ordering.
- Preserve configured special outcomes (head unconsciousness, neck decapitation, leg cripple, hand severance, cumulative eye impairment) unless an explicit design decision changes them. No change to eye targeting/medical mechanics merely to make every Eye hit cause injury.

### 5.1 Reviewed starter rates (user approved Nov 20, 2026; schema 23 implemented)

The current table begins continuing bleed at ordinary CUT severity (net 8), which explains why a relatively routine clean contact can display a flat 2–3 blood/turn. Keep CUT injuries functional/painful, but make their continuing bleed minor; reserve major rates for DEEP and structural injuries. Candidate base rates per in-game turn, before existing damage-type/exertion/bandage multipliers:

| Severity / zone | Current | Proposed starter | Intended distinction |
|---|---:|---:|---|
| CUT: torso/head/eye | 3 | 0.5 | Small persistent loss; not a serious bleed by itself. |
| CUT: arm/hand/leg | 2 | 0.5 | Same minor-bleed tier; function/status still differs by site. |
| DEEP: torso/head/eye | 6 | 8 | Serious ongoing blood loss, pain and current deep function effects. |
| DEEP: arm/hand/leg | 4 | 6 | Serious bleed plus current limb/grip/footing consequences. |
| Hand SEVERED | 8 | 12 | Catastrophic bleed from a rare net≥22 clean Heavy wrist severance. |
| GRAZE / BRUISE | 0 ongoing | 0 ongoing | Preserve current one-off graze loss / blunt contusion behavior. |

With current action exertion (idle 0.7, fight 1.3, dash 1.6), one unbandaged torso CUT at 0.5/turn takes roughly 131 fighting turns to drop blood from 100 to the 15 collapse threshold. A single DEEP torso wound at 8/turn takes about 8 fighting turns (15 idle turns); a hand severance at 12/turn takes about 6 fighting turns. Actual loop applies the rate across 10 ticks, sums wounds to 20/turn before exertion, and bandaging removes 60% of each treated source. This gives a visible separation between a lingering minor cut and a serious time-sensitive wound without inventing a new death timer.

The user explicitly approved these rates as configurable starters (“Approve proposal”). This is not a realism claim or final balance acceptance. Schema 23 applies these defaults when upgrading prior shipped defaults while preserving custom tuning; active wound instances with prior shipped rates are migrated as well. Verify concrete blood/time/status outcomes and treatment in tests and native UI before making balance claims.

## 6. Superseded NPC behavior proposal (historical only)

**Authority note (Nov 21, 2026):** the proposal below was superseded when the user approved the universal behavior design in `COMBAT-MENTAL-BEHAVIOR-AND-ANATOMICAL-INJURY-EXPANSION-PLAN.md` §10. Do not implement its named-profile response branches; retain only compatible invariants such as legality, observable evidence, shared snapshot and action limits.

Use an injury × mental-state × personality decision table; first compute/verify stats without changing behavior, then review this table before coding:

| Observed/experienced state | Candidate choices (only if legal and one action) | Personality expression / safety |
|---|---|---|
| Low pain, no continuing bleed, no function loss | Continue current tactic; guard/counter when personally disciplined; aggressive profile can press. | Confidence/aggression affect score preference, never action legality or hidden-state access. |
| Repeated hit/pain, fear rising | Guard, evade/reposition, interrupt, or continue if confident/trained; high fear may eventually yield under a separately approved threshold. | Recruit/low-confidence reacts earlier; Veteran/high discipline maintains deliberate defense; confidence/tolerance alter stats independently of profile choice. |
| Own active bleed / falling blood | Bandage only if safe enough, create distance/guard, or press if high anger/commitment. | Treatment urgency based on actual own bleed/blood; no unsafe free bandage. Group AI sees only its own exact wounds and witnessed coarse enemy cues. |
| Functional ARM/HAND wound | Re-score actions using effective strength/grip/parry and remaining legal weapon use; protect the injured side or switch tactic. | Profile weights choose between legal alternatives; absent limb/site attacks are never offered. |
| Functional LEG/FOOT wound | Avoid costly runs/dashes, guard/hold ground or take a legal exit; don't score an impossible movement route. | Fear/discipline/tolerance change preference; effective Speed/no-Dash status remains the mechanics authority. |
| HEAD concussion/stagger/unsteady | Current commitment obeys existing interrupt/stagger rules; next choice favors guard/recovery/safe spacing or a desperate attack according to approved traits. | Do not make already unconscious actors choose. |
| One/both eyes impaired | Use only current vision/targeting legality and actually witnessed impairment; avoid precise Eye attacks when impossible; choose broad legal zones if vision rules permit. | Do not infer a hidden injury from state. No extra Accuracy penalty unless an approved stat rule explicitly adds one. |
| Unconscious, decapitated, dead, blood-collapse/terminal | No new action. Resolve the engine's terminal state and report it. | No AI override, rescue, retreat or emotional score may resurrect/act after terminal. |
| Opponent repeatedly disengages/flees | Anger trigger may rise only from repeated observed evasion/flight; legal pursuit, cut-off, guarded patience, or stop pursuit if unsafe. | Tolerance reduces anger gain; no teleporting, free extra action, or omniscient route prediction. |
| Own attack parried/deflected | Anger trigger; next legal decision can vary between feint, change line, guard/evade, or renewed attack. | Counterfighter/Veteran can learn from resolved public outcome; Brute/Aggressor can press more. No reaction inside the already committed exchange. |

Personality proposal after stat contracts: use existing traits plus the user's new confidence/anger-tolerance/pain-tolerance (or explicitly map existing confidence/composure rather than duplicate them). Recommend contrasting profiles: Recruit = low confidence/tolerances; Veteran = high discipline/tolerance, restrained anger; Aggressor = high confidence/aggression but lower discipline; Brute = high anger response and pressure; Counterfighter = high discipline/adaptation; Bully = high confidence and provocation-sensitive anger. These are starting design labels, not approved values or final behaviors.

NPC policy must use one frozen shared snapshot, current legality, known effective stats, witnessed resolved event history, deterministic tie variation, and one voluntary action. No AI rollout, future RNG, hidden enemy wound/pain/mental pools or queued choice may leak through render, coach, notebook or group scoring.

## 7. Resolution and injury readout design

- **One headline number:** resolved `wound force N` points after contacted-zone armor (or a different single outcome number for block/parry/clash with a clear label). The number must be sourced from actual event deltas, not recomputed from hidden state.
- **One concise prose sentence:** attacker/defender/action + causal result + body consequence. Examples: “Your cut landed: 14 wound-force points after 3 Eye armor; a clean eye cut is bleeding and adds trauma.” / “His Light hit: your Guard was still raising, so it was not active at impact.” / “Your slash was parried: his Guard was ready and strong enough in the parry window.” Keep threshold catalog and full wound ledger expandable, not repeated in the headline.
- Every hit/blocked/parried/clashed/whiffed/interrupt explanation must be causal and event-backed. Record the relevant committed attack direction/zone/lock/reach, target movement/blocker, armor and zone, guard coverage/readiness/parry window/strength result, aim drift/miss, clash timing/lane, and interruption cause. Explain why the player's defense failed separately from why the player's attack did not land; both fighters may have independently resolved attacks in the same exchange.
- Audit missing-cause cases: `exchange_log.py:93+` currently infers some misses from cause tags/movement but can fall back to generic reach; `guard.py:13-74` emits useful parry/block strength/window facts; `wounds.py:526+` emits force/armor and injury. Add regression fixtures for every causal branch before changing wording.

## 8. Dash action identity and requirements

- Restore/retain existing `DASH_LIGHT` as **Dash+Light**: it automatically selects a legal approach route ending in Light reach, then commits the slash. Existing AI, Guard counter, geometry, timing, and save representation must remain this action.
- Design a separate `DASH_SLASH` action: retreating movement away from the opponent while making one Light attack during/at the end of the retreat; define exact move/strike tick order, attack bearing, range window, target tracking/lock, occupied-tile rejection, wall/block behavior, stamina/fatigue, defense/clash interaction, interruption and one-action accounting. It must be possible to hit on the way out, not merely run away then attack on another action. Decide whether player chooses backward vs lateral retreat direction and how AI/Guard uses it.
- New action must not change `DASH_LIGHT` action identity in old saves or action history. Add an enum/config schema migration only if required; old `DASH_LIGHT` active paths retain exact restore semantics.

## 9. Phases and gates

1. **Audit and specification (completed):** finish inventory/source trace; restore the Dash+Light label/behavior; write this plan. User approved §§3/10; Dash+Slash timing remains unresolved.
2. **Mental-stat mechanics (active):** implement confidence-seeded and in-fight fear, anger tolerance, anger events/effects, pain tolerance, nervousness migration, config/schema saves, derived stats and direct regressions. NPC action weights remain unchanged except where legality/effective timing naturally changes.
3. **Injury/bleed/pain mechanics:** after review of Phase 2 and injury model, implement severity/bleed/pain/wound merge-treatment changes and test blood-time consequences, function/reflex, special injuries, determinism and saves.
4. **NPC response design review:** after stats and injury mechanics are verified, produce actor/state matrix and sample behavior traces for all catalogued outcomes; ask user to approve this separate behavior phase. No retreat/escape or fear/anger behavior modifications before approval.
5. **NPC behavior:** after approval, add personality-specific legal choices using witnessed/public evidence only. Audit opening/fight action distributions and scenarios; no action-count violations.
6. **Dash+Slash:** implement as separate action and restore Dash+Light feature parity; user review of interaction before acceptance. Keep Guard counter integration to one action.
7. **Causal recap/readout:** event-backed compact number + prose for hit/blocked/parried/clash/miss/interrupt. Responsive dashboard review at 1280×800, 960×700 and 640×600.
8. **Verification and hands-on acceptance:** focused duel suite, full duel suite, config/session/world save migration, deterministic replay, group privacy, action-count, collision, real native input/captures. Ask the user to test; neither tests nor captures are acceptance.

## 10. Decisions still required before mechanic implementation

- Do confidence and courage remain distinct, or should existing courage migrate to the user's confidence-based starting-fear rule?
- Is fear strictly a persistent 0..100 pool, and what should safely reduce it (time, treatment, winning exchanges, distance, opponent yielding)? Is surrender/yield part of this stage or a later behavior-stage choice?
- Should Pain Tolerance reduce the stored pain pool, the effect of pain on fear/anger, the direct stat penalty, or only emotional reaction? Recommendation: keep raw pain visible; use tolerance on effective pain pressure, then avoid duplicate Speed/Accuracy penalties.
- How much should fear/anger change Speed/Strength/Accuracy at 0/50/100? Need calibrated floors/ceilings, not invented realism.
- Which events count as one anger/fear cause (hit vs wound; blood loss vs bleed-rate tick; deflected attack vs blocked hit)? Should anger decay or produce the existing crash?
- Which types of wounds should have continuing bleed, and what rate/time-to-collapse should define superficial, serious and critical? Should blunt wounds ever bleed continuously? Should multiple wounds stack linearly until the existing cap?
- Should incapacitated but alive enemies be terminal fight outcomes (current behavior), or may an approved later AI decide to yield/escape before it?
- For Dash+Slash, does strike resolve before the first backward step, mid-path, or at the last reachable tile? What range/angle/distance and which lateral routes should count?

No answer is inferred for any of these from the prior eye-specific discussion. Start with the stat contract, then return to injury severity and NPC choices in the order above.

## 11. Approved implementation record

- **Approval:** user said “ok it works, i approve, resume goal” after review of §§3/10. This authorizes the proposed stat model, not NPC behavior redesign or the separate Dash+Slash timing.
- **Schema/config:** duel tuning schema 22 added persisted fear per fighter, authored default confidence/tolerances, profile-specific Anger/Pain Tolerance, fear/anger Accuracy effects, fear Speed effect, pain-burden suppression, anger balance penalty, and event gains. Config values are initial tunings: max fear −30% Speed/−25% Accuracy; max anger +15% Speed/+25% Strength/−20% Accuracy/−15 balance rating; pain tolerance suppresses effective pain by at most 75%; positive anger gains are multiplied by `(1 − anger_tolerance)`. Player/no-profile default confidence is 1.0 (no baseline fear); authored NPC profiles seed fear as `100 × (1 − confidence)`.
- **Schema/config:** schema 23 carries the separately approved bleed-rate starters: CUT 0.5/turn, DEEP torso/head/eye 8, DEEP arm/hand/leg 6, and hand SEVERED 12; graze/bruise ongoing bleed stays 0. Old shipped-default values and active wound instances migrate, while non-default authored values remain intact.
- **Causes:** successful wound contact increases fear from hit/pain/continuing bleed and anger from hit/injury/pain; actual ongoing blood loss adds fear/anger gradually. Anger tolerance moderates positive gains, not decay. Raw pain stays intact; Pain Tolerance modifies its effective burden in physical stats and fear/anger stimulus. Maximum tolerance still leaves 25% of raw pain burden.
- **Nervousness/save compatibility:** live nervousness is set to zero. Loading an old active save folds its previous morale/anger-derived fear plus nervousness into `fear_meter`; old pre-fight nervousness is folded into the confidence baseline. Config/session schema migrations preserve old records and add tolerance defaults.
- **Behavior gate:** old NPC scoring and fear-break thresholds deliberately continue using their legacy pressure derivation during this stats phase. No new injury/personality retreat, escape, yield, or emotional response policy is implemented. Existing effective physical stats may change NPC timings because fear/anger now affect them.
- **Bleed-rate implementation:** schema 23 updates CUT to 0.5/turn for all zones, DEEP to torso/head/eye 8 and arm/hand/leg 6, and hand SEVERED to 12. Graze/bruise remain at zero continuing bleed. Existing exertion, aggregate cap, damage-type multipliers, and 60% bandage reduction are unchanged. Config migration replaces only old shipped defaults; custom config rates and non-default active wound rates survive. Active wounds that exactly match a prior shipped rate migrate to the reviewed rate.
- **Verification:** mental stage: focused mental/config/personality/stability/render tests passed **120 tests**; full duel suite passed **539 tests in 138.63s**. Fresh native dashboard passed **228 captures** at 1280×800, 960×700 and 640×600; inspected 640×600 selected Fear/Anger/Pain stat screens. Bleed-rate stage: focused wound/personality/mental/defense/stability/tactics/injury-response tests passed **179 tests**; full `tests/test_duel*.py` suite passed **541 tests in 95.65s**. Native dashboard passed **228 captures** at 1280×800/960×700/640×600; injury-ledger frames were inspected at each size. The preview asserts a torso CUT source renders at 0.5/turn and a graze has no ongoing bleed. These verify mechanics, regression and presentation, not hands-on acceptance or final tuning. The separate Dash+Slash remains unimplemented pending exact movement/strike timing.
