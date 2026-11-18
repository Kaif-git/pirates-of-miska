# Player Crew, Recruitment, and Long-Term NPC Stories

## Status and purpose

**Implementation roadmap with delivered behavior and remaining gates.** The crew vertical slice described below is implemented; the completion checklist and verification checkpoint distinguish verified features from open integration limitations.

### Implementation checkpoint — recruitment, growth, and lifecycle

`pirates/crew_recruitment.py` defines a finite authored port roster, including a high-potential novice. The port menu supports review, hire, leave-open, or decline; hiring charges a signing advance, consumes the candidate identity exactly once, joins a persistent sailor, and installs a real bunk. Recruitment is separate from captive ledgers and old saves migrate to the finite pool. Work practice awards bounded skill XP according to learning potential. A novice now receives a deterministic 25% XP increase while doing real same-labor work near an actively working crewmate at least two skill points more experienced; mentorship changes learning, not work output, and appears in the apprentice's activity/profile plus the audit/event log. Injury/recovery, release, desertion, retirement, betrayal, and untreated critical-condition death are implemented with validated saves and former-crew identity accounting. Crewless voyages with vacant officer roles remain supported; there is no headcount sailing-speed bonus.

Voyage planning, confirmation, and underway advancement tolerate zero crew and vacant officer roles; missing expertise can reduce effectiveness but never hard-stops sailing. Injuries persist with serial IDs, cause/severity/recovery and deterministic hazards; infirmary care is visible. Release and every departure transition update the validated former-crew ledger and preserve exact-once identities. Loyalty/departure pathways are causal: sustained hardship yields a 24-hour desertion warning and an affordable retention response; aging to 65 starts a visible 30-day retirement notice; betrayal requires severe unresolved grievance, a free shore-side opportunity, and a saved 12-hour warning; critical health zero opens a six-game-hour rescue window, with care canceling death. Death returns collected supplies and cancels work before ledger transfer, and waits during fixture relocation rather than stranding equipment. State and legacy migration are save-validated. Generated society NPCs remain distinct from player crew and voluntary hiring never reads captives. Remaining integration limitation is the unrelated unstable `sim` test suite, detailed below.

Verification checkpoint: the complete `python -m pytest -q` suite now passes **574 tests** with **15 xfailed**. The earlier concurrent combat failures were resolved in the current worktree; the previous `sim` failures are also absent. `python -m compileall -q play.py pirates tools tests` passes, and `git diff --check` reports no whitespace errors (only existing LF/CRLF notices). Focused evidence includes 130 lifecycle/recruitment/ship-life/fixture/voyage/crew-detail tests, 84 generated society/NPC tests, and a 491-test non-sim suite; the full suite supersedes those partial counts. The save-free native walkthrough generated 13 screenshots, including an inspected lifecycle profile. The mentor regression confirms exact bounded XP gain, eligibility, no work-speed change, visible activity/audit reporting, and save/load continuation. Unrelated uncommitted work remains in the worktree and was preserved.

The native save-free previews at `C:/Users/Warp/AppData/Local/Temp/opencode/pirates-crew-recruitment` and `C:/Users/Warp/AppData/Local/Temp/opencode/pirates-crew-lifecycle` show the prospect board, recruit profile, and lifecycle warnings. Older test-count checkpoints below/history are superseded by the current verification checkpoint above. No combat or trade files were changed for unrelated work.

Core design decision: **there is no minimum crew requirement to sail.** A small crew can still go to sea. Missing expertise or exhausted/injured crew may make work, handling, or recovery less effective, but must not arbitrarily disable voyages. Crew headcount alone is not a sailing-speed bonus.

This plan extends (rather than replaces) `NPC-GENERATION-PLAN.md` and `PIRATE-NPC-STORIES-PLAN.md`. It connects the world’s finite population to the player crew only through deliberate, validated recruitment and departure transfers. Tactical-combat and shipbuilding feature work are out of scope.

## Current implementation (verified baseline)

- `pirates/ship.py` creates six named Wayward sailors. Sailors already have needs/health, six chore skills and labor priorities, personality axes, traits, memories, relationships, and practice XP. Work, traits, and needs affect their shipboard simulation.
- `pirates/voyage.py` has assignable navigator, quartermaster, and purser roles. The provisional sailing-force estimate uses navigator rigging skill; there is no crew-count minimum or direct headcount speed bonus.
- Voyage planning and underway simulation work with no current sailors and vacant officers; missing specialists reduce effectiveness but do not prevent sailing. Released/deserted/retired/betrayed/dead identities persist in a validated former-crew ledger; captain-directed release, warning-driven desertion, age/notice-driven retirement, warning-driven port theft, and untreated critical-condition death are live transitions.
- `pirates/npc_generation.py` creates persistent, seeded generated society people and organizations. Ordinary NPC crews are currently represented by aggregate vessel headcounts; notable named society people are not a player recruitment roster.
- `NPC-GENERATION-PLAN.md` explicitly defers player recruitment UI and individually simulated ordinary NPC crews. `PIRATE-NPC-STORIES-PLAN.md` requires finite population/resource transfers and causal outcomes.
- Port-based recruitment supports three authored finite candidates. Generated notable NPCs are still not recruitable; candidates are not procedurally regenerated. Injury/recovery, explicit release, motivated warning-driven desertion, deterministic retirement, warned opportunistic port betrayal, and a six-hour rescue-window death outcome are live. Native lifecycle presentation/longer-run balance audit remain.

## Design principles

1. **People are not interchangeable buffs.** Give each recruit a legible current ability, temperament, history, needs, relationships, and uncertain potential. No universal best build.
2. **Potential differs from current skill.** A young, disciplined, quick-learning novice may be promising but inexperienced; training takes time and opportunity. Obedience is not a bonus to every task and does not erase agency.
3. **Qualities have observable consequences.** Traits, skill, health, fatigue, morale, and relationships should change actual ship work, decisions, or outcomes; profiles should explain why.
4. **Finite people and honest accounting.** Recruitment, capture, release, departure, and death are explicit status/location changes. No person is duplicated, silently deleted, or drafted from the captive/enslaved-person market as a free recruit.
5. **No arbitrary drama.** Injury, desertion, betrayal, and retirement follow from causes, motivations, and state. Offer warning signs and player responses when feasible.
6. **Deterministic and save-safe.** Simulation and meaningful random outcomes must survive save/load and chunked time advancement; UI inspection must not mutate simulation state.
7. **Keep the scope staged.** Do not implement population aging/genealogy or simulate every ordinary sailor in the world as part of the first recruitment slice.

## Target player loop

1. Hear of or meet a finite candidate through a port, personal introduction, rescue, or another supported event.
2. Learn what is known: skills, background, conduct, loyalties, and a partial read on potential. Experience, references, and time together can reveal more.
3. Decide whether to recruit, what terms/support to offer, and whether the candidate fits the crew. Recruitment has a real cost and moves one person from a valid free source to the ship.
4. Assign work and officer duties. Observe the person's behavior and how it affects the ship; build trust through shared events.
5. Train or mentor the recruit through meaningful practice. Exceptional long-term value comes from growth, not an instant stat roll.
6. Maintain the relationship and respond to hardship. Injury, grievances, loyalty, changing ambitions, or a chance at another life may change the crew roster and the ship's story.

## Phased implementation

### Phase 0 — Ownership, baseline, and contracts

- Inspect current branch/worktree, save versions, Sailor serialization, generated population ledger, port economy, ship-life update, voyage roles, crew UI, and relevant tests before schema changes.
- Define the authoritative owner and valid transitions for each person: free candidate, active crew, captured/interned, former crew/departed, retired, or dead. A person has one location/status at a time.
- Define a finite, explicit source of **free** recruit candidates (for example, a bounded port hiring pool or named referrals). Do not reuse `enslaved_people` or captives as voluntary recruits.
- Record baseline tests and make no assumptions from generated headcount totals about individual identities.

**Gate:** state ownership, save migration, candidate source, and no-crew-minimum behavior are documented before changing gameplay.

### Phase 1 — Finite candidate discovery and recruitment

- Implement one small vertical slice: discover/inspect a few candidates in a port or through a referral, then accept or decline a recruit.
- Persist stable identity and core profile; show known ability and uncertainty without hiding critical terms/costs.
- Model wages/provisions, berth availability, initial trust/loyalty, and any finite source transfer. Berth pressure may be a practical constraint but is not a sailing crew threshold.
- Include at least one “wonderkid” archetype: young/novice, disciplined or obedient in a bounded sense, high learning potential, low starting expertise, and a personal motive. The recruit is not guaranteed to stay or become an expert.
- Add visible history/reason for the recruit’s availability. No random instant candidate spawning during ordinary simulation.

**Gate:** accepted recruitment transfers exactly one valid person; declined candidates remain available according to explicit pool rules; invalid or unaffordable recruitment changes nothing; zero/small crew can still sail.

### Phase 2 — Shipboard agency and crew utility

- Reuse existing individual sailor work, needs, traits, skills, and relationships; avoid a parallel duplicate character model.
- Make role/work performance explainable: relevant experience, readiness, assigned priority, teamwork, and access affect actual work. Use current chores and officer assignments as the first useful roles.
- Add small, observable shipboard interactions and routines where they support existing needs and relationships. Social events should change state only when a real interaction occurs.
- Make relevant navigator/rigging skill, role fit, and crew readiness influence the provisional sailing estimate in a readable bounded way. Do not add raw crew-count scaling or a minimum complement.
- Give poor fits and difficult personalities tradeoffs rather than pure penalties: a less sociable sailor may tolerate solitude; a bold one may take risks; a perfectionist may trade speed for better repair outcomes.

**Gate:** tests demonstrate that changing an individual’s relevant quality changes a real outcome, while unrelated traits and total crew count do not create unexplained sailing bonuses.

### Phase 3 — Training, mentorship, and earned potential

- Define bounded per-skill XP and learning potential. Award growth for completed relevant work, training, and meaningful outcomes—not elapsed time or repeated menu use.
- Support deterministic on-the-job mentorship: an actively working same-labor crewmate at least two skill points ahead and within three tiles provides a bounded XP bonus; mentorship must not accelerate output and is visible in activity/events. This is opportunistic peer learning, not a separate teaching job.
- Let candidate potential be uncertain and gradually discoverable through references, observation, and practice. Potential is a learning bias/cap, not a free skill multiplier.
- Show skill progress and concise explanations in crew profiles. Preserve current trait and practice conventions where possible.

**Gate:** novice growth is visible, bounded, deterministic, saveable, and slower or faster for documented reasons; no XP farming via idle ticks or repeated assignments.

### Phase 4 — Health, injury, and recovery

- Introduce persistent injury as a consequence of supported hazards/events, with severity, cause, recovery needs, work restrictions, and treatment options.
- Connect care to ship facilities/supplies only where those systems actually support it. Injury should not secretly act as death or remove a person from the population ledger.
- Give the player reasonable choices: rest, treat, assign lighter work, or accept risk. Make prognosis legible but not perfectly certain.

**Gate:** injury and recovery persist across save/load, affect work in tested ways, consume finite supported resources, and never duplicate/remove the sailor.

### Phase 5 — Loyalty, desertion, retirement, and betrayal

- Track motivations and relationships in a small set of understandable dimensions (e.g. trust, loyalty, grievance, ambition); distinguish player loyalty from friendship and discipline.
- Make desertion require credible pressure and a feasible destination/opportunity. Provide accumulating signs and chances to address grievances before departure when possible.
- Treat retirement/departure as a person leaving active crew through an explicit outcome; allow respectful farewells, references, or a future encounter where supported.
- Betrayal must be rare and motive-driven, enabled by an actual opportunity, and preceded by discoverable evidence when feasible. Resolve the concrete act and its consequences exactly once; no “betrayal roll” detached from state.

**Gate:** each departure/betrayal moves a person to one explicit status/location, has a causal record and player-visible aftermath, and can be prevented or redirected by meaningful choices where appropriate.

### Phase 6 — Death and population closure

- Add death only after lifecycle status, rescue/treatment, save migration, and population ownership are stable. Define which hazards can kill, how recoverable emergencies work, and how the event affects possessions, relationships, and records.
- Never leave a dead NPC active in a roster, candidate pool, society organization, captive ledger, or ship. Avoid off-screen death without a modeled cause and report path.
- Keep death consequential but not a routine punishment for ordinary ship simulation.

**Gate:** tests prove exactly-once death transitions, no resurrection/duplication through save/load, and consistent reports/relationships/rosters.

### Phase 7 — Integration, balance, and narrative verification

- Add explicit versioned migrations for authored and generated saves. Validate references and all population/status ledgers before swapping in a loaded world.
- Keep ship crew distinct from generated notable NPCs until a supported finite event transfers a person between systems. Do not promote aggregate ordinary headcounts into invented individuals.
- Preserve important crew events separately from routine traffic so a recruit’s growth, rescue, injury, departure, or betrayal remains retrievable.
- Run fixed-seed short/medium/long simulations and inspect stories for both drama and quiet outcomes. Tune consequences from data rather than guaranteeing a particular event.
- Run relevant crew, voyage, society, economy, and save suites, the full test suite, compile checks, and a save-free native walkthrough. Preserve unrelated worktree changes.

**Gate:** deterministic, compatible and conserved crew stories work across save/load; UI reports actual transitions; full regression checks pass.

## Acceptance criteria

- Player can discover and recruit named, individually persistent candidates through a finite free-person source.
- A promising inexperienced recruit can become valuable through bounded training/mentorship; ability is not granted instantly.
- Individual crew qualities matter in real work and relevant voyage outcomes, with explanations and tradeoffs.
- No minimum crew requirement and no headcount-only sailing speed bonus.
- Injury and supported lifecycle outcomes are causal, persistent, legible, and exactly-once transitions; each person has one authoritative status/location.
- Captive/enslaved population is never silently treated as free voluntary recruitment.
- Existing authored crew, generated society, voyage, and save behavior remains compatible; deterministic tests and the complete suite pass.

## Deferred / non-goals

- Individually simulate all ordinary crew on every NPC vessel; retain bounded aggregate NPC headcounts unless a scoped feature needs more.
- Full genealogy, births, child-rearing, aging of the whole world, and dynastic succession.
- Guaranteed recruitment of specific archetypes or a guaranteed betrayal/death event.
- Tactical combat overhaul, shipbuilding work, and crew-count gates for voyages.
- Turning every personality axis into a separate stat modifier; prefer a few legible, tested causal effects.

## Key integration points

- `pirates/ship.py` — Sailor model, starter crew, work/needs/practice and save data.
- `pirates/voyage.py` — officer roles, sailing estimate integration, NPC fleet and population ledger.
- `pirates/npc_generation.py` / `pirates/society.py` — generated notable people, organizations, relationships, memories, and strategic outcomes.
- `pirates/management.py` — crew profiles, assignments, voyage management, and player-facing reports.
- `pirates/game.py` — world update hooks and validated save/load integration.
- `tests/test_crew_detail.py`, `tests/test_ship_life.py`, `tests/test_voyage.py`, NPC/economy/save tests — extend focused regression coverage.

## Re-entry checklist

1. Read this plan, `NPC-GENERATION-PLAN.md`, and `PIRATE-NPC-STORIES-PLAN.md`; inspect `git status` and preserve unrelated changes.
2. Audit existing serialization and population ownership before editing; run focused baseline tests.
3. Implement Phase 1 as a small vertical slice; do not begin death/betrayal before its prerequisites.
4. Add deterministic/conservation/save tests with each phase; run the full suite before completion.
5. Update this plan with verified status/evidence and retain explicit limitations.
