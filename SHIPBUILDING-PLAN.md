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
  This enforces connected-deck integrity; watertightness, localized flood response, and exterior hull
   openings are still not implemented.
- Blueprint queueing validates every planned demolition through the same component/access validator
  as a direct construction order before creating jobs. The batch path therefore cannot bypass the
  exterior-hull opening gate; rejected queue attempts retain the unbuilt draft.
- Vertical-navigation work now has its first structural primitive: a persisted `stair` connector is
  valid only when lower and upper floor components exist at the same ship-local coordinate. The new
  three-dimensional route graph follows cardinal floor adjacency, blocks walls, reports doors that
  need opening, and changes elevation only across built stair connectors. `route_to_worksite()` finds a
  reachable same-deck work tile for a 3D target, including routes that need stairs. Tests cover
  disconnected decks, bidirectional stair traversal, wall blockage, door reporting, unreachable
  cross-deck work, invalid connectors and model save/restore. This is a route-graph foundation only:
  actors/jobs, hauling, map projection and the F8
  editor are still deck-zero and do not yet expose live multi-deck construction.
- Blueprint/editor verification: 240 tests passed; compileall passed; native SDL plan/queued maps and
  ship-ledger geometry panel inspected; application render, movement tween and modal smoke passed.
  Vendored renderer hashes remain unchanged; F8 dispatch is bound at the game adapter.
- Primitive regressions cover Bresenham line endpoints, rectangle perimeter, centerline-reflected
  symmetry, bounded enclosed fill, and one-step undo for each gesture. Updated native SDL editor help
  was captured and visually inspected; native overlay/physical-order walkthrough passed.
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
- Live projection currently supports only deck zero inside the starter footprint. Higher decks,
  supports, movable fixtures and floor/hull reshaping remain pending.
- First playable construction: Enter > Construction orders designates an adjacent oak wall/door
  or interior barrier dismantling. Existing carpentry labor physically collects/carries planks and
  performs work; A manages priorities/cancellation; F4 advances work. Cyan glyphs show orders.
- Completion rechecks actor occupancy and essential access transactionally. Blocked construction
  keeps carried supplies. Cancellation physically returns cargo. Mid-haul saves resume correctly.
- This slice protects outer hull, installed stations/bunks and all essential routes. No salvage yet;
  build cost is one provisional plank bundle. Full support/evacuation modeling remains future work.
- Equipment types/initial stock are still authored, but live equipment positions and crew station
  targets come from the registry and relocation jobs. Sailing/buoyancy are not implemented.
- Rooms now derive from structural geometry, with doors preserving room identity even while open.
  Enter > Room designation / report assigns use to the current connected space and inspects area,
  walkable tiles, actual bunks/stations, boundary doors and shortest hold-to-station walking distances.
- Designations persist per tile through splits, merges and temporary walls; merged spaces can retain
  multiple intended uses. Version 3 saves optionally include validated room_zones for backward compatibility.
- Designations do not alter crew logic or create equipment. Dedicated infirmary/magazine/brig workflows,
  roof checks and watertight compartments remain pending. Supply distances ignore bodies and door time.
- Verified: 72 tests pass, including construction hauling/completion/cancellation, mid-haul resume,
  occupancy blocking, no-entombment, menu controls and existing migration/collision checks.
  Native ASCII render/movement/modal smoke passes with a queued cyan door marker; screenshot inspected.
- Latest room slice verification: 103 total tests passed; native room-report modal/render smoke passed
  and pirates-rooms.png screenshot inspected. Tests include geometry splits/merges, designation retention,
  equipment checks, door-state independence, save validation and paused room-menu controls.

## Regression vessels and invariants
Test plain sloop; longer supply routes; heavier armor; top-heavy tower; subdivided/open hold;
connected twin hulls; single stair bottleneck; defensive maze; broken support; mid-job save/load.
Enforce no duplicated materials, lost carried cargo, unexplained mass changes or internal water creation.
Measure travel/work separately; test conservative mass, save validation and deterministic behavior.
