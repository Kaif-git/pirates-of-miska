# Provisions, Clean Water, and Apparel Expansion

## Objective

Replace abstract ship food/water counters with named, measurable provisions; make food and water displays reflect real quantities; broaden useful clothing and consumable catalog content without adding decorative fields that have no gameplay effect.

## Current verified baseline

- `pirates/ship_storage.py` stores integer resource counters `raw_food`, `water`, `planks`, `shot`, and `medicine`; mass is inferred from a unit-mass table. The key `water` means one 1 kg unit, not a clearly stated liter.
- `pirates/ship.py` stores a separate integer `meals` count. Each eating action consumes one meal and each drink action consumes one water unit. The galley reserves 2 generic food units + 1 water unit and produces 6 meals.
- `content/trade_items.json` contains named grain, beans, fruit, dried fish, biscuit, and a sealed 10-unit water keg, but food allocation collapses several distinct goods into `raw_food` or `meals`. The trade catalog also has a work shirt, oilskin coat, and seaboots; wearable slots are currently `clothing` and `feet`.
- Trade cargo uses grams/ml, while ship stores use implicit abstract counts. Spatial storage, job reservations/cargo, capacity checks, ship physics, voyage estimates, store UI, and v3 save validation all depend on the current store keys.

## Canonical quantity and migration decisions

1. Store food quantities as integer grams and clean water as integer milliliters. Player-facing reports convert to kilograms (kg) and liters (L), with enough decimal precision to show useful amounts. A water item remains physically consistent: 1 mL weighs 1 g; a 10 L keg weighs 10 kg.
2. Eliminate `raw_food` and unlabeled `water` from current runtime/store UIs. Use named edible stocks (grain, beans, dried fish, fruit, ship's biscuit, and cooked stew) with explicit gram amounts; use `clean_water_ml` for potable water. No spoilage is implied unless separately implemented.
3. Use explicitly sized actions/recipes: define food eaten per action and water drunk per action in g/mL; recipes identify exact ingredient resources, exact clean-water volumes, cooked output mass, and description. Conserve mass or document evaporation/output loss.
4. Migrate old `raw_food` units as 1 kg each into a deterministic mixed stock of named foods with the same total mass. Migrate old water units as 1 L each. Migrate unconsumed meals as cooked stew mass using the documented serving mass. Migrate every location—stockpiles, reservations, worker cargo, transfers, saved totals, and active job records—without losing or duplicating supplies. Legacy stock-pile totals remain authoritative under the existing spatial-storage model.
5. Catalog package yields state exact grams/milliliters. `weight_g` remains the physical mass of the sold package; derived supply mass must equal the amount allocated. Item descriptions and UI use kg/L rather than “portions” or unlabeled counts.
6. Expand clothing in supported, player-visible equip slots and effects. Start with a small useful set (shirt, trousers, coat, headwear, footwear) using only modifiers the simulation actually applies; preserve current item IDs/slots via migration aliases or compatibility behavior. Do not claim weather protection until weather exposure uses it.

## Phases and acceptance gates

### Phase 1 — Quantity model and save migration
- Introduce canonical resource IDs/unit mass for named food grams and clean-water milliliters.
- Convert ship totals, pile content, bundles, job reservations, cargo, transfers, load/capacity and save validation; create migrations for old raw_food/water/meals stores and active jobs.
- Keep storage capacity in kg, show quantities in appropriate units, and preserve total physical mass.

**Gate:** legacy and current saves round-trip; in-flight jobs and carried/reserved supplies conserve quantities and mass; 10 L keg allocates as 10,000 mL and weighs 10 kg; no current `raw_food`/`water` resource exposure.

### Phase 2 — Named food, cooking, and consumption
- Allocate actual goods into named food resources; make ready-to-eat and galley-only food behavior explicit.
- Drive automatic galley jobs from exact recipes; consume exact named inputs and water, create exact cooked-food grams once.
- Make eating reduce hunger by consuming an explicit mass; report food stores as kg and voyage estimates as days using actual ration assumptions.

**Gate:** food mass and recipe inputs/outputs reconcile; ration use is finite; insufficient ingredients/water never consume partial bundles; save/reload preserves job and food state; old and new voyage estimates use kg/L and explain assumptions.

### Phase 3 — Apparel and item breadth
- Add a modest range of useful garments and everyday provisions/tools with provenance, price, dimensions, and supported effects.
- Add only necessary equipment slots, maintain legacy equipment behavior, and expose fit/effects in trade and crew-kit profiles.
- Ensure each product can be bought/sold/saved/equipped/allocated according to its declared role.

**Gate:** catalog validation and data-driven trade tests pass; equipment effects change their documented gameplay measure; save migration and cargo/kit capacities remain valid.

### Phase 4 — Integrated verification
- Audit user-facing strings for abstract “raw food,” unlabeled water counts, and inaccurate units.
- Run focused storage/economy/crew/voyage/item tests, full suite, compileall, diff checks, and save-free native previews for stores and equipment.

**Gate:** full test suite passes with known xfails only; all described units match physical mass and actual simulation behavior; unrelated dirty work remains untouched.

## Out of scope unless requested

- Food spoilage, farming/fishing production simulation, cooking fuel use, nutrition/calorie modeling, water purification, laundry/garment durability, seasonal weather protection, or a large multi-port expansion.
