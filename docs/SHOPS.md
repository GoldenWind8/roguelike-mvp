# Exploration Shops

Shops are the first reusable exploration service. They connect authored room
objects to persistent global stock, player currency, the existing item pool,
and a typed client interaction without putting shop rules in the room engine.

## Current Contract

- A placed room object opts into `"interaction": "shop"`.
- `content/shops.json` binds that stable placed-object id to a shop id, label,
  stock size, and rarity weights.
- A shop holds five one-copy slots by default. Stock is shared globally: once
  any player buys a slot, it is gone for everyone.
- On the first open of a new UTC date, `shop_store.ensure_daily_stock()` clears
  yesterday's leftovers and fills the shop through `loot.spawn_loot()`.
- Restocking is lazy. Opening the shop performs the date check, while a
  per-shop async lock prevents duplicate generation in the current process.
- `shop_states` remembers the last restock date even after every stock row is
  bought. Sold out therefore means sold out until the next UTC date.
- `shop_stock` stores immutable `item_view` snapshots, matching player packs
  and chests.
- Prices are server-owned rarity bands. The client only displays them.

## Purchase Transaction

`buy_shop_item` carries the stable object id, slot, item id, and stock date.
The server revalidates proximity and shop identity, then one transaction:

1. Locks and rechecks the global stock row.
2. Locks the player's account row.
3. Checks the authoritative coin balance.
4. Builds a detached candidate pack and checks slot capacity.
5. Deducts coins, saves the pack, deletes the stock row, and commits.

Only after commit does the handler update the live player and broadcast
`shop_purchased`. The stock date is an optimistic token: yesterday's open
panel cannot accidentally purchase a reused slot from today's selection.

## Currency

Coins are a non-negative scalar on `players` and the live `Player`, not an
inventory item. New and backfilled accounts start with 30 coins. Disconnect
saves the balance with other character state, while shop purchases write it
through immediately as part of the transaction.

Coin rewards and selling are deliberately not included yet. They should mutate
the same balance through focused server services rather than introduce coin
items into the pack.

## Boundaries

- `shop_defs.py`: validates authored shop policy.
- `shop_store.py`: daily lifecycle, persistence, pricing, and purchase rules.
- `main.py`: proximity/authentication, lock discipline, and transport.
- `loot.py`: remains the one item-generation entry point.
- `inventory.py`: remains the one pack-capacity and stacking rule source.
- React: mirrors server state and submits typed commands; it never decides
  whether a purchase succeeds.

The global `state_lock` serializes live mutations and purchases in the current
single-process runtime. When the runtime becomes multi-process, PostgreSQL row
locks on `shop_stock` and `players` become the cross-worker authority; the
service boundary and wire contract do not need to change.
