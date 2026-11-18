# Combat: understandable, playable, satisfying

Status: revised design direction following user review; implementation in progress.
The prior prototype is evidence of earlier work, not approval of its design.
The authoritative pivot below supersedes conflicting action lists, AI scope,
implementation order and acceptance requirements in the historical sections.
Supersedes the panel-first presentation direction in `COMBAT-UI-UX-PLAN.md`.
Do not build more features onto the current information dump before validating this approach.

## 1. Three requirements

1. Easily understandable: the map answers who is targeted, what threatens me, what I can do, and what just happened.
2. Easily playable: ordinary mouse and keyboard interactions work without hidden modes or memorized debugging controls.
3. Satisfying: walking, sprinting, dashing, striking, blocking and injury have distinct visible feedback.

## 1.1 Authoritative pivot: calculated duels, readable behavior, no RNG

### Close-range guided interaction

- `0 tile gap` means adjacent occupied tiles with no empty tile between fighters, not overlapping bodies. Duel adjacency and weapon reach use Chebyshev grid distance (`max(abs(dx), abs(dy))`), so cardinal and diagonal neighbors are both one tile away. Actors never overlap.
- At contact range show the enemy's actual committed action above their head, or an honest state such as `Waiting`, `Observing`, or `Recovering`. Do not invent a commitment when the AI is considering alternatives.
- Above the player show a small context-sensitive guide with the actual button for each relevant response: e.g. `Parry [G] — counter window if successful`, `Light [Space] — reaches first`, or `Dash [D + direction] — clears this strike`.
- Recommendations explain opportunities and requirements, not an automatic win sequence. Distinguish established outcomes against committed attacks from conditional advice against an uncommitted opponent. Show unavailable responses and their concrete reason only when useful.
- The focused duel menu has five actions: **Light attack, Heavy attack, Parry, ordinary Dash, and contextual Dash-strike**. Parry is defensive, and Dash-strike is a high-risk movement-plus-strike commitment, not a general attack menu.
- Dash-strike can gain first-contact opportunity from closing distance but carries high stamina/fatigue cost and exposes recovery on a miss or parry. Ordinary Dash remains an escape/reposition action. Implemented bindings are Space, F, G, D+direction, and J respectively.
- Keep safe select-only targeting, keyboard/mouse parity, explicit cancellation, character-local outcomes and details on demand.

### Deterministic physical contest

- **No RNG anywhere in duel resolution or tactical decision-making.** No seeded rolls, random accuracy, random drift, sampled utility ties or hidden luck. Identical initial state and ordered inputs produce identical choices and results; stable tie-breaking must be explicit and must not favor actor ID/player side arbitrarily.
- Derive contact timing and reach from weapon length, weapon handling/speed, wielder swing speed and current position. Longer/faster equipment can win first contact, but must not guarantee a hit through a correctly timed parry or escape.
- Strength and body structure affect force, stability and whether a parry can absorb/redirect an attack. Speed and accuracy affect whether a successful parry can become a quick counter before the opponent recovers. A parry is not universally an automatic counter or stun.
- Accuracy means deterministic placement/control and a calculable hit margin, not hit probability. Define how positioning, recovery, fatigue, injuries and opponent motion alter that margin.
- Stamina expenditure, fatigue accumulation, recovery, speed penalties and accuracy penalties create the main combo/tempo tradeoffs. Display the causal reason for a hit, failed parry, missed counter or exhaustion.
- Specify equations, units, rounding, simultaneous contacts, interruptibility, costs and action windows before tuning. No animation-created time, invulnerability or outcome changes.
- Stat overpowering can make a disciplined opponent effectively unwinnable in a given position. Do not secretly normalize stats or promise every matchup is winnable. Conversely, weaker/smaller/slower fighters can exploit a stronger opponent's predictable mistakes.

### Intelligence and learning behavior

- Build the strongest decision model first, then derive limited-intelligence models by restricting observation, planning depth, response selection and memory—not by adding random mistakes.
- The strongest model evaluates legal actions, delays, counters and future stamina/recovery states under the same rules as the player. Treat `impossible to defeat at equal stats` as a **verification target**, not a proven fact: seek a non-losing policy in a bounded, explicitly defined equal-stat duel model; publish losses, draws, cycles, search horizon and unproven cases. If both optimal sides can force a draw, do not claim guaranteed victory.
- Separate an omniscient test oracle from a normal NPC. Normal NPCs do not read queued player input, hidden exact stats or future actions. They may wait/observe and infer weapon reach, timing, strength, accuracy and stamina constraints from visible exchanges. Unknown facts remain bounded estimates until evidence narrows them.
- Discovery never retroactively changes a committed strike. Knowledge acquisition, planning limits and decisions must be inspectable and deterministic.
- After the strong baseline, add deterministic imperfect models and authored behavior: overaggressive first-mover who rarely/never parries; dash-and-slash specialist; patient observer/counterfighter. Behavior constraints can deliberately reject the oracle's safest choice and create learnable openings.
- Repeated attempts should teach attack sequences, decision conditions and recovery opportunities, like learning a boss. Same conditions reproduce behavior; changed player actions/stamina/position can change the response. No random behavior mix to conceal patterns.
- Example acceptance exchange: an aggressive equal-strength/body opponent opens; a properly timed parry succeeds; the player's sufficient speed/accuracy permits a quick light counter. Reduce those stats and demonstrate why the same counter fails. Repeat with stronger force defeating the parry and with dash escape avoiding contact.

### Revised delivery order and gates

1. **Rules contract:** define contact distance/reach, five actions, deterministic timing/accuracy/parry/counter formulas and resource effects. Keep old saves/unrelated systems intact; document migration/versioning separately.
2. **Strong equal-stat baseline:** build a headless duel/action evaluator and strongest policy before adding weaker policies. Test both sides, mirrored positions, waits, dash/kiting, parry loops, resource exhaustion and adversarial action sequences. Report bounded guarantees honestly.
3. **Information limits and weaker policies:** add observation/inference and explicit planning limits; compare against the oracle without giving live NPCs oracle knowledge.
4. **Learnable behavioral opponents:** add aggressive and dash-slash profiles, predictable conditional sequences and stat-disadvantage victories caused by exploitable decisions, not luck.
5. **Native guided implementation:** at zero-gap contact, enemy intent/waiting above enemy and actionable response/key guide above player. Demonstrate light/heavy/parry/counter/dash-strike and causal stamina/fatigue/accuracy changes. Continue through the full integration and verification gates; do not pause at a prototype-review gate.
6. **Integration and verification:** deterministic replay/snapshot continuation, truthful guide-vs-execution checks, no-RNG audit, mouse/key parity, native zoom/reduced-motion checks and save/map safety. Run focused and full suites and disclose unrelated failures.

This is a combat rules/AI design pivot, not merely UI polish. Existing frozen commitments and save safety remain constraints; any required timing/balance/rules changes must be explicit rather than smuggled in as presentation. Implementation status and verified limitations are recorded below; this does not claim unbounded oracle guarantees or complete campaign injury parity.

### Rules contract v1 (implemented in kernel and guided live adapter)

This contract is deliberately scoped to person-to-person melee duels. Existing firearms, boarding hazards, morale, and group-fight behaviors are not silently redefined by these duel rules; integration must explicitly route duel actions through this model and retain compatibility for other encounters until separately migrated.

#### Space, facing, and reach

- Simulation time is integer ticks at 100 ticks/second. All rounding is integer ceiling for durations and integer floor for derived bonuses; never use floating-point randomness.
- Duel range is Chebyshev distance. A sword with effective reach 1 can hit cardinal or diagonal adjacency. A weapon with effective reach 2 can hit across one intervening tile when its committed line is unobstructed. A body tile occupied by a third actor stops a thrust; walls/closed doors stop it. A weapon cannot hit through its wielder's target or retarget after commitment.
- Each weapon has `reach_tiles` (integer length), `handling` (50..150; higher means easier/faster control), and `mass` (integer load units). Each actor has `swing_skill`, `accuracy`, `strength`, body-part `structure`, `stamina`, `fatigue`, and `balance`; all values are clamped to their documented ranges at load/creation. Missing new fields migrate to documented neutral defaults, not random values.
- Facing is one of eight normalized directions. Melee attack and parry require the target inside the actor's forward 90-degree cone (dot product positive and `abs(cross) <= dot`); turning is instantaneous only while ready and before commitment. A committed attack/parry freezes facing and target lane.
- If two attacks would first contact on the same tick, resolve from a snapshot of both actors at that tick. Both may connect (or both be defended); neither receives scheduler-ID priority. Apply simultaneous damage only after both contact decisions. An actor already incapacitated by an earlier tick cannot act.

#### Core actions and commitments

- **Light:** one-target, one-lane strike; default weapon reach; 30-tick base windup, 40 stamina cost, 45-tick base recovery, 8 force units. Its short windup and recovery make it the standard punish/counter.
- **Heavy:** one-target, one-lane strike; weapon reach plus one lunge tile, 75-tick base windup plus 20 base ticks for the lunge, 70 stamina cost, 90-tick base recovery, 14 force units. It is stronger but more readable and costly.
- **Parry:** no damage by itself; 10-tick startup, 35-tick active window, 45-tick recovery, 20 stamina cost. Must face the incoming attack and be active on its contact tick. A successful parry contests force/stability; exceeding the defender's capacity causes a failed/overwhelmed parry, not a guaranteed block. Only a successful parry opens a counter opportunity.
- **Ordinary dash:** choose a legal straight 8-way destination up to three tiles, pay 35 stamina immediately, use an 80-base-tick total travel time split across scheduled tile events, then take 120 base recovery ticks. It has no invulnerability. Every traversed tile can be struck; endpoint/path are frozen at commitment. Blocked tiles stop movement. It does not strike.
- **Dash-strike:** only legal when at least two tiles from a visible target and a legal straight path reaches melee range. It commits a two-tile maximum dash path followed by one light-force strike. Contact is evaluated at its scheduled endpoint, not by teleporting or homing. Cost 60 stamina; if target leaves the committed lane/path, the strike misses. Base 50-tick approach plus light windup; 110-tick recovery. A parry can stop it; a miss/parry exposes the long recovery. Exact cost/duration tuning follows contract tests, not UI adjustment.
- Existing pattern attacks are not additional player-facing duel buttons. During migration the legacy patterns remain for non-duel encounter adapters; duel actors expose only the actions above.
- On acceptance, an action freezes its target/lane/path, start tick, contact tick, and recovery deadline. The simulation does not accept another action while committed or recovering. There is no hidden input queue. Invalid/cancelled selection, preview, target change, or dash-path targeting before commit consumes no time. Once accepted, a commitment cannot be cancelled by player input; a valid interruption (e.g. incapacitation) may stop it and must be logged.
- An intent label means an accepted frozen commitment. Before acceptance the display says `Observing`/`Waiting`; during a parry it says `Parrying`; during action completion it says `Recovering`. Do not surface internal AI candidates as a chosen action.
- Contact-time order: capture actors' positions/facing/resources/statuses; determine all attacks contacting those positions; evaluate active parries/escapes and deterministic accuracy; compute simultaneous force/damage results; apply consequences; then advance recovery/ready events. Stable actor-ID ordering is allowed only for presentation/log iteration, never for tactical victory or priority.

#### Deterministic formulas v1 (integer contract; balance may be tuned only with tests)

All calculations below use integer stats and integer division; `ceil_div(a,b) = (a+b-1)//b` for positive values. Clamp inputs at creation/load, not opportunistically during combat. Any tuning changes these equations/version and their boundary tests together.

- Actor duel stats: `speed` 25..200 (existing), `swing_skill` 50..150 (neutral 100), `accuracy` 0..100 (legacy/default 70), `strength` 0..8 (existing), `stamina/max_stamina` 0..100/positive, `fatigue` 0..100, `balance` 0..100. Weapon stats: integer `reach_tiles` 1..4, `handling` 50..150 (neutral 100), `mass` 1..8. Missing save/config fields migrate to swing_skill=100, accuracy=70, weapon defaults per content; explicit zero values remain valid where range allows.
- Existing per-part `structure` is clamped 0..100 and defaults to 100. `body_stability = floor((torso_structure + left_arm_structure + right_arm_structure)/3)`. A missing/severed arm counts as zero. This makes the body actually holding/absorbing the weapon matter while preserving existing injury data.
- `fatigue_factor = max(50, 100 - fatigue//2)`. `stamina_factor = 100` at ratio >=30%; below 30%, it is `75 + floor(25*stamina/(0.30*max_stamina))` using exact integer form `75 + (250*stamina)//(3*max_stamina)`, clamped 75..99 (at zero stamina =>75). `effective_swing_speed = clamp(25,300, floor(speed*swing_skill*handling*fatigue_factor*stamina_factor / 100_000_000))`. Neutral speed/skill/handling, no fatigue and full stamina yields 100. A low-resource/fatigued fighter is slower; no hidden action haste.
- Weapon reach is `reach_tiles`; Light can reach that many tiles. Heavy's lunge grants exactly +1 tile but only along its frozen forward lane and its contact duration includes that lunge. Dash-strike has no additional reach beyond its committed path endpoint plus weapon reach. The range metric remains Chebyshev.
- For action base windup `B`, `windup_ticks = max(1, ceil_div(B*100, effective_swing_speed))`; action recovery `R` is `ceil_div(R*100, effective_swing_speed)`. Parry startup/window/recovery use the same speed conversion. They cannot be zeroed by high stats.
- Deterministic contact placement: compute `control = accuracy + handling//4 - fatigue//4 - (20 if stamina below 30% else 0) - (15 if attacking while moving)`. Compute `evasion = 20 + target.speed//4 + (25 if target is moving at contact else 0) + target.balance//10`. If control < evasion, the committed strike misses by control margin. Otherwise it connects at the committed target; no reroll/body-part selection. This is a threshold, not a chance. A controlled strike's location is the declared target part (duel defaults to torso); optional aimed parts must add a deterministic control penalty in a later contract change.
- `attack_force = base_force + 2*strength + weapon.mass + (10 if dash-strike has completed its approach without collision else 0)`. Light base_force=8; Heavy=14; dash-strike uses 8. Force is evaluated at contact from frozen action/weapon but current structure and resource state; already-committed attacks do not become faster when stats are newly learned.
- `parry_capacity = 2*strength + floor(body_stability/10) + balance//5 + handling//5`. A parry is successful only if active, correctly facing, and `attack_force <= parry_capacity`. Otherwise the strike connects if its control check passes; feedback distinguishes `wrong-facing`, `outside-window`, `overpowered`, and `precision-miss`. A successful parry costs its declared stamina once, reduces defender balance by `ceil(attack_force/2)`, and creates a counter window ending at `parry_contact_tick + 30`.
- A quick light counter is legal only in that explicit 30-tick window, if the attacker is in Light reach/forward lane, the original attacker is not already in recovery-exit invulnerability (there is none), and defender's `effective_swing_speed >= opponent.effective_swing_speed` at counter start. Counter contact uses ordinary deterministic accuracy. It is not a forced hit/stun; a countering attack can itself be parried if the opponent's state/action permits it.
- `stamina_cost` is paid at commitment: Light 40, Heavy 70, Parry 20, Dash 35, Dash-strike 60. If cost exceeds current stamina, reject with exact deficit; do not clamp stamina below zero or let desperation actions execute. Recovery restores `floor(elapsed_ticks/10)` stamina up to max and reduces fatigue by `floor(elapsed_ticks/50)`; waiting explicitly adds 12 stamina and removes 2 fatigue per 100 ticks. Attacking adds fatigue: Light +4, Heavy +8, Parry +2, Dash +16, Dash-strike +12. Clamp at 100. Fatigue/resource changes are applied exactly once at action start/elapsed recovery, not duplicated at scheduler events.
- The standalone bounded kernel's `Wait` chooses an explicit 1..100 tick delay so a player/oracle can time a parry against a visible commitment. Passive recovery still accrues while waiting, plus `floor(12*wait_ticks/100)` stamina and `floor(2*wait_ticks/100)` fatigue relief. This timing control does not silently choose an NPC action; it advances only the accepted simulation clock.
- Weapon length creates a first-contact advantage via reach; weapon handling and actor swing speed reduce the contact deadline by the same explicit formula. Equal-tick contacts are simultaneous per the snapshot rule above. Reach never bypasses an active parry, line obstruction, frozen target lane, or deterministic accuracy check.

These equations are a proposed v1 rules contract, not a claim of balanced implementation. Add pure formula tests at every clamp/threshold, plus the aggressive-opener/parry/counter, low-stat counter failure, overwhelming force, longer-reach first contact and dash-escape examples before mechanics code uses them. If tests expose a contradiction, revise formulas and the ruleset version first; do not quietly diverge implementation from this section.

The isolated kernel in `pirates/combat_duel.py` implements the integer formulas and a bounded open-lane 1v1 event model. Its evaluator vitality is a normalized 0..100 **search-only durability score**; it is not campaign HP, blood, injury, or an assertion about one-hit combat. It commits frozen actions, allows a ready opponent to respond to a public commitment, advances earliest scheduled events, resolves same-tick contacts from one snapshot, processes dash tiles after contacts, and stops at terminal/cycle/horizon states. The `OraclePolicy`/`OraclePolicyConfig` seam accepts only `DuelDecisionState` and refuses incomplete search by default; the ordinary `Combat`/NPC runtime does not import it or receive oracle state. The kernel has no arena walls/hazards/multi-actor occupancy; these are explicit search bounds, not claims about live encounters. `tests/test_combat_duel_rules.py` verifies this standalone model. The native/boarding adapter is not integrated yet.

#### Bounded oracle verification checkpoint (Nov 17, 2026)

- `python tools/combat_duel_headless.py --depth 2 --nodes 100000` completed all ten searches across five equal-stat starts (cardinal contact, diagonal contact, tired contact, reach-2 at distance 2, and open-lane distance 4) with both seat orderings. Every score was 0, each seat-swap score negated, actions matched, and every bounded search was complete. Principal variations chose repeated 1-tick waits: a draw/cycle line, not a guaranteed win.
- Equal adjacent neutral fighters at depth 3 completed with score 0 after 65,338 nodes; the policy again selected the repeatable wait cycle. This only proves the minimax value for the exact depth-3 open-lane action model (normalized vitality, all wait delays 1..100, no walls/hazards), not an unlimited equal-stat non-loss theorem.
- A depth-4 equal-stat attempt with a 500,000-node budget did not return within 120 seconds; no depth-4 result is claimed. The search exposes node budget and `complete`; incomplete results must not be used as proof or silently treated as complete.
- `tests/test_combat_duel_rules.py` additionally covers long-reach first contact before a short weapon can act, aggressive Light → timed Parry → speed-gated quick counter, overwhelming force, Dash escaping a frozen Heavy lane, no invulnerability before the first Dash tile, delayed Parry against Dash-strike, same-tick mutual contacts, resource recovery, cycle detection, seat symmetry, and node-budget exhaustion. These remain kernel tests, not native gameplay acceptance.

#### Commitment and observation contract

- Combat-facing state is derived only from the simulation: `Observing` (active and ready, no accepted intent), `Waiting` (explicit wait/hold decision), `Preparing Light/Heavy/Dash-strike` (accepted intent with frozen lane/contact tick), `Parrying` (accepted active-window intent), `Dashing` (accepted frozen path), `Recovering` (action ended but recovery deadline not passed), and disabled states (`Staggered`, `Unconscious`, `Dead`, `Fled`). A zero-gap HUD must never translate a generic ready state into an invented attack.
- UI display priority: an accepted imminent attack or active parry first, then dash/movement commitment, then recovering/disabled reason, otherwise Observing/Waiting. Show the exact resolved-at tick relative to current simulation time only as secondary detail; plain-language outcome guidance is primary.
- A commitment captures action, weapon, target actor ID (for duel attacks), lane/path, target part, acceptance tick, contact tick, and scheduled recovery. It cannot follow the target or change weapon/attack after acceptance. If the target moves, contact checks the actual committed lane/path and current tile at the scheduled tick; the action may miss. The displayed label is generated from this immutable record.
- Same-tick event processing follows the snapshot/resolve/apply sequence defined above. A parry window is active on ticks `[startup_end, startup_end+window)`; a hit resolving on the first tick is catchable, on the exclusive end tick is not. Counter window is `[successful_parry_tick, +30)` and closes before attacks resolving at its exclusive end. Store bounds explicitly to avoid scheduler-order off-by-one behavior.
- Ordinary NPC policy receives only public visible state and its own exact stats/state. It may see current player tile/facing, visible weapon identity/type, committed visible intent/lane, stamina/fatigue cues only when exposed by the UI/observable behavior, and past resolved contact events. It never reads player queued input, private combat snapshot fields, exact hidden player stat values, or future events. The headless oracle is a separate interface and is never passed into runtime NPC policy.
- Beliefs are deterministic intervals, initialized from broad archetype-independent bounds: actor swing speed 25..300, accuracy 0..100, strength 0..8, stamina 0..100, fatigue 0..100. Visible weapon identity narrows reach/handling/mass to that content record; concealed/unknown weapon stays at bounds. A directly observed timed action may narrow effective speed only if action type, start tick and contact tick were visibly observed; a successful/overwhelmed parry provides force/capacity inequalities, not exact strength/body structure. Each update records the public evidence event ID and resulting interval. No inference may claim more precision than the equation supports.
- Belief updates occur after a public event resolves and affect only future action selection. They cannot change an already-frozen commitment or rewrite earlier outcomes. Campaign-persistent learning is out of scope for v1; within-encounter beliefs are part of snapshots/replays and reset with a fresh encounter. Player learning across attempts comes from repeatable authored tells and outcomes, not hidden NPC memory.
- The player guide may compute from public committed intents and the player's own exact stats. For an NPC that has not committed, it may show conditional options (`If they swing: Parry [key]`) and must not label any one response as guaranteed. Against a committed attack, it must evaluate the frozen contact tick and actual current stats; if the state does not prove a result, show `uncertain` plus the known condition rather than a confident false recommendation.

#### Observation-only belief implementation checkpoint (Nov 17, 2026)

`pirates/combat_beliefs.py` now defines a narrow `PublicObservation` input DTO and immutable `CombatBelief`. It rejects live/unknown object inputs, deduplicates stable event IDs, records each event with the exact intervals it changed, and preserves inconsistent evidence without corrupting prior bounds. Verified observations include visible weapon identity; accepted/rejected stamina-cost actions; timed-action effective-speed intervals from the integer ceiling equation; force/parry inequalities; observed balance; and deterministic hit/miss accuracy inequalities. Composite parry-capacity/known-force evidence may bound strength and body stability only where the public equations permit. It does not infer exact hidden values from acceptance, damage, or a single parry. Campaign save persistence is intentionally not added: beliefs reset per encounter and must be attached to the future encounter snapshot/replay adapter. `python -m pytest tests/test_combat_beliefs.py -q` passes 8 tests; these tests do not imply ordinary NPC policy or live combat integration exists.

`pirates/combat_npc.py` adds a first deterministic policy boundary with `NPCObservation` (own exact state, public opponent facts, evidence beliefs and action candidates) and three declared tiers: limited (one-tile sight, 25-tick response delay, no force inference, fixed action ordering), informed (four-tile sight, 8-tick delay, belief-derived force bound, earliest-contact selection) and disciplined (eight-tile sight, immediate response, contact-plus-recovery selection). Policies reject full duel/live actor inputs, use only typed public commitments and choose parry/dash or a bounded tactical ranking; the limited tier reproducibly misses short response windows. The planning limits are deterministic one-action ranking horizons, not game-tree search; this remains an observation/response seam, not authored behavior profiles, a combat-runtime adapter, or balanced NPC difficulty. `tests/test_combat_npc.py` covers the DTO boundary, deterministic repeats, evidence-derived force bounds, parry failure, planning-rank and tier response differences.

The same module now exposes explicit authored `BehaviorProfile`s with stable, inspectable tells: `FIRST_MOVER` chooses Heavy first and never parries, `DASH_AND_SLASH` prefers contextual Dash-strike then ordinary Dash, and `PATIENT_COUNTER` waits in fixed 10-tick increments, parries a known incoming threat, and chooses Light during its counter window. These are deliberate deterministic errors/openings, not random misplays. `tests/test_combat_npc.py` verifies each tell/action match. Profiles currently return actions only; event logging, player-facing tell UI, encounter persistence and live adapter remain outstanding.

The learning/exploit harness also verifies a complete bounded example: a lower-strength player (strength 0) observes/reliably times a parry against a strength-7 Heavy-first NPC, then uses the speed-gated Light counter to damage it. A separate repeated-state test proves identical policy/observation inputs yield identical actions. Publicly observing a known-mass Light force narrows the opponent-strength interval and changes a later informed response from Dash to Parry; repeating the updated observation reproduces Parry. This verifies evidence-sensitive policy behavior and a predictable authored mistake in the isolated kernel only; it is not yet a native player encounter.

#### First live adapter and control pass (Nov 17, 2026)

`content/combat_arena.json` has duel reach/handling/mass for Cutlass, Fists, Spear and Boarding Ram; ranged weapons are rejected by the live projections in `pirates/combat_core.py::duel_stats_for_actor` / `duel_weapon_for_actor`. `CombatArena.act` routes explicit `duel_light`, `duel_heavy`, `duel_parry`, `duel_dash`, and `duel_dash_strike` commands through `combat_duel.apply_action`; it initializes only when exactly one melee enemy is within reach + two tiles and both actors have no frozen legacy commitment/recovery/movement. Live dash paths and attack lanes are checked against the actual arena walkable map, leaving the pure oracle's open-lane bounds intact. The NPC adapter builds an `NPCObservation` from own exact stats and public player position/facing/visible weapon/commitment, then uses the informed observation-limited policy; no oracle state is passed to runtime AI. The result maps normalized duel vitality proportionally to the actor's starting blood/HP, copies stamina/fatigue/balance/position/facing/readiness, updates simulation time through the existing time hook, and writes the typed duel state into snapshots and guided replay rows. Dump format now versions and stores the guided kernel slice, actor IDs and replay rows; `duel_state_from_data` strictly validates JSON records, and `CombatArena.restore_guided_duel_snapshot` restores it onto a matching active arena only when the ruleset, actor, map, and replay-tail checks pass. Tests verify JSON roundtrip, rejection of malformed/version/tail data, and identical next kernel transitions. This is not a general campaign/world save loader; active campaign saves remain blocked by the existing safety gate.

`play.py` now exposes exactly five core buttons (Light/Space, Heavy/F, Parry/G, Dash/D+direction, contextual Dash-strike/J); mouse target attack resolves to Light only when guided actions are available. Legacy group/ranged mechanics remain on their old route. At adjacent contact the enemy label is derived from the actual commitment (`Preparing Light/Heavy/Dash-strike` with ETA, `Parrying`, `Dashing`, `Waiting`, `Recovering`, or `Observing`). The player guide checks the committed contact tick against the actual parry window and dash first-tile timing; it says `force uncertain` instead of promising a parry outcome and calls out late/unavailable responses. Live feedback carries first-contact ordering, control-vs-evasion margin, force-vs-parry capacity, parry/counter timing and speed gate, action stamina cost, resulting stamina/fatigue and the exact fatigue/low-stamina attack-control penalties. Failed Dash-strikes expose remaining recovery; structured consequence records avoid translating normalized duel vitality into campaign HP or hand injuries. Actor-local labels now wrap against the actual SDL output bounds and clamp horizontally, keeping long cost/risk messages legible. `tests/test_combat.py` covers an in-window guide, too-late parry/dash, first-contact advantage, successful parry/counter and costs, and committed Dash-strike miss recovery. The combined focused suite passed 202 tests; compileall and scoped diff-check passed (only Git's LF→CRLF warning for `play.py`). `python tools/preview_combat.py --output C:\Users\Warp\AppData\Local\Temp\opencode\pirates-combat-guided` passed automated native SDL with 21 screenshots, hit-testable five-button bar, adjacent contact, 3-tile approach, 2-tile spear commitment, in-window parry/counter, authored First Mover heavy intent, failed Dash-strike risk/recovery, 640×400, enlarged-font/zoom, reduced-motion, monochrome and clean map restoration; inspect `01c-long-reach-intent.png`, `03-committed-threat-guide.png`, `04-parry-resolution.png`, `04b-authored-first-mover.png`, and `04c-dash-strike-miss-risk.png`. The fresh repository-wide `python -m pytest -q` run passed **837 tests, with 11 xfailed**. This is automated native SDL evidence, not a manual usability playtest or full acceptance: the rules kernel remains open-lane with normalized vitality, and live critical/body-injury parity remains incomplete. Static scans found no RNG calls in `combat.py`, `combat_core.py`, `combat_duel.py`, `combat_npc.py`, or `combat_beliefs.py`; combat hit drift/part selection, decisive outcomes, AI ties and spawn selection now use deterministic stat/spatial rules under ruleset 3. Preserve these bounds in any status claim.

### Deterministic legacy-resolution follow-up (ruleset 3)

The initial native-duel status above predates the final zero-RNG audit and is superseded on randomness. `combat_core.py` no longer constructs/uses an RNG: hit-part drift is a deterministic pressure threshold (`drift_pressure >= 0.50`) with spatial left/right selection; decisive clean-heavy head/torso wounds compare strength, target imbalance and pattern damage against explicit integer thresholds; unconscious-vs-death uses a deterministic remaining-blood/balance survival score; near-best AI actions use stable action/destination order. `CombatArena.spawn` selects the nearest legal tile with coordinate tie-break instead of a random tile. `COMBAT_RULESET_VERSION` is 3 so old replay dumps cannot silently execute under changed semantics. Source scans report no RNG calls in `combat.py`, `combat_core.py`, `combat_duel.py`, `combat_npc.py`, or `combat_beliefs.py`; combat tests verify seed-independent AI tie resolution, deterministic wound/part selection, old replay-version rejection, and all five guided actions. Focused verification after this follow-up: 202 passed; native preview passed 21 screenshots with map restored/no save touched; compileall and scoped diff-check passed. Fresh repository-wide `python -m pytest -q` after ruleset 3: **837 passed, 11 xfailed**. This audit covers combat runtime, not the village/weather or `sim/` RNG systems. The live duel kernel remains open-lane with normalized vitality, and campaign body-injury parity/general campaign-save loading are not implemented; oracle bounds remain finite, not an unlimited equal-stat theorem.

## 2. Problems to solve first

- Dash looks like normal movement: the player cannot perceive the action they chose.
- Sprint appears to require recovery after one tile: movement feels interrupted rather than athletic.
- Selecting/attacking a target is undiscoverable and ambiguous.
- A countdown such as 0.6 seconds gives no useful decision guidance by itself.
- The large status panel and ASCII anatomy compete with the actual fight.
- Added history/preferences/onboarding work is unfinished; inspect and preserve it, but do not assume it fixes these problems.

## 3. Core interaction: point, preview, act

### Mouse

- Hover an enemy: outline them and show a short name plus `Click to target`.
- Left-click an enemy: select only. Display a persistent target ring and facing/aim marker. Never make a second identical selection click unexpectedly attack.
- Left-click a reachable ground tile: move there using an explicit previewed path. Bound path commands to a short tactical distance; do not walk blindly through an impending attack.
- Right-click the selected enemy: basic attack. Also expose an always-visible clickable Attack button so this is discoverable.
- A small bottom action bar provides Attack, Heavy, Guard, Shove, Dash and Wait. Buttons show their keybindings, availability and a one-line reason when unavailable.
- Click Dash or press its key, then click a landing tile: preview first, commit on that click. Esc/right-click ground cancels targeting.
- Selecting a target, hovering and cancelling never consume combat time.

### Keyboard

- Arrows/WASD move; Shift toggles sprint with a visible player-local marker.
- Tab cycles visible targets with the same ring used by mouse selection.
- Space attacks selected target; guard, shove and dash have one consistent displayed binding each.
- Enter or a visible Wait button waits; Esc cancels a targeting mode before leaving combat.
- Holding movement must not repeatedly toggle sprint or produce uncontrolled queued actions.
- Resolve binding conflicts deliberately; publish one control table and test it against dispatch.

## 4. Information lives beside the combatants

Remove the large default combat information panel and default ASCII body diagram.

### Enemy-local presentation

- Persistent target ring: `TARGET`, not color alone.
- Short current-action label: `Preparing sweep`, `Aiming at you`, `Guarding`, `Recovering`.
- Committed attack footprint on the map; a clear line connects ranged aim to threatened space.
- A compact windup indicator may supplement the label, but never requires translating a number into an action.
- State priority: incoming attack > vulnerability/recovery > routine movement.

### Player-local presentation

- Small stamina indicator and sprint/dash marker near the player, not over their sprite.
- Only urgent functional warnings: `Bleeding`, `Left leg hurt — slower`, `Guard broken`.
- Selected action preview near its destination or target.
- Clear facing/guard direction when it matters.

### Outcomes

- Brief source/recipient feedback: `Blocked`, `Parried`, `Missed`, `Arm injured`.
- Impact flash/outline and restrained animation reinforce the result without hiding tiles.
- Optional details/history contain numbers and anatomy; ordinary play does not require opening them.
- No HP bar or cosmetic relabeling of the internal survival pool.

## 5. Replace countdown interpretation with actionable previews

When an enemy threatens the player, display `Sweep incoming — leave marked tiles`.
Hovering an action or destination asks the deterministic scheduler what actually happens:

- `Step here: escapes before sweep`.
- `Dash here: escapes; costs 12 stamina`.
- `Attack: sweep lands before your hit`.
- `Guard: ready before impact; facing attacker`.
- `Too late to step clear` or `Blocked by opponent` when appropriate.

Use plain outcomes plus distinct safe/risky/invalid symbols; color is supplementary.
Evaluate first arrival, subsequent path occupancy, windup, recovery and existing commitments—not just total command duration.
Do not predict uncommitted AI choices, invent guaranteed safety, or retune telegraphs to make labels look correct.
If the preview cannot establish a result, say `Outcome uncertain` and explain the known timing instead.

## 6. Make movement feel different

### Walk

Readable single-step translation; no exaggerated recovery effects.

### Sprint

Audit the reported one-tile recovery against command mapping, path length, stamina and scheduler readiness before changing balance.
Make valid consecutive movement feel continuous. Show ordinary action resolution as movement, not as a misleading exhausted state.
Keep meaningful stamina/fatigue costs, but avoid an unexplained stop after every tile.
If true recovery is mechanically required, show the reason and duration clearly; propose any cadence retuning separately with before/after tests.

### Dash

Animate the actual multi-tile path noticeably faster than walking, with a brief start cue, restrained trail and definite landing cue.
Show the committed landing marker before execution and preserve per-tile collisions and threat timing.
No teleporting through enemies, cosmetic invulnerability or animation-created extra reaction time.
Reduced-motion mode uses a static path/landing flash instead of travel trails or camera effects.

Presentation playback must not change simulation ticks, RNG, intent geometry, replay or outcomes.
User input during playback must be explicitly ignored or bounded—not accidentally queued into another move.

## 7. Aiming and injury

Default aim is torso. Targeting an enemy must work without understanding anatomical menus.
An optional small aim selector opens from the action bar or target interaction, with clear Head/Torso/Arms/Legs choices and short real mechanical tradeoffs.
Mark the selected region without a default large body diagram.
Do not imply independent hand anatomy: the existing hand-to-arm mapping remains a documented model limitation.
Show injury where it matters through functional labels and effects, not a dense anatomy readout.

## 8. Implementation order and gates

### A. Audit and small interaction prototype

- Reproduce dash, one-tile sprint recovery and targeting with native input.
- Record actual command path, readiness and animation behavior.
- Prototype only targeting ring, enemy intent label, threat footprint, action bar and one movement preview.
- Remove default information dump in the prototype; details remain accessible.

Gate: visually inspect the prototype with the user before a broad redesign implementation.

### B. Mouse/keyboard parity

- Implement shared action dispatch for buttons, pointer and keys.
- Add clear selection, attack, cancellation and invalid-action feedback.
- Test no accidental attacks during target selection and no time consumption during inspection.

Gate: target, attack, move, dash, guard and wait are usable with either input method.

### C. Decision guidance

- Build truthful safe/risky/invalid preview data from the real scheduler.
- Cover short deadlines, intermediate movement tiles, occupied destinations, guard direction and ranged attacks.

Gate: displayed timing claims match executed outcomes across deterministic regression cases.

### D. Movement and impact presentation

- Add distinct walk/sprint/dash playback and landing/impact cues.
- Fix misreported recovery states; separately review any real movement-balance changes.
- Integrate reduced motion without changing mechanics.

Gate: a viewer can distinguish walk, sprint and dash without reading a label; combat snapshots/replays remain equivalent.

### E. Polish and verification

- Add unobtrusive optional tips only where actions first become relevant.
- Complete history/preferences integration only after the core screen is comprehensible.
- Verify 640x400, default and enlarged UI/font sizes, zoom extremes, monochrome and reduced motion with actual native screenshots/input.
- Check nearby groups, map-edge labels, obscured targets, wounded/missing limbs, firearm windup/reload and ended fights.
- Run focused combat, related ship/boarding tests, fairness audit and full suite; report unrelated failures rather than editing around them.

## 8.1 Native prototype checkpoint (not yet user-approved)

The first save-free prototype is implemented in `play.py` and `pirates/combat.py`:

- Left-click selects only; Space, right-click on a foe, or the visible Attack button attacks.
- The selected foe has a persistent `TARGET` caption and bracket; committed attacks retain exact marked tiles and use short actor-local intent captions.
- A six-button action strip shows Attack/Heavy/Guard/Shove/Dash/Wait with bindings and dimmed unavailable actions. The status panel is reduced to three short control/target lines; the default anatomy/stat dump is suppressed.
- Hover guidance uses the existing read-only scheduler preview for movement and reports `SAFE`, `TOO LATE`, or `BLOCKED` where a committed threat makes that verdict meaningful.
- Shift sprint maps keyboard movement to the existing two-tile run action. Walk/run/dash paths are drawn after their deterministic resolution; playback does not step the combat scheduler. Reduced motion uses a static path cue.
- Zoom/font rebuilds clear SDL texture references before drawing with the replacement renderer.

Known prototype limits: action-bar disabled states are dim rather than accompanied by individual reasons; guard/attack hover previews, facing cues and the full injury/outcome vocabulary are not complete. Mouse ground movement stays short and uses the legacy click dispatch. Movement playback is post-resolution presentation, not interruptible real-time simulation. The capture is at `C:/Users/Warp/AppData/Local/Temp/opencode/combat-intuitive-prototype`; native preview verified 16 frames, action-button hit regions, scheduler-backed escape guidance, reduced-motion/zoom/small-window modes, frozen world clock, map restoration, and no save-slot writes. Focused combat tests: 144 passed. This is implementation evidence, not a usability result; wait for the user to review before Phase B or broader polish.

## 9. Acceptance tests

- A new player can select an enemy, identify the target and attack without explanation.
- A player sees an incoming sweep and finds a valid response without calculating simulated seconds.
- Mouse and keyboard can perform the same core actions with clear cancellation.
- Normal screen contains no large statistics panel or required ASCII anatomy interpretation.
- Invalid actions explain why, consume no time and never silently become a different action.
- Movement cadence feels intentional; sprint does not appear as exhaustion after every step.
- Dash visibly traverses its actual path and has a distinct landing.
- Hit, miss, block and parry are distinguishable without reading a debug log.
- Five fresh-player sessions are a proposed usability gate, not a result to claim from automated tests.
- All presentation and preview work preserves deterministic combat, frozen threats and ordinary save safety.

## 10. Scope boundaries

This is a combat interaction/presentation redesign, not a new injury simulation, realtime combat conversion, AI rewrite, persistent campaign wound system or strategic NPC migration.
Do not call the plan complete merely because individual features/tests exist: first validate that the resulting fight is understandable and enjoyable.
