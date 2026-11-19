# Sea Encounters and Cooperation — Proposed Plan

Date: 2026-11-19
Status: approved release-one implementation delivered; full combat consumption and physical NPC-deck transfer remain integration work.
Parent: `PIRATES-OF-MISKA-MASTER-PLAN.md:43,60,142,260`.

## 1. Scope and player decision

When another vessel appears, choose whether to communicate at range, ignore it, evade, approach and negotiate, cooperate, open cannon fire, or attempt to cross aboard. These are distinct intentions, not different labels for one combat button.

This slice owns the approach and encounter decisions. It stops at a durable combat handoff. Combat, cannon damage, boarding combat, injuries and combat outcomes belong to the separate combat work. Story generation and strategic faction simulation remain parallel authorities. Magic is excluded; land expeditions come later.

Confirmed scope decisions (2026-11-19):

- "Jump over" means the captain personally crosses. The captain may cross alone, take named escorts, or send everyone over.
- Any crossing that is not clearly welcomed becomes a boarding request; the combat system then takes over entirely.
- Cooperation variants — allied ships joining forces, shared objectives, different kinds of cooperation — belong to the faction system and are out of scope here. This slice only detects and displays relationship/eligibility information supplied by that authority.
- Combat ownership is total after handoff: no cannon resolution, boarding fighting, injury, capture or outcome is simulated in encounter code.

## 2. Verified starting points

- `pirates/voyage.py:1000` — `_encountered_ship` validates the active stable-ID contact and its current node.
- `pirates/voyage.py:1013` — `hail_ship` records a signal exchange and advances canonical time; currently rejects every pirate-threat vessel and uses a permanent hailed flag. Neither is sufficient for negotiated recurring encounters.
- `pirates/voyage.py:1034` — `assist_ship` performs a finite iron transfer, bounded repair and one-time contact update; reuse its accounting principles rather than granting free assistance.
- `pirates/voyage.py:968` — current authored vessels already carry faction display labels. These labels are not proof of a complete diplomatic relationship system.
- Master plan §4 explicitly identifies faction simulation as existing/parallel work; §5 separates fame, conduct reputation and specific relationships. Audit before introducing any new authority.

## 3. Boundaries and integration ownership

Before implementation, agree an interface with the parallel simulation and combat work:

1. Stable vessel, captain, organization/faction and encounter IDs; distinguish legal owner, operational controller, captain and allegiance.
2. Faction authority supplies membership, relationship stance, war/truce/treaty restrictions and provenance/version. Encounter code must not infer alliance from matching display names or a pirate/marine category.
3. Encounter code owns local contact state, observed signals, approach geometry, offers and accepted encounter-scoped cooperation. Strategic diplomacy and story/event interpretation remain external.
4. Publish factual encounter events with causal IDs for the simulation to consume once; do not independently apply the same relationship consequence in two systems.
5. If the live simulation bridge is unavailable, use explicitly authored fixture data behind the same adapter for tests. Do not present fixture factions as an integrated living world.

No global approval meter, independent faction generator, fleet ownership system or universal NPC planner is required.

## 4. Encounter state and distance

Proposed lifecycle:

`sighted → distant contact → closing / separating → close contact → peaceful resolution / cooperation / lost contact / combat pending`.

Track stable participants, encounter ID, canonical timestamps, relative separation/bearing, observed identity confidence, signals, current declared intentions, offer status, departure/escape progress and any pending handoff. Range labels must derive from one geometry model, not independent UI flags.

Phase 0 must decide whether existing navigation supports quantitative relative movement. If not, specify an honest bounded approach model with explicit range transitions; do not claim tactical sailing physics. Wind, speed, damage and crew effects enter only through verified existing authorities or separately approved bounded rules.

The other captain can refuse, keep distance, follow, approach, leave or threaten. Ignoring a ship does not freeze it; choosing evade does not guarantee escape. No hidden NPC intention or exact unseen cargo/power leaks into player options.

## 5. Player choices

| Choice | Required behavior |
| --- | --- |
| Signal from afar | Release one offers bounded identify, intentions and warn-off signals at distant range. Each subject is exchanged once; replies may be answered, refused or unanswered, and cost one canonical minute. This is not unlimited dialogue and does not expose hidden cargo/mission. |
| Ignore / maintain course | Explicitly hold course and resume the existing route without inventing relationship/economy effects. The sighted vessel is reserved during the active encounter; wider autonomous following behavior is outside this slice. |
| Evade / break contact | Deterministic encounter-local separation attempt costs five canonical minutes. Failure can close a distant contact; success ends contact and resumes the route. No relative speed/bearing or tactical sailing geometry is claimed. |
| Approach and negotiate | Deterministic five-minute approach can be refused or move the encounter-local range to close. Being close is not consent to board. Close parley costs one minute per topic and records only encounter-local consent/refusal. |
| Fire cannons | Require an installed loaded cannon and explicit hostile confirmation, then create a durable cannon request. Do not consume shot, resolve firing, hit chance, damage or retaliation here; artillery range/arc belongs to the combat authority. |
| Cross aboard / jump over | Captain personally requests a crossing alone, with named living escorts, or with everyone at close encounter range. No NPC deck/transfer geometry exists, so a welcomed transfer remains pending; any unclear or refused crossing creates a boarding-combat request naming exactly who would cross. No teleportation, fighting or damage is resolved here. |
| Propose joining forces | **Out of scope.** Deferred to the faction system. Encounter code may display authoritative faction/relationship information but must not invent cooperation types, command, shared objectives or fleet behavior. |

Each unavailable choice must name its observed blocker. Show time, resource and commitment consequences before confirmation; cancellation is free unless a disclosed action has already occurred.

## 5A. Phase 0 audit (2026-11-19)

Measured before changing behavior.

### Current encounter path

- `pirates/game.py:231-245` — `on_ship_second` asks `NpcFleetSystem.advance_second` for a sighting only while the voyage is `underway`, then passes it to `VoyageSystem.step_second`.
- `pirates/voyage.py:1063-1097` — the fleet pulses once per simulated hour, moves ships node to node and returns a first sighting when a ship is within 1.25 chart units. `first_sighting` is permanent per ship per save, so one vessel cannot currently generate a second contact.
- `pirates/voyage.py:436-444` — a sighting is only accepted after 1800 voyage seconds, sets `status = "encounter"` and pauses the voyage.
- `pirates/voyage.py:566-587` — `stop_after_encounter` / `interrupt_current_advance` pause advancement; `advance_encounter_action_time` charges deliberate actions through `world.advance`.
- `pirates/management.py:1035-1104` — current choices are: begin/resume cannon exchange, repel a pirate boarding party, hold course, turn back, hail (1 minute) and one repair aid. There is no approach, evasion, close parley or captain crossing.

Measured on the authored fleet, counting contacts until arrival or an 8-hour horizon:

| Route | Simulated | Sightings | Detail |
| --- | --- | --- | --- |
| Verdant → Cinder | 2.86h | 1 | Rogue pirate brig at Patrol Lane, minute 120 |
| Verdant → Patrol Lane | 3.52h | 1 | Marine patrol, minute 180 |
| Verdant → Freeport | 8.00h | 3 | marine minute 180, syndicate cutter minute 240, jackal minute 480 |
| Verdant → Deepwater | 4.86h | 1 | marine patrol at Shoals, minute 60 |

Ordinary routes therefore produce roughly one contact, all inside the first eight hours. This is a geometry/cadence baseline, not a balance guarantee; recurring contact would need a deliberate cooldown change, not an accidental one.

### Range and movement

The overworld is strategic: `map_coordinate` interpolates a chart leg (`pirates/voyage.py:191-203`) and there is no relative bearing, speed or separation against a sighted vessel. `step_second` moves the player's ship, not the contact. Therefore approach/evasion cannot reuse physics as if it existed. Release one adds an explicitly encounter-local two-stage range (`distant` → `close`) with time costs and deterministic outcomes; it makes no continuous-range or tactical-sailing claim. Shared range/position with the separate naval work remains an open integration item.

### Faction and relationship authority

`pirates/society.py:96-100,111-160` owns authored/generated organizations and inter-organization relationships. `pirates/npc_generation.py:541-554` gives organizations ids, names, kinds, leaders and vessels; generated ships carry an organization identity while authored ships use display faction labels.

Important gap: the society has **no player/captain entity**, so there is no captain-to-faction stance to read. Encounter text must not claim "they like you". It may show the vessel's faction/organization identity, its known kind, and the organization's public reputation; willingness is derived from observable profile (threat class, mission, condition, crew) plus deterministic authored rules. Any real captain-faction relationship needs the parallel faction work.

### Crossing and combat ownership

- `pirates/ship_boarding.py:44-66` resolves only **NPC boarders entering the player's ship**, breaching the player's hull and routing to the player's tile. There is no NPC ship deck or player-initiated boarding map.
- `pirates/voyage.py:688-776` is an existing, complete artillery authority: `begin_naval_combat` creates a seeded combat record once and `cannon_exchange` consumes loaded charges and resolves hull/rigging/condition damage.
- `pirates/management.py:754-798` drives that artillery exchange; `world.combat.start_boarding` drives the legacy arena for pirate boarding.

Decision: new cannon fire and refused/unclear crossing intents stop at one durable `encounter.contact.combat_pending` request; encounter code does not call the existing artillery authority or consume shot. An already-active `encounter.combat` record in a legacy save retains its resume path. Player-initiated crossing has no NPC deck/transfer consumer, so a welcomed request remains peaceful and pending and moves nobody. The request ID is stable for the encounter/cause/tick/participants/action and its payload carries participants, initiator, target, chart-position/range snapshot, requested action and canonical tick.

### Save and validation gaps

- `pirates/voyage.py:830-854` validates voyage status and requires an encounter dict, but only `:918-928` validates encounter combat state. New contact state needs its own strict validation and migration.
- `pirates/voyage.py:1299-1310` strictly validates `player_contacts` (hail/aid), which stays a persistent factual contact ledger; per-encounter signals should not overload it.
- Old saves must load an encounter with a default `distant` contact and no invented permissions or hostility.

### Locked implementation choices

- Contact state lives inside `voyage["encounter"]["contact"]`; cannon keeps `encounter["combat"]`.
- Time costs: signal 1 minute, approach 5 minutes, evade 5 minutes, parley 1 minute, crossing request 1 minute; all through `advance_encounter_action_time`.
- Evasion is deterministic from a hash of encounter id + tick + ship condition/rigging/speed vs target condition/crew; a failed attempt keeps the contact and spends the time.
- Approach succeeds into `close` for non-pirate contacts; pirates allow approach too (they want the prey) but refuse peaceful parley. Close parley still requires the other captain's consent per topic.
- Crossing manifests: `alone` (captain only), `escorts` (captain plus selected crew), `everyone` (all living crew). Invalid or duplicate ids are rejected.
- No movement, ammunition use, hull damage or combat result occurs in encounter code.

## 6. Close parley (encounter-local)

Close contact allows a bounded conversation, not an alliance. Permitted subjects are encounter-local: exchange or challenge identity, ask intentions, warn off, request safe separation, accept a stand-down, or ask permission for a personnel transfer. Each result is scoped to this encounter and expires when contact ends; nothing here creates a treaty, shared objective, escort contract, fleet command or lasting cooperation.

Distinguish faction stance and personal relationship when displaying why the other captain sounds friendly, wary or hostile. Those values are read from the faction authority. A hostile-faction vessel is not automatically incapable of parley, and a friendly one is not obliged to agree.

Keep offers bounded. Any payment or cargo transfer requires a supported recipient account, atomic acceptance and conservation tests; defer unsupported barter, ransom, tribute and profit sharing rather than invent sinks. Reuse the existing aid transfer rather than promising a diplomatic reward that no authority implements.

If the conversation reaches a combat boundary, stop and create the combat request. Do not resolve the clash.

## 7. Combat handoff contract — hard stop

Proposed request fields: unique request/encounter IDs, cause (`cannon_fire`, `forced_boarding`, or hostile interception), stable participant IDs, initiator, observed declarations, authoritative position/range snapshot, requested action, relevant cooperation IDs and canonical timestamp/version.

Creation is idempotent and persists as `combat_pending`. This slice neither calls legacy combat automatically nor silently selects a combat mode. While pending, no continued peaceful actions, duplicate firing, ammunition debit or crossing can occur. Save/resume restores the same request.

Until the combat consumer exists, show “Combat handoff pending — resolution belongs to the combat system” and stop encounter progression; do not fabricate an escape, win, loss or resumed voyage. Define consumer acknowledgement and exactly-once result application jointly with the combat work before enabling a runtime bridge. Hostility consequences distinguish a declared threat from an actual shot; simulation must not be told that damage occurred when only a request exists.

## 8. Time, persistence and information

- Reuse `World.advance` and voyage encounter-time advancement; payroll, deadlines, food and existing weather/time hooks continue normally.
- Ensure encounter action time does not recursively create duplicate contacts or allow the same vessel to be both at a strategic node and locally controlled by incompatible authorities. Specify a bounded reservation/ownership protocol with the simulation adapter.
- Manual and accelerated advancement must reach equivalent encounter results for equivalent declared actions and seeds; rendering/inspection advances nothing.
- Distinguish unknown vessel, observed flag, claimed identity and verified identity. Show the source and age of relationship/identity information.
- Preserve migrated saves without invented treaties or historic hostility. Repeated hails in a new encounter are valid; repeated offers/transfers within one encounter must not duplicate effects.
- Arrival reporting records actual time/costs, contact outcomes and unresolved commitments, not guaranteed rewards.

## 9. Phased delivery and gates

### Phase 0 — integration and lifecycle audit
Trace encounter triggering/resumption, existing avoid/continue choices, relative geometry, navigation/readiness, NPC movement and identity, faction/relationship authorities, contact persistence and combat entry points. Agree ownership and handoff contracts with parallel work. Measure current encounter frequency and approach/escape times before tuning.

### Phase 1 — nonviolent encounter lifecycle
Implement read-only sighting information, distant signaling, ignore, approach, refused approach and evasion. Test recurring contacts, time, interruption, lost visibility and save/resume. No combat bridge yet.

### Phase 2 — close parley
Integrate faction/relationship read access and consent-based close contact: challenge identity, ask intentions, warn off, accept stand-down and request permission to transfer personnel. Verify refusal, expiry at separation and no invented alliance or shared objective.

### Phase 3 — crossing intents
Expose the captain's crossing choice — alone, named escorts, or everyone — and the welcomed-transfer path. Any refused or unclear crossing becomes a boarding request. No personnel are moved by a request, and no clash is resolved.

### Phase 4 — combat-pending boundary
Expose cannon-fire and crossing intents with honest prerequisites, explicit hostile confirmation and durable idempotent requests. Stop there. No combat or cannon/boarding resolution implementation.

### Phase 5 — integrated verification
Exercise the full peaceful loop, refusal, ignore-while-followed, failed evasion, parley, welcomed transfer, cannon request, solo/escort/whole-party boarding requests and a reloaded pending request. Verify old saves, exact-once side effects, equivalent time advancement, conservation and observation privacy. Capture native screens at 1280×800, 960×700 and 640×600. Report focused tests, compile/whitespace and repository-suite status honestly; scripted captures are not unaided playtests.

### Implementation record (2026-11-19)

Delivered in `pirates/sea_encounter.py`, `pirates/voyage.py`, `pirates/management.py` and `pirates/game.py`; regression coverage is in `tests/test_sea_encounters.py` and `tests/test_voyage.py`. `tools/preview_sea_encounters.py` is the save-free native capture harness.

- Contact state lives in `voyage["encounter"]["contact"]`: encounter-local `distant`/`close` range, bounded attempt counters, one signal per subject, encounter-local parley, a peaceful crossing request or one `combat_pending` handoff. Old sightings get a fresh distant contact; strict restore validation rejects malformed state, unknown/non-living participants, invalid manifests, bad positions and forged request IDs.
- Signal/approach/evasion/parley/crossing actions cost 1/5/5/1/1 minutes through `advance_encounter_action_time`; withdrawing a peaceful crossing is free. Tests measure approach/evasion as 300 ship-seconds. Baseline frequency remains unchanged: Verdant→Cinder 1 contact/2.86h; Patrol Lane 1/3.52h; Freeport 3/8h; Deepwater 1/4.86h. No tactical sailing geometry or continuous relative motion is claimed.
- Signals distinguish answered/refused/no-answer and never disclose cargo/private mission. Lookout information reports observed vessel/condition/crew estimate, claimed colours, identity confidence, source/age and current shared-registry organization/kind/public reputation. The registry has no captain-to-faction stance; none is fabricated. Private mission/cargo leakage is tested.
- Approach can be refused and stays distant; accepted approach becomes close but is not consent. Evasion may fail (a distant contact closes) or succeed (contact ends and voyage resumes). Close parley is consent-based and contact-local; warn-off, stand-down and transfer permission can be refused. No alliance, command, shared objective or lasting organization change is created.
- All crossing manifests include the captain: alone, selected living named escorts, or captain plus every living crew member. A clearly welcomed transfer remains a peaceful pending request; refused/unclear crossing creates `combat_pending` with the exact proposed manifest. Nobody moves because no NPC deck/player-initiated transfer scene exists.
- New cannon intent requires an installed, loaded station and explicit UI confirmation, then persists as `combat_pending` without time, shot, damage, retaliation or combat-state mutation. Stable request IDs make same-intent retries idempotent; pending requests block further encounter actions/departure. Only already-active legacy artillery saves retain their old resume path. The active contact's vessel is reserved from hourly NPC movement.
- `tests/test_sea_encounters.py`, `tests/test_voyage.py` and `tests/test_voyage_save.py` pass 80 tests. Compile and whitespace checks passed. Native preview generated seven screens × 1280×800/960×700/640×600 under `C:/Users/Warp/AppData/Local/Temp/opencode/sea-encounters-verified`; the scenario captures were inspected. After final handoff-row extraction/regeneration, cannon handoff was rechecked at all three sizes and crossing handoff at 640×600. Scripted captures are not unaided playtests.
- Compact 640×600 and 960×700 handoff captures keep encounter text readable, but underlying game/status text remains visible through or around the overlay; this is a presentation limitation, not a clipping-free full-screen modal.
- Repository suite: 1,506 tests collected, with the two decade-scale tests deselected. The server restart cancelled its shell; captured output stopped at 90% with failure markers and no final pytest summary. The original PID is no longer present. No repository-wide pass or failure count is claimed; the final suite result remains unavailable.

Known limits recorded honestly:

- A welcomed crossing is a peaceful pending request because no NPC ship deck or player-initiated transfer scene exists; it does not teleport anyone. A combat consumer/acknowledgement bridge also remains future integration work; this release persists/displays the request only.
- Cannon range/arc gating remains the combat system's decision; encounter range is not forced onto artillery.
- `first_sighting` is still permanent per vessel per save, so the same hull cannot generate a second contact without a deliberate cooldown change. Different vessels can still produce repeated contacts.

## 10. Decisions to confirm before implementation

Settled on 2026-11-19:

- "Jump over" means the captain personally crosses; escorts may accompany, or everyone may be sent.
- Combat takes over completely once a clash begins.
- Cooperation types and allied joining belong to the faction system; this slice only reads relationship/identity data and ends at the handoff.

Still open:

- What ship geometry/navigation will the separate naval combat work expose? Encounter range is encounter-local (`distant`/`close`); shared tactical geometry is still not defined.
- Which faction adapter and event-consumption contract will the parallel simulation provide? Release one reads organizations directly and writes nothing.
- What noncombat welcomed-transfer mechanics already exist? None: no NPC deck or player-initiated transfer scene, so a welcomed crossing is a peaceful pending request.
- Does the combat handoff accept a personnel manifest (solo/escorts/everyone), and who owns validating that manifest? Release one validates manifests locally and stores them in the pending crossing request; the future consumer must honour the same shape.

## 11. Completion criteria

- Distant communication, ignore, approach/parley and evasion are distinct, consequential choices.
- Other captains retain bounded agency; factions/relationships use shared authoritative identities and never imply command or cooperation.
- The captain can attempt to cross alone, with named escorts, or with everyone, gated to close encounter range. Welcomed transfer remains pending until an NPC deck/transfer scene exists.
- Cannon attack and any refused/unclear crossing stop at a saved, exactly-once combat request; no combat result is resolved here.
- All costs, time, eligibility, refusals and uncertainty are visible and tested, with no hidden-state leakage or fabricated outcomes.
- Migration, time equivalence, negative cases, native evidence and plan/global-skill handoff are recorded.
