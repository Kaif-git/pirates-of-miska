# NPC Society and Sea News — Design Plan

## Purpose

Make Miska's sea feel inhabited by persistent people and organizations with
histories, ambitions, alliances, and grudges. The player should encounter
recognizable captains and hear news that reflects events the simulation actually
produced—not a set of anonymous ships following fixed loops.

This is the first NPC-worldbuilding slice. Trade contracts are deferred until the
society/news loop is working.

## Scope and boundaries

Included:

- A small authored cast of pirate organizations, independent crews, named leaders
  and vessel captains, and Marine figures.
- Crew/fleet command relationships, individual and organizational ambitions,
  reputation, alliances, rivalries, and hostility.
- Bounded offscreen movement, meetings, diplomacy, and abstract NPC-vs-NPC clashes.
- A persistent news history derived from actual state changes and visible to the
  player at ports and at sea.
- Save/load migration, validation, and tests for the simulated society.

Not included in this slice:

- Player tactical ship combat, boarding, or a competing encounter resolver.
- Shipbuilding or multi-deck construction changes.
- Player cargo-delivery contracts, bounties, or a new trading loop. Bounties are a
  possible later consumer of the news/history system, not a first-slice feature.

An NPC clash can be resolved strategically offscreen (for example, a ship retreats,
a captain is captured, or a vessel is lost). It should produce a structured outcome
that a separate player encounter system could later consume; it does not implement
tactical combat for the player.

## Society model

Represent people and organizations as stable, persistent identities—not labels
recreated from a ship on every tick.

### Layers

1. **Person** — named NPC, role, reputation, personal ambition, current status,
   affiliation, relationships, and optionally a current vessel.
2. **Crew** — a ship's working group, headcount, morale/readiness, loyalty, and
   members. Important captains are people in the crew, not just ship fields.
3. **Vessel** — identity, chart position/destination, crew, supplies/cargo, and
   current orders or activity.
4. **Organization / faction** — command hierarchy, members and vessels, broad
   ambition, resources/holdings, allies and rivals. A major pirate organization has
   a famous leader plus named captains commanding its other vessels.
5. **Marine command** — patrol vessels and named officers linked to an organization,
   with orders such as patrol, pursue, escort, or return to port.

The first authored cast should be deliberately small: one prominent pirate
organization, its famous leader and several named vessel captains, two independent
pirate crews, and a Marine presence. Add characters only when they have a role in
the simulation or a story. Names and lore remain original to this game; initial
roster details can be tuned as playtesting reveals which characters are memorable.

### Ambitions and relationships

Give important people and organizations a current ambition with a target and a
reason. Initial ambition types may include gaining notoriety, acquiring wealth,
recruiting, avenging a loss, protecting a route, escaping a Marine pursuit, expanding
influence, or weakening a rival. Ambitions should select actions from the current
world state; they are not free-form text that the simulator cannot execute.

Store relationships by stable IDs and faction/person pair. Relationship dimensions
should distinguish at least trust/affinity from hostility/rivalry; optional fear,
debt, or respect can be added only if they drive behavior. Relationships change
because of recorded causes (aid, betrayal, raid, rescue, defeat, shared enemy,
negotiation), so a news item can explain why a bond changed.

## Offscreen simulation

- Use the existing strategic sea chart and a coarse hourly/daily simulation cadence;
  do not add continuous ship physics.
- Advance each entity at most once per due strategic tick, in stable ID order, so
  results are deterministic and independent of render/UI frame rate.
- Select actions from ambition, orders, relationships, resources, and chart position.
- When rival groups meet, decide whether they parley, trade/help, evade, ally, or
  clash based on their state. Abstract clashes update vessel/person/crew status,
  reputation, resources, and relationships, then create a structured result.
- Keep people, crews, vessels, and market resources conserved. Recruitment and loss
  must be explicit transfers/status changes with a causal event; never silently
  spawn or delete a named person or ship.
- Keep the simulation bounded: a small fixed roster, capped histories, and no
  unbounded per-second narrative generation.

## News and player awareness

Every news entry must refer to a real event/state transition. Store structured data
such as a stable event ID, simulation tick, event kind, participants, location,
cause, outcome, and readable headline/body. The prose is a view of the event record,
not a separate random headline generator.

Examples of useful reports include a named captain breaking an alliance, a Marine
officer beginning a pursuit, a pirate captain losing a vessel, or two independent
crews joining a larger fleet. The report should identify the people/factions and
make the cause or consequence legible. Routine movement should not flood the news.

Surface recent headlines and nearby identities in the existing sea-chart/voyage
views, and provide a way to inspect fuller recent reports in port or at sea. Sighted
ships should expose their names, affiliations, and current activity. News should
remain understandable when the player missed the event itself.

## Save/load and migration

Preserve existing saves and the current NPC ship/economy behavior while introducing
the richer cast. The current implementation has three persistent NPC ships, a
finite market/headcount ledger, an hourly fleet pulse, and a capped string event
history in `NpcFleetSystem` (`pirates/voyage.py`). `World.save/load` stores that
state under `npcs` in the existing version-3 save (`pirates/game.py`).

Design the migration to accept old `npcs` snapshots and fill in the authored
society defaults without duplicating ships, crew, or historical events. Validate
IDs, command links, affiliations, relationship endpoints, ambition targets,
simulation time, event records, and conservation invariants before replacing the
live World. Keep old harbor passage and voyage state separate from society state.

## Implementation phases

1. **Audit and design** — preserve concurrent workspace edits; document existing
   fleet/save/time hooks and finalize stable IDs and state ownership.
2. **Cast and hierarchy** — add the authored people, organizations, vessels, crew
   links, Marine roles, ambitions, and relationship data.
3. **Strategic behavior** — add deterministic bounded decisions, meetings,
   relationship changes, and abstract NPC-vs-NPC outcomes.
4. **News** — emit structured causal events from every consequential transition,
   persist a bounded history, and render headlines/details in existing interfaces.
5. **Integration and migration** — connect society state to world time, sea-chart
   sightings, and save/load while preserving old saves and the existing economy.
6. **Verification** — test behavior, determinism, conservation, migration, malformed
   state rejection, and news accuracy; run the full suite and a save-free native
   walkthrough with screenshots.

## Acceptance checks

- The famous leader, named subordinate captains, independent crews, and Marine
  figures retain identity and command/affiliation links over time and across saves.
- Ambitions produce distinguishable actions and relationships change for explicit,
  logged reasons.
- NPC encounters/clashes alter real state and generate matching news; headlines
  never claim an event that did not happen.
- Simulation outcomes are repeatable for the same initial state/time advancement,
  and people/vessel/resource conservation checks hold.
- The player can recognize nearby named ships and review recent world news at sea
  and in port.
- Existing save migration, voyage, economy, and ship tests continue to pass.
- No player tactical combat, ship-construction implementation, or trade-contract
  feature is added in this slice.
