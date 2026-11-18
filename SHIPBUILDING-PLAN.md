# Shipbuilding: a floating fortress

Design specification, not a claim that these features already exist.

## Principles
- Build irregular ships tile by tile across multiple decks, using ASCII.
- Structure, fixtures, actors, cargo, water and work occupy actual space.
- Mass, buoyancy, support, sailing and logistics follow the constructed vessel.
- Bad but representable designs are allowed; explain consequences rather than granting room bonuses.
- Planning pauses time. Confirmed work requires materials, hauling, tools, access and labor.
- Damage modifies the same components that construction creates.

## Spatial model
Use ship-local integer x/y/z coordinates, separate from world position and heading.
Store floor, barrier, support and fixture separately; items, occupants and hazards are separate state.
Materials carry mass, strength, damage and fire properties. Fixtures have footprints and working clearances.
Render one deck with optional adjacent-deck ghosts and overlays. Proposed glyphs: sea ~, floor ., wall #,
door + or /, reinforced bulkhead =, up <, down >, shaft O, cannon C, mast M, bunk b, stockpile %.
Deck names are labels, not mandatory classes. Diagonal contact alone is not structural support.

## Construction
Provide paint, line, rectangle, flood-fill, copy, rotate, mirror and symmetry tools.
Unbuilt plans support undo; completed structures must be dismantled physically.
Jobs reserve materials and work locations; hauling precedes installation. Workers build from reachable
adjacent positions. Dependencies include foundations, supports, scaffolding and clearance.
Reject actor entombment, overlaps and impossible operations; warn about dangerous but valid layouts.
Dry dock permits hull/keel reshaping, mooring permits supported renovations, underway work is restricted.
Opening submerged hull admits water immediately. Rebuilding requires usable working conditions.

## Structure and rooms
Calculate support through keel, ribs, beams, bulkheads and pillars. Check spans, loads and connections.
Heavy guns and masts require foundations. Failed connections can detach sections.
Compute physical compartments from barriers separately from player-designated functional zones.
Rooms require actual usable equipment and supplies: galley, mess, berths, infirmary, brig, magazine,
workshop and hold. Removing walls merges spaces without deleting intended-use zones.

## Crew and defenses
Replace fixed station coordinates with placed fixtures and reachable work positions.
Every reload, meal, repair and treatment includes actual supply journeys and work time.
Model congestion, door operation, stairs, carried bulk, yielding and destination reservations.
Provide route, traffic, unreachable-station and emergency-response overlays.
Defenses include vestibules, protected stairheads, firing positions, reinforced bulkheads and lockdown.
Enemies can breach, burn and bypass; locks are not faction-forcefields. Mazes inconvenience crew too.
No instant rebuilding through occupants to entomb enemies.

## Physics
Count structure, fixtures, cargo, actors, ballast and floodwater in total mass and center of mass.
Calculate displaced volume, draft, freeboard, trim, list and approximate stability from hull geometry.
Track wetted surface, waterline length, beam, submerged shape and windage.
Propulsion depends on sail area/condition, wind angle, rigging, crew and supported masts.
Steering depends on rudder placement, area, condition and vessel motion.
Use acceleration = (drive - resistance) / mass; sustainable speed balances drive and resistance.
Use a tunable approximate force model, not an asserted engineering-accurate solver.
Explain performance under stated wind and load. More armor adds load; high mass hurts stability;
asymmetric loading causes list; extra sail adds drive and heeling; guns require foundations and labor.

## Hazards
Local damage creates breaches. Flow depends on opening size and pressure; water travels through
doors, holes, stairs and hatches. Distinguish watertight bulkheads from ordinary doors.
Pumps require capacity, outlets, access and power/labor; bailing requires actual discharge trips.
Conserve transferred water and account for floodwater mass/free-surface effects.
Use coarse connected compartments for flooding, smoke and ventilation rather than expensive fluids.
Burning consumes actual fuel and harms actual structure; powder danger comes from stored contents.

## Inspector
Show categorized mass, loading margin, draft, freeboard, list, trim, stability, structural overloads,
speed estimates, steering/sail effectiveness, service access and escape routes.
Warnings include exposed powder, long supply routes, single exits and poor balance.
Hard errors include overlaps, impossible access and invalid connections. Launch overrides cannot
override broken simulation invariants.

## Integration
Replace fixed STATIONS/BUNKS/ROOMS and aggregate stores in pirates/ship.py incrementally.
Extend actor/job locations with ship identity and z. Preserve physical supply reservations.
The renderer is a projection, never authoritative structure. Preserve the copied engine snapshot.
Version saves and migrate the starter vessel; persist geometry, damage, fixtures and designations.
Suggested boundaries: ship_model, ship_building, ship_spaces, ship_navigation, ship_physics,
ship_hazards, ship_editor. Introduce modules as required, not empty scaffolding.

## Implementation sequence and acceptance
1. Authoritative component model: materials, layers, identifiers, validated serialization.
   Then migrate the starter ship, station access and world/save adapters without breaking crew life.
2. Single-deck builder: irregular geometry, fixtures, demolition, physical construction, undo plans.
   Acceptance: altered walls change actual routes; changes survive reload.
3. Multiple decks: stairs/hatches, vertical hauling, support and span warnings.
   Acceptance: a functional two-deck vessel without actor/material teleportation.
4. Physics: mass distribution, displacement, balance, drive/resistance and steering inspector.
   Acceptance: equal-condition designs differ measurably for explainable reasons.
5. Damage: local breaches, compartment flooding, pumping, repairs, coarse fire/smoke.
   Acceptance: compartment layout changes survival after identical damage.
6. Boarding: breaching, combat routes, lockdown and alternate approaches.
   Acceptance: defenses help while retaining crew costs and enemy counterplay.
7. Polish: blueprint library, copy/mirror tools, traffic overlays and equal-condition trials.

First playable target: editable two-deck sloop, real construction jobs, movable stations,
functional zones, multi-deck crew routes and a basic mass/buoyancy inspector.

## Current implementation status
- Live equipment now has persisted identities/ship-local coordinates in ship_fixtures.py; crew stations,
  assigned bunks, room reports, inspection records, construction checks and render markers use the
  registry rather than starter constants. Optional fixtures records migrate older saves automatically.
- Registry verification: 115 tests passed, including relocated galley job targets/equipment reporting,
  relocated assigned-bunk save/load, legacy registry migration and invalid/overlapping placements.
  Native SDL smoke passed and pirates-fixture-registry.png was inspected.
- Player-facing equipment relocation is now Enter > Relocate equipment: exclusive carpentry
  jobs unfasten, haul and refasten existing fixtures. Heavy equipment moves more slowly; carried
  fixtures are absent from the source map and unavailable for work/needs. Source/destination
  reservations, occupancy/access rechecks and reciprocal carrier audits prevent duplication.
- Cancellation, reassignment and urgent need trips physically return equipment to its source.
  Mid-haul saves preserve carrier/state. Active work targets follow installed fixtures; terminal
  history keeps original positions. Stocked hold relocation awaits the spatial-storage task.
- Relocation verification: 134 tests passed including the paused equipment UI regression; native
  preview_relocation.py moved the galley under normal crew simulation in 74 seconds. Pickup,
  installed and actual-equipment-report screenshots inspected. Tackle/roller mass-scaled hauling
  is provisional; team handling and foundations remain part of later physical-system refinement.
- Spatial storage now replaces aggregate authority with hold/water fixture-bound and floor stockpiles.
  Existing `ship.stores` access is a read/write totals mapping view over pile contents for compatibility;
  reserved and carried supplies are excluded. Version-3 saves without `stockpiles` migrate totals into
  piles at the current fixture positions, including a moved hold.
- Enter > Storage / hauling supports empty floor-pile designation/removal, inventory/capacity inspection
  and source/destination/resource/quantity transfer orders. Jobs reserve exact source piles, visit all
  required pickups, and deliver or physically return each bundle. Destination capacity and source
  reservations are protected against duplicate orders. Recipe inputs can span multiple reachable piles.
- Transfer loads are limited to 50 kg; carried mass increases travel delays/energy. A stocked hold's
  cargo mass slows equipment relocation, follows its fixture, and is unavailable while in transit.
  Building cannot cover or isolate floor stockpiles; moving equipment cannot overwrite pile sites or
  disrupt active pile reservations. Piles and job phases are validated/persisted.
- Spatial storage verification: 143 tests passed; compileall passed; native SDL preview_storage.py
  completed actual hold-to-floor transfer in 49 game seconds and inspected reserved/carried/delivered
  frames; renderer smoke passed. The next active work is blueprint editing/freeform hull geometry.
- Blueprint drafting is now persisted in `game.blueprint`; it does not mutate built structure. F8
  enters a ship-local cursor editor with build/dismantle floor/wall/door modes, marks/unmarks,
  line/rectangle drawing, bounded four-way flood fill, vertical-axis drawing symmetry, selection corners,
  destination translation, copy, mirror, quarter-turn rotation, bounded undo, clear, and Enter-to-queue.
  Each drawing gesture is one undo transaction; fill is bounded by the current deck bounds to prevent
  empty-cell fill from spilling across the ship grid. Proposed geometry is overlaid separately from built tiles. Queueing
  validates the complete candidate geometry/access/material budget before creating physical
  carpentry jobs; completed changes alone affect ship collision. Saves retain unbuilt proposals.
- Physical construction now accepts connected deck floor builds and interior floor dismantling;
  floor jobs are save-validated as well as wall/door jobs. New floor plans must produce one connected
  deck region, have reachable adjacent work positions, preserve actors/bunks/stations/stockpiles, and
  pay one plank per built component. Multi-cell floor extensions are dependency-ordered from the
  existing deck; every tile is installed by a separate hauling/carpentry job. Unreachable hull-edge
  sites are rejected before queueing. Exterior hull floor removal/openings remain rejected until
  flooding simulation exists. This is an initial deck-zero slice, not the full freeform hull/deck system.
- Single-site floor demolition now checks the resulting cardinal floor graph before creating a model
  candidate; removing a narrow bridge cannot split the structural deck into detached islands, and the
  final deck cannot be removed. Regression constructs a three-tile deck with a one-tile articulation.
  This enforces connected-deck integrity. The separate damage API can now open an exterior wall breach;
  watertight compartments and pressure-sensitive flow remain incomplete.
- Blueprint queueing validates every planned demolition through the same component/access validator
  as a direct construction order before creating jobs. The batch path therefore cannot bypass the
  exterior-hull dismantling gate; damage-created breach repair is a physical build-wall job.
  Rejected blueprint proposals retain the unbuilt draft.
- Vertical-navigation and physical construction now have a live first slice. A persisted stair connector requires floors on
  both adjacent deck levels at the same ship-local cell. `World.set_active_deck()` projects a chosen
  level into the existing map and keeps per-deck door state; PageUp/PageDown changes the captain's
  elevation only on a built stair, refuses occupied landings, and saves/restores captain elevation.
  Sailors persist their own z coordinate and use the 3D floor/wall/door/stair route graph; a sailor
  upstairs must physically descend stairs for hold-attached supplies, but can haul planks
  through stairs to upper-deck physical floor/barrier work. Stairs themselves are buildable physical
  jobs once endpoint floors exist. The first upper floor can be placed directly above an accessible
  lower-deck floor before a stair exists; the crew works from that lower landing, then builds the
  stair and continues upstairs. F8 edits the selected deck; drawing, selection, undo and saved
  blueprint changes preserve z, and planned stair connectors follow the same unbuilt workflow.
  Deck-inactive crew remain hidden. Tests cover deck switching, occupied landing, crew cross-deck
  movement/construction/save-load, isolated door state, unreachable upper work and invalid elevation.
  Floor stockpiles can now be designated on any selected deck, retain z in identity/save data, and
  participate in real cross-deck source allocation, hauling, transfer, capacity and return routes.
   Upper stockpiles must remain reachable by stairs and cannot overlap stair landings. Ground items
   now persist under deck-aware keys; pickup, drop, map overlays and inspection use the selected
   elevation, while two-coordinate keys remain compatible for deck zero and port memories. Tests
   cover upper-deck drop/pickup/save/load plus legacy harbor restoration. Equipment can now be
   physically relocated through stairs, installed on a clear upper floor, projected as a functional
   fixture, used as an elevated station/bunk target, and restored from a mid-haul or installed save.
   Room reports and some ship-wide logistics remain lower-deck oriented, so full multi-deck work is
   still incomplete.
- Blueprint/editor verification: 286 tests passed; compileall
   passed; native SDL plan/queued maps and
  ship-ledger geometry panel inspected; application render, movement tween and modal smoke passed.
  Vendored renderer hashes remain unchanged; F8 dispatch is bound at the game adapter.
- Primitive regressions cover Bresenham line endpoints, rectangle perimeter, centerline-reflected
  symmetry, bounded enclosed fill, and one-step undo for each gesture. Updated native SDL editor help
  was captured and visually inspected; native overlay/physical-order walkthrough passed.
- Live upper-deck verification: `tools/preview_decks.py` captures the selected deck with stair,
  relocated functional fixture, upper stockpile and ground-item overlays; physical construction,
  storage transfer, item pickup/drop, fixture hauling and save/load have dedicated cross-deck
  regressions. The focused fixture/storage/decks set passed 63 tests; integrated suite passed at
  335 tests; compileall and native SDL renderer smoke passed.
- The suite also includes guards for floor articulation removal and blueprint-based exterior-wall
  dismantling; a failed hull-demolition draft remains intact for undo/editing.
- The built ShipModel now derives per-deck footprint diagnostics from actual floor components: area,
  bounding box, connected floor regions, enclosed cutouts, and cardinal contour-edge count. The ship
  ledger surfaces deck-zero contour/area alongside structural mass and center of mass, so irregular
  construction changes measurable vessel geometry instead of only altering ASCII collision tiles.
  Tests cover an 8-tile ring with a hole (16 contour edges), disconnected sections, another elevation,
  and a physically constructed L-shaped three-floor extension with save/resume and conserved plank use.
- Sparse component model supports independent floors/barriers/support slots and x/y/z data.
- Starter ship structure is captured into ship-local components and projected to live collision tiles.
- Version 3 saves persist structural components; versions 1/2 generate the starter structure on load.
- Projection validates before changing the map; installed fixtures cannot lose their floors or be
  covered by walls. Removed/new doors do not inherit stale open-door state.
- Live projection supports the selected deck inside the ship footprint; functional fixtures are
  elevation-scoped and supports occupy independent component slots.
- First playable construction: Enter > Construction orders designates an adjacent oak wall/door
  or interior barrier dismantling. Existing carpentry labor physically collects/carries planks and
  performs work; A manages priorities/cancellation; F4 advances work. Cyan glyphs show orders.
- Completion rechecks actor occupancy and essential access transactionally. Blocked construction
  keeps carried supplies. Cancellation physically returns cargo. Mid-haul saves resume correctly.
- This slice protects outer hull, installed stations/bunks and all essential routes. No salvage yet;
  build cost is one provisional plank bundle. Structural failure now stresses unsupported upper floors
  and overloaded beams/bearings, removes failed supports, and detaches clear unsupported floor cells;
  occupied/equipment/storage/item-bearing tiles are held at critical condition rather than invalidated.
- Equipment types/initial stock are still authored, but live equipment positions and crew station
  targets come from the registry and relocation jobs. A hydrostatic loading report and a provisional
  wind/sail/resistance/rudder model now affect the existing strategic chart voyage; direct tile-by-tile
  ship handling and wave physics are not implemented.
- Structural support increment: oak beams bridge up to four aligned tiles, with pine/iron span limits
  derived from material ratings; pillars raise vertical bearing capacity. The ship ledger reports
  supported/unsupported floor counts, beam spans and estimated overloads using actual fixture, store
  and actor loads. Build/dismantle jobs and blueprints accept beams/pillars; a beam over a clear gap is
  installed from an adjacent reachable deck tile. Component condition records fatigue, and a failed
  beam can leave upper flooring unsupported. This tunable model is intentionally approximate, not
  engineering-grade structural analysis.
- Support verification: focused support/building/blueprint tests passed; regression cases verify
  overload-driven beam failure and two-tile unsupported-section detachment. Latest full suite passed
   351 tests; compileall and native SDL renderer smoke passed. `tools/preview_support.py` rendered and
   visually verified a two-cell oak beam bridge to an upper-deck floor. Latest integrated suite passed
   351 tests.
- Hydrostatics increment: `ship_physics.calculate()` now totals built structure, installed fixtures,
  spatial supplies, trade cargo, crew/kit, captain inventory, deck ground items, prepared meals, loaded
  shot and bilge water with their physical positions. The footprint estimates waterplane area and
  provisional displacement; the ledger shows total/category mass, center of mass, displaced volume,
  draft, freeboard, reserve, trim, list and vertical stability margin. A save/load regression checks
  deterministic reports; loading cargo changes draft, freeboard and balance, while upper-deck stores
  raise center of mass and reduce estimated GM. This is a low-order estimate; ballast and dynamic
  wave physics remain future work.
- Propulsion increment: `sailing_forces()` estimates sail drive from deterministic chart wind,
  provisional sail area/rigging condition, point of sail and navigator rigging skill; hull dimensions
  and estimated draft set resistance, while total load affects acceleration. The underway voyage now
  accelerates toward force-balance speed, scales chart progress by speed/alignment, updates heading
  under a bounded rudder order, and records cross-track drift. `V > Set rudder angle` exposes steering;
  chart rows show speed/heading/force/acceleration/drift. Sailing state is save-validated; old saves
  without it initialize lazily. Short synthetic test voyages retain accelerated completion behavior.
  Sail area, drag, speeds and steering authority are deliberately provisional and need tuning.
 - Local flooding increment: `ShipHazards` persists hull breaches and local kilograms of water by
  ship-local deck cell. Damaging an exterior wall removes that same component, opens the real floor
  tile, and starts ingress; water transfers conservatively into adjacent floor cells through open
  space/open doors, while closed doors and walls block spread. The existing physical bilge work loop
  pumps up to 120 kg from reachable wet tiles and updates the compatible bilge meter; building a wall
  over the breach via a plank-hauling construction job seals ingress. Floodwater contributes location-
  aware mass and trim/load estimates; the ledger/sidebar and native map expose breaches/wet tiles.
  Legacy saves seed the old global bilge amount at the bilge station, and hazard state is optional in
  v3 saves. Current flow is a slow tile-neighbor approximation with fixed breach rates, not pressure,
  watertight compartment simulation, hull-floor damage, or realistic pump discharge.
   `tests/test_ship_hazards.py` covers breach restrictions, flooding/spread, pumping, physical patching,
   save migration, load accounting and ledger reporting.
- Coarse fire/smoke increment: `ShipHazards` now ignites actual non-iron structure, damages component
  condition as fuel is consumed, spreads on deterministic local pulses, diffuses smoke through open
  passages and stairs, and applies a small proximity stress/energy cost to sailors. Hazard saves
  migrate the old v1 breach/water shape; v2 persists fire/smoke. The ship ledger and selected-deck
   renderer identify active hazards. Water-based crew suppression now uses the physical storage-haul
   loop. Powder ignition, material-specific combustion and airflow/ventilation remain coarse or absent.
   `tests/test_ship_hazards.py` covers structural damage, smoke,
  persistence and v1 migration; focused hazard tests pass (8), and native renderer smoke passes.
 - Damage-control extension: active fires now generate repair-labor suppression jobs at the affected
  tile. A worker physically collects one water unit from spatial storage, routes to the fire, and uses
  it to cool/extinguish the flame; the supply reservation is consumed only on completion. The ledger
  exposes active fire jobs through the regular chore report. Focused hazard suite passes (9). The
  The integrated suite after this job extension passed 363 tests. This is a first operational loop,
  not an extinguisher/tool or coordinated firefighting model.
- Boarding increment: `World.locked_doors` and the ship-wide lockdown command persist across saves;
  closed locks block captain movement and crew routes, show an `L` marker, and can be released by
  the captain. During a pirate-vessel sighting the player can repel boarders: the raiders breach a
  real outer wall, floodwater enters, and their local route avoids locks where possible or spends
  time forcing/removing real locked-door components when no alternate path exists. A tactical fight
  then runs on a temporary projection of the current built deck; the exact prior map/deck/captain
   state is restored afterward. Victory resumes the chart voyage; defeat/withdrawal orders a retreat.
  `tests/test_ship_decks_live.py`, `tests/test_combat.py` and `tests/test_voyage.py` cover lockdown
  persistence, crew path blocking, alternate/forced routes, lower/upper-deck combat projection,
  pirate-sighting launch, hull/door damage and voyage resolution. The boarding implementation is
   still a first slice: no persistent wounds/captures, crew tactical allies, or ship-wide boarding AI.
- Room-workflow increment: starter vessels now include movable, persisted medicine and brig fixtures;
  older fixture snapshots migrate by adding only these known canonical additions, while arbitrary missing
  fixture identities remain invalid. Health-injured crew seek care only when the chest is installed,
  reachable and located in a designated infirmary, then physically walk there and recover gradually.
  Reports also distinguish a live magazine (real cannon stations/ammo; existing shot-haul loading jobs)
  and berths (installed/assigned bunks; sleep already routes to each sailor's actual bunk). Boarders
  defeated in tactical combat can be interned only when the brig is designated, its cage is installed,
  and every boundary door is locked; captures persist and can be released through the room menu. Capacity
  is three. Reports expose missing/insecure prerequisites. Captive feeding, transfer/negotiation, and
  medicine consumption remain outstanding. `tests/test_ship_fixtures.py` and `tests/test_combat.py`
  cover health care, equipment and save migration, magazine/bunk state, locked brig, capture/release,
   and victory-to-custody. The post-brig integrated suite passed 376 tests; focused room, fixture and
   combat tests pass after it.
- Rooms now derive from structural geometry, with doors preserving room identity even while open.
  Enter > Room designation / report assigns use to the current connected space and inspects area,
  walkable tiles, actual bunks/stations, boundary doors and shortest hold-to-station walking distances.
- Designations persist per tile through splits, merges and temporary walls; merged spaces can retain
  multiple intended uses. Version 3 saves optionally include validated room_zones for backward compatibility.
 - Logistics inspector increment: `ship_logistics.inspect()` estimates current
  claimed-job routes, closed-door and load-weighted travel time, route overlap/body conflicts, jobs
  completed in the last 10 minutes, and nearest repair-labor response to fires/breaches. Enter >
  Logistics / traffic inspector displays the report and can overlay shared routes with `;` on the
  active deck. These are snapshot estimates; no path is reserved and future needs/work/congestion
  are not simulated. Tests and native preview are in `tests/test_ship_logistics.py` and
   `tools/preview_logistics.py`. Focused inspection/room/fixture/combat tests passed 79; native traffic
   overlay and report screenshots inspected; the integrated suite passed 378 after this increment.
- Designations do not create equipment or passive bonuses. Room workflows require installed physical
  equipment; brig capture is limited to defeated boarders and a locked designated room. Roof checks and
  watertight compartments remain pending.
  Supply distances ignore bodies and door time.
 - Earlier room baseline: 72 tests passed, including construction hauling/completion/cancellation, mid-haul resume,
  occupancy blocking, no-entombment, menu controls and existing migration/collision checks.
  Native ASCII render/movement/modal smoke passes with a queued cyan door marker; screenshot inspected.
 - Earlier room slice verification: 103 total tests passed; native room-report modal/render smoke passed
  and pirates-rooms.png screenshot inspected. Tests include geometry splits/merges, designation retention,
  equipment checks, door-state independence, save validation and paused room-menu controls.

## Regression vessels and invariants
Test plain sloop; longer supply routes; heavier armor; top-heavy tower; subdivided/open hold;
connected twin hulls; single stair bottleneck; defensive maze; broken support; mid-job save/load.
Enforce no duplicated materials, lost carried cargo, unexplained mass changes or internal water creation.
Measure travel/work separately; test conservative mass, save validation and deterministic behavior.

## Current integration audit (November 2026)
- Integrated `python -m pytest -q`: 378 passed after the room and logistics increments.
- Focused room/fixture/combat/voyage coverage exercises equipment-backed infirmary recovery, magazine
  state, assigned bunks, locked brig custody, boarding outcome/geometry restoration, and save migration.
- `tests/test_ship_logistics.py` verifies shared-route metrics, recent completion throughput, fire ETAs
  and missing repair labor. `tools/preview_logistics.py` captured the current-deck traffic overlay and
  complete diagnostics modal; both native screenshots were inspected.
- Remaining inspector approximations: no future-need forecast, route reservations, dynamic rerouting,
  actor congestion simulation, full task-duration throughput, or route/hold distances on all room UIs.
  Captive upkeep/negotiation, medicine consumption, naval-grade physics and sealed compartments remain
  outside the current implemented slice. No user save was modified during verification.
