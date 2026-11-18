# Trade Commodities and Merchant Career Plan

## Status

Implemented first pass, with the original scope and future ideas retained below. Eight trade-only fantasy commodities now use distinct source/import markets across Verdant Cay and Cinder Quay; port-office delivery contracts use finite issuer-funded escrow, deadlines, cargo delivery, and trader reputation. Spoilage, more ports, market production chains, and other items remain future work. This work does not change the parallel ship-construction scope.

### Verified implementation checkpoint

- `content/trade_items.json` now has 30 goods, including bluegrain, brine pears, emberpepper, mooniron, stormglass, lantern-moth silk, reef porcelain, and black kelp ink. Regional factors make each source cheaper than its documented destination; these goods are trade-only, not ship supplies or equipment.
- Item facts and definitions now live in `content/trade_items.json`: production method/inputs, found-at ports/merchants, market stock/buyers, prices, physical dimensions, regional factors, item effects, flavor text, and optional contract-offer data. Production inputs are explicitly lore-only until a production simulation is added.
- `content/trade_rules.json` holds shared fees, market-pricing/restock rules, and contract progression rules. `pirates/economy.py` is the generic interpreter and validates catalog/rule references; merchant inventory, accepted categories, and contract templates are derived from item data rather than item-ID branches.
- The economy retains finite stock/purse and cargo rules, bounded daily offers, a two-active-contract limit, four-day deadlines, escrow refunds on expiry, cargo-consuming delivery, and one trader reputation per completed delivery. Reputation 2 unlocks larger orders. Legacy economy saves default new contract state and newly added item stocks safely.
- `pirates/trading_ui.py` exposes the contract board under the port menu. Native SDL board screenshot: `C:/Users/Warp/AppData/Local/Temp/opencode/pirates-merchant-contract-board.png`.
- Verification: after the JSON refactor, `python -m pytest -q tests/test_trade_expansion.py tests/test_economy.py` passed 53 tests and full `python -m pytest -q` passed 459 tests. `python -m compileall -q pirates tests` and `git diff --check` passed.

### Adding an item without Python item-specific code

1. Add a new ID and object to `content/trade_items.json`.
2. Provide the required fields: `name`, `category`, `base_price`, `weight_g`, `bulk_ml`, `description`, `production` (`method`, `inputs`, `simulated`), `found_at`, `market_stock`, `market_buyers`, and a `regional_factors` value for every port.
3. For source/destination trade routes, add `origin` and `demand_ports`; importer factors must exceed the source factor. `market_stock` determines starting finite supplies; `found_at` must match those merchant/port locations.
4. Optionally add `contract_offer` (`id`, `issuer`, `destination`, `quantity`, `min_reputation`) to put the item on the daily contract board. The issuer must stock and buy the good.
5. Add effect metadata such as `supply`/`yield`, `slot`, or labor/need modifiers only when the existing generic code supports that behavior. Do not add item-ID checks to Python.
6. Run `python -m pytest -q tests/test_trade_expansion.py tests/test_economy.py`.

## Goals

- Make trade-only cargo worthwhile even when it has no direct player, crew, or ship utility.
- Give ports distinct production, shortages, buyers, and local flavor so profitable routes emerge from geography.
- Offer a satisfying lawful merchant progression path, not merely a safer way to earn less money than piracy.
- Use commodity names, provenance, and folklore to add fantasy worldbuilding without requiring every magical good to grant a gameplay power.
- Preserve the existing finite-stock, stock-sensitive market and cargo-capacity principles.

## Existing foundation and constraints

- At planning time, `content/trade_items.json` defined 22 goods. It mixed useful supplies and gear with trade-only iron, coal, and sailcloth; some descriptions explicitly say their production uses are not implemented.
- `pirates/economy.py` defines two regional economies, Verdant Cay and Cinder Quay, six merchants, finite merchant stocks and purses, regional price factors, stock-sensitive marginal quotes, and the new delivery-contract ledger.
- Trade cargo and issued gear share a 250 kg / 350 L capacity. Goods need plausible mass and bulk so trade involves cargo choices, not only price comparison.
- Current market prices are simulated locally. New commodities should not imply that spoilage, crafting, mining, or production chains already exist.
- The broader design document at `gemini-code-1794751887357.md` describes more regional economies and smuggling as aspirational scope; those regions and systems are not all implemented.

## Commodity families to explore

The first eight goods below are implemented working names, not necessarily final canon; other concepts remain proposals. Trade value should primarily come from regional availability and demand; direct utility is optional.

### Food and drink

| Working name | Origin / character | Trade identity |
| --- | --- | --- |
| Verdant bluegrain | Terraced farms on fertile islands | Bulky, steady staple; strong demand in mining and industrial settlements |
| Brine pears | Salt-preserved fruit from coastal orchards | Long-lasting provisions; a recognizable island export |
| Glowcap cheese | Cave-grown fungi and milk from upland herds | Perishable luxury; high value but riskier to carry if spoilage is later added |
| Emberpepper | Smoky red spice from volcanic slopes | Small, valuable parcels; luxury demand |
| Kelp-cider | Sour fermented drink from fishing communities | Common at its source, an acquired taste and curiosity elsewhere |
| Cloudberry cordial | Sweet spirit made from highland berries | Portable luxury cargo for affluent buyers |

### Fantasy minerals and curiosities

| Working name | Origin / character | Trade identity |
| --- | --- | --- |
| Stormglass | Blue-green glassy stone found after lightning storms | Rare ornament; sailors treat it as an omen, but it need not have magical effects |
| Mooniron | Silver-veined ore from remote mines | Heavy, valuable industrial/luxury export; prized by distant smiths |
| Singing salt | Crystals from tidal caves that hum in damp air | Unusual curiosity with religious and collector demand |
| Dreamstone | Opalescent mineral used in jewelry | Luxury good with disputed folklore; no gameplay power required |
| Leviathan amber | Fossil resin washed ashore after deep-sea storms | Rare high-value cargo tied to sea-monster legends |

### Crafted and cultural exports

- **Lantern-moth silk:** luminous thread woven into costly cloth.
- **Reef porcelain:** delicate coral-patterned ceramics; high-value and fragile if cargo damage is later modeled.
- **Black kelp ink:** dense ink sold to chartmakers, clerks, and scholars.
- **Tide-carved idols:** local religious or folk objects whose acceptance and value depend on the buyer's customs.
- Further candidates from the existing regional-economy concept: resin, pearls, obsidian, titanite, salt, and exotic fruit. Do not add all at once; each should have a clear source and at least one destination buyer.

## Merchant-route gameplay

1. **Regional supply and demand:** Assign each commodity one or more source regions and buyer regions. Source ports carry reliable stock at lower prices; importing ports have stronger demand and higher willingness to pay. Preserve finite stock and purse constraints.
2. **Cargo tradeoffs:** Give each good canonical base price, weight, and bulk. Use size/weight, fragility, and shelf life only when those properties affect an actual system. Avoid fake attributes that the simulation ignores.
3. **Merchant contracts:** Offer finite delivery orders with a requested commodity, quantity, destination, and optional deadline. Show the reward and any fee or penalty before acceptance. Contracts should be a new way to earn money and reputation, not a requirement for ordinary buying and selling.
4. **Trader reputation:** Completing deliveries can unlock larger orders, better contract access, merchant credit, or invitations to exclusive buyers. Keep this progression separate from pirate notoriety and do not turn it into combat-stat grinding.
5. **Risk and variety:** Later options may include seasonal demand, port shortages, taxes, fragile cargo, or restricted goods. Add only when the player can understand the risk before committing. Smuggling should be a high-risk alternative, not the only profitable route.
6. **Worldbuilding:** Give each good a concise description with origin, producer, and a local belief, custom, or use. Let the same item be mundane in one port and a luxury or taboo in another.

## Suggested first content pass

Start with a small, readable addition rather than a large catalog:

- Add 6-8 trade-only goods across food, fantasy minerals, and crafted luxuries.
- Give every new good at least one plausible source and one distinct buyer/demand region; avoid distributing every good everywhere.
- Use the existing two-port contrast first (Verdant Cay's farms/forests versus Cinder Quay's foundries/workshops). Add new ports only when their geography, merchants, and market identity are part of the agreed scope.
- Test profitable reciprocal routes with fees and stock-sensitive marginal quotes. Avoid unlimited buy-low/sell-high exploits caused by replenishing stock or purses without a sink.
- Add contracts and trader reputation as a separate follow-up phase after commodity flow is fun and understandable.

## Acceptance criteria for implementation

- Trade-only goods can be bought, carried, sold, saved, and loaded without requiring a ship-supply allocation or equipment slot.
- Catalog validation rejects missing or invalid price, category, mass, bulk, and regional market references.
- Every added good has documented source/demand flavor and a functioning market route; no commodity is merely decorative unless explicitly intended as a rare curiosity.
- Quotes remain stock-sensitive and finite, and multi-unit quotes account for marginal pricing, transaction costs, cargo capacity, and merchant funds.
- The trader route can generate sustainable profit through delivery/trade choices without requiring piracy or combat.
- Save/load remains compatible with older cargo snapshots and validates newly introduced item IDs safely.
- Tests cover route profitability after costs, stock/purse conservation, capacity constraints, save/load, and any contract/reputation reward-once behavior.

## Out of scope for the first pass

- Magical item powers, crafting recipes, mining gameplay, production simulation, or ship upgrades for every commodity.
- Spoilage, breakage, or cargo inspection until those mechanics are designed and can be explained to the player.
- A full multi-port world expansion solely to justify a long item list.
- Changes to the tactical combat or parallel ship-construction systems.
