# Crew Leadership, Care, and Naval Combat Expansion

## Status

**Design and baseline audit complete; mental/command implementation underway.** This plan separates existing behavior from the proposed expansion. Preserve concurrent project work and update this checkpoint as phases land.

### Implementation checkpoint — command attributes and assignments

Crew and recruit profiles now have validated 0–100 mental attributes: intelligence, discipline, leadership, and tactical awareness. Candidate prospect details and onboard profiles display them; older save rows default from each canonical identity. The voyage role list now includes an optional appointed crew captain (separate from the player-controlled Captain). Captain intelligence contributes up to +0.05 to the voyage-plan denominator, lowering the displayed duration estimate by at most about 5%; no captain is required to sail. Team-leader discipline reduces sword-drill energy drain by up to 20%; leadership scales participant sword-practice XP from 0.05 to 0.15 per qualifying minute. Focused role/migration/recruitment tests pass; remaining UI integration and broader suite verification are pending.

### Implementation checkpoint — galley and treatment

The pre-existing galley is a real finite-input work loop, not a placeholder: it hauls 2 raw-food portions and 1 water, then adds 6 prepared meals. This expansion makes that recipe explicit in the chore explanation and adds a regression for the input/output contract. Full recipe choice/quality mechanics are not part of the initial slice.

The infirmary now consumes one finite medicine dose per care visit, grants an immediate 10-health/5-stress stabilization, then continues gradual health and injury recovery. Critical sailors cannot recover without reaching a designated infirmary with an installed medicine chest and available dose; treatment clears the rescue timer immediately. Medicine is a new spatial ship supply, initially stocked at 3 doses, sold as two-dose medical kits by Petra at Cinder Quay, and included in cargo allocation/storage mass. Legacy aggregate-store snapshots migrate medicine to zero (no retroactive resource grant); pre-existing stockpile snapshots cross-check the new summary. Care report shows dose stock; the sailor activity shows when medicine is missing. Focused lifecycle/storage/economy/room suites pass (83 tests).

### Ship-to-ship combat design

Start an explicit artillery engagement from a voyage sighting, before the existing continue/turn-back/boarding choices. Keep the voyage paused and persist a compact engagement state (target vessel ID, deterministic seed, exchange count, engagement log and terminal status) inside the saved sighting. Each player action chooses a loaded, installed port/starboard gun or disengages; a fired shot consumes its loaded charge. The target returns fire with one of its finite `cargo.shot` charges when available. Hits use a deterministic keyed roll and explainable bounded accuracy from loader `load` skill, appointed captain `tactical_awareness`/leadership, target condition and generated-NPC strategic strength where available. Player hits persistently reduce NPC vessel condition; enemy hits reduce player hull/rigging. Reloading remains a real existing crew job; firing never creates ammunition.

End the exchange when either party disengages, the target is forced to withdraw at a low-condition threshold, or the player's ship is critically damaged and must turn back. Do not create individual deaths, captures, loot, or population transfers in this artillery slice; disable/withdraw is a vessel state/outcome, not an excuse to mutate the NPC headcount. Generated NPC people/organizations stay under their existing owners, and tactical boarding remains a separate follow-up choice for a suitable weakened pirate ship. While a cannon engagement is active, keep the sighted NPC vessel fixed in the encounter rather than advancing it away mid-fight. Tests must prove deterministic outcomes, shot conservation, condition effects, retreat, save/load continuation, and unchanged normal sighting/boarding behavior.

### Implementation checkpoint — deterministic artillery prototype

Implemented the encounter action from the voyage sighting menu, including a resumable saved exchange screen, real installed/loaded cannon validation, finite player/NPC shot use, deterministic BLAKE2-keyed hit rolls, load-skill/appointed-captain effects, persistent NPC condition and player hull/rigging damage, automatic opposing volleys, wait-for-reload time, and forced enemy withdrawal/player retreat outcomes. NPC movement/strategy is held during an active artillery exchange so the target cannot leave mid-fight. Save restoration validates combat state and target identity against the restored NPC fleet. Focused integration run across voyage, lifecycle, recruitment, ship life, rooms, storage and economy passed **169 tests**; cannon determinism/save-resume/withdrawal/UI regressions passed separately (3 tests). Full suite remains pending.

## Verified current behavior

- `pirates/ship.py` has six ship-labor skills (`repair`, `bail`, `cook`, `clean`, `rig`, `load`) on a 0–10 scale, personality dimensions (`diligence`, `sociability`, `resilience`, `curiosity`), traits, needs, memories, relationships, and earned practice.
- `pirates/voyage.py` assigns unique navigator, quartermaster, and purser roles. It supports captain free-time actions and team orders. A team leader is currently recorded/displayed but does not confer a leadership effect. There is no appointed in-world captain/shipmaster attribute model for crew.
- The current galley loop is already functional at a basic level: a cooking job reserves/hauls 2 `raw_food` and 1 `water`, then produces 6 `meals`; low meal stock queues work automatically. Meal stores and cook jobs persist. Cooking is presently one recipe and has no quality/recipe choices.
- An installed medicine chest in a designated infirmary provides passive health and injury recovery when a sailor seeks care. Treatment does not consume a medical item; the room report explicitly says there are no passive designation bonuses.
- Two physical cannon stations each hold one loaded charge. Loading is a real crew job that consumes one `shot` from ship storage. Cannon firing at another vessel is not implemented.
- `VoyageSystem` pauses an underway trip at a ship sighting; the player may continue or turn back. The separate tactical combat arena handles close-range boarding, not ranged ship artillery.
- Generated society NPCs and aggregate NPC ship crews are separate from the player ship's persistent crew. Do not silently transfer or invent individuals across those systems.

## Design decisions

1. Add command/mental capabilities as a compact, understandable stat set (candidate dimensions: judgement, discipline, leadership, and tactical awareness). Keep them distinct from personality and labor skill; choose the smallest set that produces tested consequences.
2. Let the player assign a captain and crew-team leaders explicitly. Leadership can improve coordination, readiness, or a relevant combat/order result, but never grants a raw headcount speed bonus or imposes a minimum crew requirement. A crewless ship remains sail-capable.
3. Preserve the existing finite galley input/output contract unless new recipes are added with explicit, conserved inputs and outputs. Avoid free meal creation or cosmetic-only cooking jobs.
4. Medical care must be an explicit, visible workflow. Define and consume a finite treatment supply (prefer an existing catalog item where suitable), require reachable medicine-chest/infirmary access, and preserve the existing passive recovery expectations only where deliberate. Critical-rescue treatment must integrate with the crew-life rescue timer.
5. Keep naval gunnery in the sea-voyage layer, separate from tactical boarding combat. Use actual installed cannon stations, loaded charges, range/target state, crew loading/gunnery skills, ship readiness and deterministic saved combat state. Damage must affect persistent ship condition and be repairable through existing systems.
6. Use validated save migration, deterministic advancement independent of time chunking, finite ammunition/treatment resources, causal logs, and tests for every transition.

## Phases and acceptance gates

### Phase 1 — Mental attributes and command assignments

- Define, document, and migrate the command stat schema for existing and recruited sailors.
- Expose the relevant values in candidate/crew profiles without implying they are labor skill.
- Allow unique, validated captain and team-leader assignments; show who leads what and why it matters.
- Apply only bounded, explainable effects to an actual supported order/workflow. Keep old saves and vacant roles valid.

**Gate:** tests cover exact effects, valid/invalid assignment, reassignment/departure, legacy-save defaults, deterministic continuation, and crewless sailing.

### Phase 2 — Cooking and provisioning

- Audit the existing cook job and make its ingredients, output, recipe, and progress legible in the galley UI.
- Add recipes/quality or leadership effects only when finite inputs and outputs can be reconciled and saved.
- Keep meals usable by crew needs and voyage ration estimates.

**Gate:** end-to-end cook work consumes exactly its reserved ingredients, creates the documented meal output once, survives interruption/save/load, and rejects unavailable galley/supplies without loss or duplication.

### Phase 3 — Medical treatment

- Add a finite treatment supply and explicit treatment work/action using reachable infirmary equipment.
- Define how treatment affects injury recovery, health, and the critical rescue deadline; expose supplies, prognosis, and results to the player.
- Preserve population identity and avoid treatment resources appearing through room labels or UI inspection.

**Gate:** tests cover treatment success/failure, resource accounting, work interruption, injury recovery, critical rescue/death boundary, and save migration/continuation.

### Phase 4 — Ship-to-ship cannon combat

- Define engagement state, range/readiness, target selection, aiming/firing cadence, hit/damage model, retreat/continue/turn-back choices, and terminal outcomes before coding.
- Make the player issue clear gunnery orders and use actual loaded cannons and finite shot. Relevant skill/command stats affect documented factors only.
- Persist encounter progress and apply damage to hull/rigging with causal combat logs; allow repair, withdrawal, surrender, or boarding where supported. Do not conflate ranged fire with the tactical arena.
- Resolve NPC fleet interactions through supported NPC ship state without duplicating or destroying crew identities.

**Gate:** fixed-seed engagements are repeatable across save/load and chunk sizes; ammunition and ship-condition changes reconcile; player orders, misses/hits, damage, withdrawal, and end states are inspectable; existing voyage/boarding behavior remains intact.

### Phase 5 — Integration and verification

- Audit UI, event reporting, old-save defaults, identity/resource conservation and generated-NPC boundaries.
- Run relevant focused suites, full `python -m pytest -q`, compileall, diff checks, and save-free native previews for leadership, care, and naval combat.

**Gate:** complete repository suite and compile checks pass; previews reflect the actual persisted systems; unrelated changes remain untouched.

## Baseline and verification record

At plan creation, the worktree already contains extensive uncommitted shipbuilding, trade, generated-NPC, simulation, and tactical-combat work. The previous crew/NPC goal's last verified full suite was **574 passed, 15 xfailed**; this is baseline context, not evidence for the new expansion. Relevant baseline source locations: `pirates/ship.py` (labor, cooking, needs and health), `pirates/voyage.py` (officers, free-time/team orders, encounter interruptions), `pirates/ship_spaces.py` (infirmary/magazine reports), `pirates/ship_physics.py` (sailing force), `pirates/management.py` (profiles and UI), `pirates/ship_fixtures.py` (medicine chest), and `tests/test_voyage.py`.
