# Shipboard Supplies and Daily Life — Expansion Roadmap

Date: 2026-11-17
Status: design backlog, not implementation authorization or a claim of completed features.

## 1. Purpose and relationship to existing work

Build on named provisions, measured clean water, recipes and separate apparel slots to make provisioning and everyday equipment meaningful. Prefer a small number of connected, observable mechanics over a large decorative catalog.

`PROVISIONS-AND-APPAREL-PLAN.md` describes the earlier unit/catalog migration. This roadmap records the follow-up suggestions, including systems that were explicitly outside that earlier scope. It does not silently mark those systems implemented.

### Recorded starting point

- Named uncooked/ready foods use integer grams; clean water uses integer milliliters. Reports show kg/L.
- Recipes identify named ingredients and measured water, but cooked output is pooled in `prepared_food_g`: recipe identity and leftovers are not individually preserved.
- Eating consumes 250 g and drinking consumes 1 L per action. These are prototype balance rules, not a nutrition model.
- Voyage estimates use total food mass and a nominal water/food budget; ingredients, cooking access and recipe water can make those estimates optimistic.
- Canvas trousers and wool cap have existing numeric modifiers; weather exposure, garment wear and warmth are not thereby simulated.
- Existing physical storage, source reservations, crew hauling, cancellation and save migration must remain authoritative.

## 2. Design rules

1. Every added item needs a clear player decision or supported use. Label trade-only goods honestly.
2. Keep integer canonical quantities: food g, liquid mL, discrete items as counts. Display appropriate g/kg/mL/L without hiding small remaining quantities.
3. One authoritative inventory; summaries must not duplicate stock. Reservations and carried goods are unavailable to other consumers.
4. Transformations conserve material mass or explicitly record waste, evaporation or another sink. Packaging has separate tare mass once reusable containers exist.
5. No instant off-screen hauling, free refills, duplicated leftovers, infinite fuel or repair material.
6. All new state must survive save/reload and deterministic time chunking. Browsing reports must not advance simulation time.
7. Explain blocked actions, effects, costs, assumptions and shortages in player-facing reports.
8. Keep shipboard systems separate from tactical-arena and headless `sim/` systems unless a later task explicitly defines integration.
9. Proposed numbers below are tuning candidates, not verified physiology or final balance.

## 3. Named cooked foods and leftovers — highest priority

### Behavior

- Preserve grain porridge, bean stew and fish chowder as distinct cooked foods after completion.
- Track exact remaining grams; a 250 g meal can use a partial remainder plus another eligible food without rounding away mass.
- Show cooked-food name, available mass, reserved mass, source batch and, once supported, freshness.
- Use cooked foods first according to policy, then suitable ready foods. Dry grain/beans must not be eaten as if already cooked.
- A completed recipe creates output exactly once; pauses/reloads cannot recreate it.

### Data approach

- Start with a validated cooked-food mapping or batch list rather than more hardcoded item branches.
- Introduce stable batch IDs when age/quality requires them; persist recipe ID, output food ID, grams, creation time and location.
- Keep batch-aware reservations possible. Do not merge differently aged batches merely to simplify the UI.
- Migrate unidentified existing prepared food as explicitly named legacy cooked provisions. Do not invent a historical recipe or reset freshness on every load.

### Acceptance

- Cooking all existing recipes retains their names and reconciles ingredient/output mass.
- Partial servings, mixed leftovers, cancellation and mid-cook saves neither lose nor duplicate food.
- Stores, crew consumption logs and ship-load calculations agree.

## 4. Drinking and actual consumption

- Replace the fixed 1 L gulp with a configurable 250–500 mL target; start testing at 250 mL.
- Scale thirst relief to consumed volume. Do not retain the old 1 L thirst relief for a quarter-liter drink.
- Permit useful smaller remainder drinks; never strand 100 mL because a full serving is unavailable.
- Track bounded daily consumed mL per sailor and ship totals, separately from cooking, cleaning and firefighting.
- Report nominal planning budget alongside measured recent use. Account for incomplete days and avoid extrapolating from a few seconds of play.
- Later exposure/workload modifiers may increase demand, but only when those mechanics exist and are explained.

**Gate:** exact measured withdrawal and proportional relief, no negative water, shortage handling, daily rollover and deterministic save/resume tests.

## 5. Provision planning and honest voyage warnings

### Required report sections

- Ready food: cooked dishes and foods edible without cooking, in kg and estimated crew-days.
- Cook-only ingredients: quantities, feasible recipes and currently missing ingredients.
- Clean water: available L, reserved L, minimum drinking reserve and projected recipe demand.
- Access: whether water, mess, galley and ingredients are installed/reachable; labor or fuel blockers where applicable.
- Crew count, route duration, safety margin and explicit per-person planning assumptions.
- Recent consumption trend, clearly distinguished from the nominal estimate.

### Estimation algorithm

1. Snapshot available supplies without mutating/reserving them.
2. Calculate immediate ready-food coverage.
3. Simulate policy-selected batches on a private copy of ingredient stocks, respecting shared ingredients and cooking water.
4. Reserve drinking needs before optional cooking; never count one liter twice.
5. Distinguish potential food coverage from food actually usable with current labor, fixtures, time and fuel.
6. Report the limiting resource and uncertainty rather than one deceptively precise number.

Example warning: “Enough ready food for about 1 day. Ingredients could extend this to 4 days, but cooking would leave drinking water below the configured reserve.” Example values are illustrative, not a current game report.

**Gate:** plentiful dry ingredients cannot hide no cook/no water/no galley; increasing crew reduces coverage; shared ingredients and reserved bundles are counted once; report remains read-only.

## 6. Galley orders and emergency reserves

- Provide automatic, preferred-recipe, make-once, maintain-target and paused modes.
- Show each recipe's exact inputs, output, work requirement and blocking reason.
- Configure minimum ready-food target, drinking-water reserve and protected emergency biscuit stock.
- Allow recipe ordering and an explicit substitutions policy; substitutions must correspond to declared recipes, not free mass conversion.
- Respect unavailable ingredients, disabled cooking labor, inaccessible galley and existing outstanding batches.
- Avoid repeated duplicate jobs when a target is unmet but work is already underway.
- Revalidate at reservation and completion; once ingredients are reserved, policy changes must not erase or refund them twice.
- Emergency reserve override must be explicit and logged; critical starvation/dehydration should not be silently caused by routine policy locks.

**Gate:** saved policies, one-time batch execution, duplicate prevention, predictable cancellation/returns, water-reserve protection and clear emergency behavior.

## 7. Food variety, preferences and meal quality

- Give sailors a small bounded preference profile and bounded recent-meal history.
- Apply modest, decaying morale effects for variety, a preferred dish or monotonous emergency fare.
- Avoid per-bite mood spam; one meaningful meal memory per meal/window is enough.
- Preferences guide selection among available foods; they do not cause otherwise healthy sailors to reject safe emergency rations while starving.
- Meal quality may depend on recipe and actual cooking skill later, but cannot create extra mass or miraculous healing.
- Display why a sailor chose a dish and the actual morale effect.
- Do not claim calorie/protein/vitamin realism merely because foods have different names or tastes.

**Gate:** variety bonus is capped, repetition effects decay, preferences persist, emergency eating works, and identical seeds/time produce identical choices.

## 8. Food and recipe content expansion

Add incrementally after distinct cooked food and planning work. Package sizes and prices require a balance pass; each definition must include mass/bulk, source merchant, finite stock, buyers, regional price factors, production provenance and actual use.

| Food | Intended role | Candidate recipe/use | Dependency |
|---|---|---|---|
| Rice | Alternative dry staple | Rice porridge, fish rice | Recipe and cooking-water support |
| Oats | Cheap breakfast staple | Oat porridge | Named cooked output |
| Potatoes | Bulky fresh staple | Fish-and-potato stew | Edibility/cooking rules |
| Onions | Meal-variety ingredient | Bean or fish stew variants | Explicit multi-ingredient recipes |
| Salted pork | Preserved protein | Bean-and-pork stew | Explicit safe preparation rules |
| Hard cheese | Higher-price ready food | Cold ration with biscuit | Ready-food allocation |

### Recipe authoring checklist

- Stable ID, output food ID/name, exact ingredient g/mL, exact output g and documented losses.
- Required fixture/tool/fuel only if actually enforced.
- Work duration and allowed batch sizes bounded by hauling/fixture capacity.
- No negative/zero amounts, unknown IDs, impossible outputs or implicit substitutions.
- Recipe descriptions generated from data where practical so units cannot drift from behavior.

Initial additional dishes: oat porridge, fish-and-potato stew, bean-and-pork stew and declared onion variants. Existing grain porridge, bean stew and fish chowder remain useful rather than becoming obsolete filler.

## 9. Water sourcing and treatment

### Stage A — port refill

- Buy potable water from a documented supplier using actual funds, stock and destination capacity.
- Clearly state whether delivery is abstract porter service or a physical crew haul; never mix both accounting paths.
- Filling an existing container charges for contents/service, not another free container.

### Stage B — rain collection

- Requires weather that genuinely produces rain, installed collector/catchment and free receiving capacity.
- Yield is bounded by time, rainfall and catchment area; storm flags alone must not generate unlimited water.
- Persist collected quantity and classify water quality explicitly.

### Stage C — questionable water and boiling

- Store untreated water separately from potable water; ordinary drinking AI must not treat them as interchangeable.
- Boiling requires measured input, suitable vessel/galley, work time and finite fuel; output plus evaporation reconciles input.
- Boiling addresses the modeled biological contamination only. It does not desalinate seawater or remove every chemical hazard.
- No seawater-to-clean-water shortcut; distillation is a separate future system.
- If illness is introduced, document exposure and bounded consequences before enabling risky drinking.

**Gate:** no free refills, rain obeys capacity/weather, treatment consumes real resources, unsafe stock stays distinct and save/resume preserves quality.

## 10. Food freshness, spoilage and preservation

- Introduce batch age/condition only after named cooked output is stable.
- Fresh fruit, cooked meals and fresh vegetables deteriorate sooner than dry grain, biscuits, salted fish/pork and hard cheese.
- Publish game-scale freshness windows and storage modifiers; do not imply laboratory accuracy.
- Use earliest-expiring-first within permitted food/policy choices.
- Spoilage transfers mass to waste rather than silently deleting it; disposal is an explicit sink/action.
- Age continues while reserved/carried; transfer, repacking, sale or reload cannot reset it.
- Warn before spoilage; allow sensible ration prioritization without forcing micromanagement.
- Later salting/drying recipes need real inputs, time, yield/loss and suitable facilities. Do not advertise preservation crafting before it exists.

**Gate:** consistent elapsed-time decay, known old-save initialization, no freshness laundering, preserved foods remain useful, and waste reconciles mass.

## 11. Apparel with meaningful effects

### Candidate additions

| Garment | Intended distinction | Required mechanic |
|---|---|---|
| Leather apron | Limited task/contact protection | Modeled workplace hazards and coverage |
| Work gloves | Hand protection while working | Hand injury/hazard integration |
| Wool sweater | Insulation with weight/layer tradeoff | Temperature/exposure |
| Neckerchief | Comfort/appearance initially | No unsupported health bonus |
| Existing oilskin | Reduced wetting in rain | Real rain exposure and wetness |
| Existing wool cap | Warm head layer | Real temperature exposure; revisit arbitrary walking bonus |
| Existing seaboots | Practical footwear | Explain existing effect; later wetness/condition integration |

### Exposure foundation

- Define ambient conditions, rain/wetness, deck exposure, drying and bounded cold stress before assigning warmth numbers.
- A room designation does not prove a roof or shelter. Derive shelter from implemented geometry or state its abstraction explicitly.
- Support compatible layers and slots without unlimited stacking. Explain conflicts and combined effects.
- Do not accidentally apply shipboard work protection to the isolated tactical arena.
- Migrate/rebalance existing modifiers explicitly; avoid silently removing paid-for equipment utility.

### Condition and repair

- Persist issued garment identity and condition; count-only commodity stacks are insufficient once individual wear differs.
- Work/movement/exposure cause bounded, documented wear; no passive destruction while browsing menus.
- Damaged clothes lose relevant function gradually, with warnings before failure.
- Sewing consumes finite cloth/thread and work time using a sewing kit. It restores condition within a declared limit, not item duplication.
- Worn-item sale/pack transfer must preserve condition and reject canonical-new-item laundering.

**Gate:** useful differences, no false weather claims, compatible layers, finite repairs, condition-aware transfers and old-gear migration.

## 12. Everyday supplies and useful tools

| Item | Proposed action | Real cost/effect required before release |
|---|---|---|
| Bandages | Dress a supported injury | Finite dressing consumed; distinct from existing medicine doses |
| Soap | Personal/item washing | Consumes soap and measured water; cleanliness must have an observable use |
| Lamp oil | Refill a compatible lamp | Finite mL, actual burn time and lighting effect |
| Candles | Portable/simple light | Finite burn duration, clear extinguish behavior |
| Cloth/thread | Repair clothing | Exact material consumption and repair result |
| Sewing kit | Enables mending | Actual job requirement; not an unexplained global speed buff |
| Knife | Food preparation/tool action | Declared compatible recipes/tasks, not a duplicate mandatory tax |
| Ladle | Galley equipment | Genuine serving/capacity workflow or remain flavor/trade-only |
| Whetstone | Maintain applicable tools/blades | Requires modeled sharpness/condition and bounded maintenance |

Lighting requires installed/carried state, fuel, extinguishing and save-safe timers; integration with fire hazards should be deliberately scoped, not assumed. Bandages must not silently replace or duplicate existing infirmary treatment. Prefer reusing generic action/job definitions over item-ID branches.

## 13. Reusable containers and personal provisions

- Add waterskins, bottles, food sacks and barrels with capacity, empty mass, allowed contents and ownership/location.
- Filled mass equals tare plus contents; moving contents does not create another package.
- Distinguish merchant package, reusable vessel and ship fixture. Existing kegs currently model provision yield; do not retrospectively claim their tare was already simulated.
- Pour/fill/decant transactions validate source, destination, quality compatibility and capacity before mutation.
- Personal food/water is transferred from ship stocks and consumed once, never mirrored in both inventories.
- Decide whether crew carry personal supplies automatically before adding such behavior; start with explicit issuance/refill policy.
- Mixed-quality liquid rules must be explicit; adding clean water cannot magically sanitize unsafe contents.

**Gate:** empty/filled mass, partial pours, full-container rejection, quality propagation, container return, issued-item ownership and save/resume conservation.

## 14. Shipboard meals and social routine

- Offer meal windows around watch changes, using actual available meals and reachable mess positions.
- Stagger groups so the whole crew does not abandon helm, lookout or emergency work simultaneously.
- Meal scheduling encourages routine but urgent hunger/thirst overrides the schedule.
- Eating still consumes measured finite stocks; a social gathering is not an extra free meal.
- Small social benefits require overlapping real attendance, not just a mess designation.
- Preserve collision/pathing, seat/access limits and priority interruptions; sailors can resume work afterward.
- Report waiting for food, galley work, blocked mess access and remaining emergency supplies.

**Gate:** partial attendance, critical duty coverage, emergency interruption, reachable eating, finite stock and deterministic scheduled behavior.

## 15. Implementation order and completion gates

All items start pending. Each phase should be a separately reviewable change with evidence before moving on.

| Phase | Scope | Completion evidence |
|---|---|---|
| A | Named cooked foods, leftovers, smaller measured drinks | Quantity/mass, consumption and migration regressions |
| B | Honest provision report and galley orders | Read-only forecast, reserve policies, job lifecycle tests |
| C | Food preferences, variety and modest recipe/catalog additions | Bounded morale, useful choices, finite market/effect tests |
| D | Port water refill and reusable container foundation | Atomic transactions, tare/capacity/ownership tests |
| E | Batch freshness, spoilage and waste | Deterministic aging and mass reconciliation |
| F | Rain collection, fuel-backed boiling and water quality | Weather/input limits and treatment conservation |
| G | Exposure, clothing differentiation, wear and mending | Observable effects, individual-item migration and finite repairs |
| H | Supported bandage/hygiene/light/tool workflows | Each item has an enforced action, cost and visible result |
| I | Watch-aware shared meals and final balancing | Duty coverage, social attendance, multi-day gameplay preview |

Recommended first delivery: A + B only. Preserve separate later approvals/scope for large environmental, condition and medical changes. Container basics may precede D if needed by another phase; record dependency changes explicitly.

## 16. Save compatibility and data validation

- Add a clear schema/version marker for batch, condition, container and policy state rather than guessing from arbitrary key presence forever.
- Define migrations from generic food/water, current gram/mL saves and later batch saves; never rerun migration on already migrated data.
- Preserve stockpile, source reservation, carried bundle, transfer, active job, equipment and cooked-output relationships.
- Define deterministic initialization for legacy freshness, condition and preferences. Reject impossible references, unknown IDs, negative/noninteger quantities and invalid timestamps.
- Keep a safe replacement-world load: validation failure must not overwrite a working save.
- Stock defaults for new merchandise must not reset existing purchases or create duplicate issued kit.

## 17. Likely code/data touchpoints

These are planning touchpoints, not a requirement to enlarge existing modules indefinitely.

- `content/trade_items.json`: package definitions, provenance, markets and supported uses.
- `content/food_recipes.json`: measured recipes; extend with explicit cooked output IDs and requirements.
- `pirates/ship_storage.py`: canonical units, authoritative piles, reservations, capacity and migration.
- `pirates/ship.py`: consumption, job lifecycle, needs, cooked-food state and persistence.
- `pirates/economy.py`: finite buying/refilling, allocation, equipment/condition transfers and catalog validation.
- `pirates/voyage.py`: honest crew/route supply estimates.
- `pirates/management.py`, `pirates/trading_ui.py`, `play.py`: reports, galley policies, units, inspection and warnings.
- `pirates/ship_physics.py`: all actual contents, containers, waste and carried/issued mass without duplication.
- `pirates/ship_spaces.py`, `pirates/ship_fixtures.py`, `pirates/ship_hazards.py`: real facilities, access and deliberately scoped environmental effects.
- Extract focused provisioning/batch/policy modules when state complexity warrants it; share pure forecast helpers rather than duplicating formulas across UI and simulation.
- Do not alter vendored `engine/` renderer or unrelated combat/NPC/headless-sim work for this roadmap.

## 18. Verification strategy

### Automated scenarios

1. Buy named foods and water, allocate, haul, cook two different dishes, eat partial leftovers, save/load and reconcile all material.
2. Dry ingredients plentiful but no potable water/cook/galley: ready-food warning remains honest.
3. Simultaneous drinking, cooking reservation, firefighting and transfers cannot overspend the same water.
4. Cancel/reassign jobs at each phase; reserved or carried ingredients return once with age/quality intact.
5. Reduced drink size yields proportionate relief and consumes the exact available remainder.
6. Emergency reserves, disabled labor and policy edits produce clear, recoverable behavior.
7. Batch spoilage, container pours and garment repairs preserve identity/condition across save and trade transfers.
8. Many small advances equal one large advance for consumption, aging, wear, fuel and meal windows.
9. Old saves migrate once; malformed new snapshots fail safely.
10. New catalog goods have actual source stock, buyers, valid mass/bulk and demonstrated supported actions.

### Test and preview gates

Working directory: `E:/Pirates of Miska`.

```powershell
python -m pytest -q tests/test_ship_life.py tests/test_ship_storage.py tests/test_economy.py tests/test_voyage.py tests/test_ship_fixtures.py tests/test_ship_relocation.py tests/test_ship_hazards.py tests/test_ship_physics.py tests/test_ship_spaces.py tests/test_crew_lifecycle.py tests/test_trade_expansion.py
python -m pytest -q
python -m compileall -q play.py pirates tools tests
git diff --check
```

Add dedicated batch/policy/condition/container tests as those features land. Native save-free previews must demonstrate the stores report, distinct meals, recipe selection, shortage warning, equipment effects and relevant physical actions. Inspect screenshots; successful rendering alone is not proof of readable units or correct state.

### Manual acceptance story

Provision six sailors for a chosen route, identify the limiting resource, buy exactly the shortfall, protect emergency biscuits, order a meal, watch ingredients physically reach the galley, see named leftovers consumed, and inspect an honest remaining-days report. Later phases extend this story with refill, preservation, worn-clothing repair and shared meals.

## 19. Decisions to resolve during implementation

- Whether named cooked output begins as resource totals or immediately adopts located batches; avoid building two competing authorities.
- Initial drink target and thirst rates, validated against observed game-day use.
- Nominal ration budgets versus measured trends; forecasts must label both.
- Whether policies protect reserve stock from ordinary AI only or require a logged emergency override.
- Freshness windows, waste disposal and legacy-batch age policy.
- Container tare/packaging pricing and whether returned containers have a deposit.
- Exposure abstraction, compatible clothing layers and how existing bonuses transition.
- Which medical/hygiene/lighting mechanics are worthwhile before selling items that claim those effects.
- Exact package prices, merchant availability and recipe work/fuel costs after a small multi-day balance playtest.

## 20. Explicit non-goals

This roadmap does not itself authorize farming/fishing production, a complete nutrient/disease simulator, seawater desalination, engineering-grade thermal modeling, a multi-port economy rewrite, tactical-combat equipment unification or automatic integration with the separate headless world simulation. Those can be separately designed if requested.
