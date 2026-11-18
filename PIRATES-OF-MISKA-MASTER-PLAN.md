# Pirates of Miska — Master Design Plan

Date: 2026-11-18
Status: agreed direction and design backlog, not implementation authorization.

This document consolidates the captain/crew/world/voyage discussion. Later plans will break it into implementable, verified tasks. It does not replace existing subsystem plans or declare their unfinished work complete.

## 1. The game we are making

A captain's rise in a persistent island world: recruit people, pursue ambitions, prepare a ship, undertake memorable voyages, and return with consequences that create the next opportunity.

Start with a modest ship, a handful of starter crewmates and limited wealth. Nothing particularly prestigious. Starting as a captain does not establish starting magical powers; that remains open.

The player chooses their ambitions: wealth, reputation, exploration, personal strength, a formidable crew, alliances, revenge or eventually command of a fleet. The largest fleet is not the only valid form of success.

Core loop:

**Choose an ambition → gather information and people → negotiate commitments → prepare and choose a route → simulate ordinary passage → play consequential situations → return with gains, losses and new leads → choose the next ambition.**

The desired “one more voyage” feeling comes from anticipation, competing opportunities, persistent people and meaningful outcomes—not compulsory chores or an encounter every few minutes.

## 2. Design boundaries

- Extend existing authorities for crew, relationships, work, inventory, money, skills, ships, voyages and saves. Do not build parallel versions of them.
- Ordinary ship life need not entertain the player every second. The ship is a moving home and expedition platform, not necessarily a floating production colony.
- Skipping uneventful time must not skip simulation or consequences.
- No daily clicking requirement to collect progression rewards. Learning and growth belong quietly in personal logs, not mandatory popups.
- Prefer bounded, explainable behavior over unrestricted emergent-AI frameworks. Do not make the project depend on a universal NPC planner.
- Every new mechanic must connect to a player decision or an existing capability. Do not add activities merely to increase the feature count.
- Separate confirmed direction, recommendations and unresolved rules. Balance numbers require measurement.
- Preserve deterministic advancement, old saves, finite resources and concurrent work. Reports remain read-only; actions have explicit costs.

## 3. Captain, ambition and defeat

The captain has wealth, reputation, relationships, capabilities and self-chosen goals. A voyage can advance these in different combinations; profit and positive reputation are not guaranteed after every trip.

First-version defeat is usually lethal. Do not assume imprisonment, ransom, resurrection, escape or a second-chance campaign loop. These may be reconsidered later.

Consequently, major threats need readable warning, useful information and meaningful opportunities to avoid or withdraw before commitment. Lethality must not justify arbitrary, unavoidable campaign-ending rolls.

Open: precise captain creation, starting abilities, death/save policy and whether any nonlethal defeat cases belong in the first version.

## 4. Persistent world and information

The world contains islands, trade routes, marine authority, pirates, land rulers, local factions, bands and independent communities. Islands have people, history, creatures and interests beyond serving as shops or combat arenas.

Faction growth and decline, NPC wealth, power, trade, status, legends and feuds are already implemented or being implemented in separate work, according to the project discussion. Audit that work before integration; this plan does not authorize replacing it or treating every proposed behavior as complete.

Information is a practical advantage:

- Charts and route knowledge.
- Sightings, identities and reported movements.
- Harbor access, repair and resupply opportunities.
- Introductions and local relationships.
- Rumors about treasures, powers, discoveries and conflicts.
- Relevant news received through a source, with age and uncertainty.

Knowing a coastline is not the same as knowing how to reach it safely or who will shelter you there. Reports must not grant omniscient knowledge of hidden world state.

## 5. Reputation, law and political relationships

Wealth and reputation attract recruits, patrons, rivals and threats. Good and bad reputations attract different people and opportunities.

Recommended distinction, not a settled meter implementation:

- Fame: how widely known the captain is.
- Reputation: what conduct and capabilities people associate with them.
- Relationships: what particular people and factions think of them.

Marines protect legal trade, enforce authority and place bounties on dangerous people and pirates. The captain can cooperate with or oppose them. They are the strongest institutional opponent: a fleet that challenges their major forces prematurely should expect devastating defeat.

Local victories need not mean defeating the institution. Patrols, officers, jurisdictions and political interests can create different situations; corruption, privateering and regional negotiation are possibilities to scope, not assumed implemented systems.

Pirates and land factions offer alliances, rivalries, favors, access, support and enemies. Commitments and prior conduct should matter beyond one global approval number.

## 6. Crew recruitment and agreements

Recruitment connects NPCs, the world and the captain's ambitions. Not everyone joins for the same reason:

- Salary and reliable employment.
- A share of wealth or captured treasure.
- Better equipment, training or access to power.
- A destination, rescue, revenge or confrontation.
- Loyalty, belonging, shared interests or a cause.

Recruiting someone can create an obligation and a new voyage objective. A promise must have understandable terms before it becomes enforceable; no hidden deadline or retroactive interpretation.

Use the Football Manager distinction: an inexperienced recruit may want simple wages, while an exceptional recruit may negotiate several terms. Do not burden every sailor with a complex contract.

Candidate agreement terms to design:

- Role and officer status.
- Wage, payment schedule or profit share.
- Joining payment.
- Equipment or instruction commitments.
- A specific personal undertaking.
- Review/renewal and departure conditions.

These are clause candidates, not a finalized contract schema. Define accounting, ownership, eligibility, failure and renegotiation before implementation. In particular, define what profit means before promising shares.

People who become more accomplished may seek better terms. Recommended review points are renewal, promotion or substantial changes in standing—not constant automatic renegotiation.

Ordinary sailors remain valuable. Dependable cooks, workers and navigators must not become disposable merely because they lack exceptional combat or magic.

### Personal money and bounded goals — proposed first approach

Crew can retain savings. Not every coin needs a simulated purchase.

Where useful, give a person a small number of explicit financial goals: family remittances, equipment, instruction, security or retirement. Supported port transactions may satisfy these goals.

Do not initially give crew unrestricted shopping, trading, adventuring or expedition autonomy. No silent purchases from ship funds, unexplained use of stores or invented equipment. Exact personal-finance rules remain to be designed.

## 7. Control and companion contributions

Confirmed intent: directly control the captain; do not require manual command of every person.

Recommended control model to prototype:

- Choose expedition members, equipment, roles and general intent beforehand.
- Companions perform routine movement, combat and immediate self-preservation.
- Offer a small vocabulary: follow, hold, protect, focus a target and fall back/return.
- Orders have explicit boundaries; guarding a landing must not turn into an uncontrolled pursuit across an island.

Test this in a small encounter against the actual combat architecture before committing to a final control scheme. Do not silently merge isolated duel and campaign combat systems.

Crew should contribute beyond damage:

| Situation | Potential contribution |
| --- | --- |
| Unfamiliar coast | Hazard recognition, local knowledge, a known landing |
| Other ships | Recognition, introductions, negotiation, relevant history |
| Wreck or damaged vessel | Structural assessment, salvage expertise, repairs |
| Storm | Execute rigging, cargo, damage-control and care priorities |
| Unusual find | Partial identification, historical context or a lead |
| Faction meeting | Contacts, credibility, personal complications |
| Later land expedition | Expertise, equipment handling and securing retreat |

Contributions depend on actual knowledge, capability and availability. They may improve information or offer alternatives; avoid making every situation require a specific key-person.

Keep world simulation, crew scheduling and encounter behavior separate, with explicit interfaces. Bounded goals and authored rules are preferable to a single general-purpose autonomy framework.

## 8. Voyage rhythm and advancement

Ordinary passage is primarily auto-simulated, like advancing days between matches in Football Manager.

Candidate controls:

- Advance to tomorrow.
- Advance to the next meaningful event.
- Advance to arrival.

All modes run the same underlying time, needs, work, supplies, relationships and world hooks. Define event ordering, interruption, arrival and save/resume semantics before implementation.

Daily reports may summarize progress, ship state, notable crew developments and relevant world news. Detailed learning stays in personal logs. Routine reports should not repeatedly interrupt unless requested.

Stop or slow advancement for actionable situations: entering dangerous territory, an ambush, a significant sighting, another ship, a creature, a discovery or threatening weather. Not every distant sail deserves a forced modal.

Optional time aboard remains possible, but no exclusive reward should require watching uneventful simulation.

## 9. What makes voyages memorable

### Purpose and competing opportunities

Deliveries, trade, bounties, personal promises, exploration, recruitment leads and pursuit can overlap. An interesting detour competes with the original objective through time, risk, supplies or relationships.

### Route decisions

Navigation and a useful world map still need development/audit. Routes should express understandable tradeoffs, not merely distance:

- Busy lanes: commerce, witnesses, assistance and patrols.
- Coastal passages: shelter and stopping places, with local hazards and authorities.
- Open sea: isolation, exposure and uncertain opportunities.
- Shortcuts: time saved against a known kind of risk.
- Rumor-driven detours: uncertain reward against a real commitment.

### Encounter families — design backlog

| Family | Main decision space |
| --- | --- |
| Ships and sightings | Investigate, identify, hail, assist, trade, shadow, evade, pursue or fight |
| Dangerous territory | Detour, seek help, change approach or knowingly accept exposure |
| Ambush or pursuit | Recognize clues, escape, negotiate or commit to combat |
| Storms | Avoid or press on; prioritize people, ship, cargo and progress |
| Discoveries | Investigate now, commit more resources, record for later or share information |
| Sea monsters | Learn behavior, evade, deter, exploit circumstances or fight where feasible |
| Crew-linked situations | Respond to a relevant commitment, history, contact or opportunity |

Every situation needs available information, meaningful choices, real consequences and a way to conclude. No cosmetic options with identical outcomes. Authored situations with state-based variation are a sensible first approach.

Not every distress signal is a trap. Not every encounter is hostile. Quiet passages, genuine assistance and uncomplicated opportunities are necessary for uncertainty to remain interesting.

### Persistence and arrival

Outcomes should generate future possibilities: rescued traders become contacts, escaped opponents become rivals, useful anchorages remain on the chart and unresolved discoveries remain leads.

Arrival answers: what happened, what changed, and what could we do next? Include profit/loss, obligations, condition, readiness, relevant relationships and new opportunities without guaranteeing rewards.

## 10. Combat and treatment of defeated enemies

Combat is a major part of the game and is being developed separately. Integrate through verified campaign interfaces rather than assuming every combat subsystem is interchangeable.

The player is usually killed in defeat in the first version. Victorious players may eventually have broader choices over defeated NPCs: kill, capture, release, ransom/exchange, claim a bounty, recruit, humiliate or enslave/use forced labor.

These are requested design possibilities, not completed features or a promise to ship all outcomes initially. Captivity and prisoner handling need explicit scope, custody, escape, accounting and NPC-response rules.

Coercion must not instantly create genuine loyalty. Cruel treatment should not be a consequence-free labor upgrade; witnesses, crew and affected factions may respond under defined systems.

## 11. Magic — agreed game direction

### Source and canon boundary

Inspiration: `F:/OneDrive/Kaif's Novellas/Tales of Miska (Disbanded)/World Building/Miska World Buildimg.md`.

That document is not binding game canon. Its named characters, Wall, Sharya origin, history, political explanations and personality restrictions are not automatically adopted. No fixed protagonist or novel plot is required.

### Fusion: a secret, learned art

- Learned from hidden societies that still preserve and practice it.
- Requires substantial effort and instruction/access, not a casually purchased universal upgrade.
- Makes equipment and infrastructure better and stronger: weapons, armor, ships and buildings.
- The inspiration's material bonds and specialist applications are candidates to redesign, not finalized mechanics.

Teachers, specialist knowledge and materials create reasons to travel and build relationships. Exact access requirements, costs, limits, progression and any personal combat applications remain open.

### Floren: rare power that transforms a person

- Publicly known; accomplished users are legendary figures.
- For now, approximately 20–30 distinct Florens, informed by the source's branches. The final roster is not selected.
- Devil-Fruit-like acquisition: rare physical fruits found among treasures, exchanged, stolen or sold for fortunes.
- Most fruits are unidentified before consumption. Do not assume a reliable universal appraisal method.
- One Floren per person.
- One living user of each distinct Floren worldwide. No duplicate users for now; future expansion may revisit this.
- Power has depth through prolonged use and mastery. A modest combustion ability might eventually permit catastrophic applications; this is not a finalized ability tree.
- After the user's death, the power returns to circulation.

The old source's purpose-based awakening is not the game's acquisition rule. Personality can shape applications without determining whether someone deserves power.

Open rules: fruit activation details, identification evidence, unused fruit uniqueness, storage/transfer, recirculation location and delay, mastery costs/counterplay, starting captain powers, and whether/how Fusion and Floren coexist in one person. Do not automatically import swimming weaknesses, killer inheritance or other unagreed Devil Fruit rules.

Recommended safeguard: death does not automatically award the fruit to the killer. Decide this explicitly before building power hunting.

### World and crew consequences

A power can change what an expedition can attempt, not only its damage. Giving an unknown fruit to yourself, a trusted crewmate or selling it should be consequential. Powerful recruits can bring obligations and political attention.

Preserve ordinary expertise, equipment and preparation. Magic should not automatically erase navigation, logistics, medicine or the value of a functioning crew.

## 12. Shipbuilding, fleet growth and delegation

Shipbuilding and upgrades support the captain's goals. Extend existing ship systems; do not invent an unrelated upgrade inventory.

Recruitment and capture are not free expansion. Relevant constraints include wages/shares, provisions, capacity, berths, maintenance, capable officers and political exposure. People must be worth employing, not merely willing to join.

Later, trusted officers can command additional ships and people. Delegation should operate through bounded missions and policies, not require manually controlling every sailor on every vessel.

Suggested mission shape: objective, assigned assets, operating area, risk policy, return/abort conditions and a report. Exact authority, remote communication, loyalty and failure rules are future design work.

Fleet scale is a later ambition, not a prerequisite for an enjoyable single-ship game.

## 13. Scope tiers and recommended sequencing

This is dependency guidance, not an active task list or completion claim.

1. **Audit existing work:** establish what crew/NPC simulation, combat, shipbuilding, economy, voyage progression and saves actually support.
2. **One compelling voyage loop:** an objective, meaningful route alternatives, compressed passage, a small set of actionable encounters and persistent arrival consequences.
3. **Recruitment and commitments:** simple agreements first, then a few exceptional clauses and bounded personal goals.
4. **Connected world opportunities:** information, faction relationships, recurring NPCs, discoveries and consequences that produce another voyage.
5. **Magic vertical slice:** establish rules, then a small verified Fusion/Floren selection before attempting the full roster.
6. **Broader expeditions and command:** land excavation/adventure, larger crews, fleets and delegation once the single-ship loop works.

Land exploration, excavation and adventure are intended later. Fishing, additional daily activities, unrestricted production chains and general NPC autonomy are not prerequisites or automatically approved expansions.

## 14. Questions to settle in smaller plans

- Captain creation, starting magic, death and save policy.
- Actual companion control model and campaign-combat integration.
- Contract accounting, profit shares, promise deadlines and renegotiation.
- Personal money ownership, spending and departure rules.
- Map scale, travel duration, knowledge and news propagation.
- Encounter triggers, avoidability, interruption and persistent identity.
- Bounty jurisdiction, evidence, marine escalation and faction response.
- Floren roster, uniqueness across fruit/user states and recirculation.
- Fusion curriculum, material rules, teaching access and progression.
- Prisoner outcomes and their first-release scope.
- Delegation boundaries and communication limits.

## 15. Gates for every future implementation plan

- Identify the player decision and connection to the core loop.
- Audit existing authorities and declare what is reused.
- Specify state, finite costs, eligibility, failure and player-visible explanations.
- Define bounded NPC behavior without a universal autonomy dependency.
- Verify equivalent manual/accelerated time, interruption and save/resume.
- Measure balance and frequency before making broad claims.
- Test unhappy paths, negative cases, conservation and compatibility.
- Inspect native UI where applicable and report full-suite status honestly.
- Preserve unrelated work; maintain global agent memory outside the repository.

Success is not the number of implemented systems. It is arriving from a voyage with consequences you understand, people you care about, and a reason to set sail again.
