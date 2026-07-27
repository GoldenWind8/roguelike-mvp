/** Shared daily shop stock. Every Buy is a server transaction; this view only
 * mirrors the remaining globally available slots. */
import { useGame, useGameApi } from "../store/gameStore";
import { ItemArt } from "./ItemArt";


export function ShopModal() {
  const { shop, room, playerId } = useGame();
  const api = useGameApi();
  if (!shop) return null;

  const me = room && playerId ? room.players[playerId] : null;
  const pack = me?.inventory ?? [];
  const coins = me?.coins ?? 0;
  const nextStock = new Date(shop.restocks_at);
  const restockLabel = Number.isNaN(nextStock.getTime())
    ? "tomorrow"
    : nextStock.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });

  return (
    <div className="shop-veil" onClick={() => api.closeShop()}>
      <section className="shop-modal" onClick={(event) => event.stopPropagation()}>
        <header className="shop-head">
          <div>
            <div className="shop-kicker">Today&apos;s wares</div>
            <h2>{shop.label}</h2>
          </div>
          <div className="shop-purse" title="Your persistent coin balance">
            <span>●</span> {coins}
          </div>
        </header>

        {shop.stock.length === 0 ? (
          <div className="shop-empty">
            <strong>Sold out for today.</strong>
            <span>Fresh stock arrives {restockLabel}.</span>
          </div>
        ) : (
          <div className="shop-stock">
            {shop.stock.map((entry) => {
              const affordable = coins >= entry.price;
              return (
                <article key={entry.slot} className={`shop-item rarity-${entry.item.rarity}`}>
                  {entry.minted && <span className="shop-new">new today</span>}
                  <ItemArt item={entry.item} className="shop-item-icon" />
                  <div className="shop-item-copy">
                    <strong>{entry.item.name}</strong>
                    <em>{entry.item.rarity} {entry.item.type}</em>
                    <p>{entry.item.description}</p>
                  </div>
                  <button
                    type="button"
                    className="shop-buy"
                    disabled={!affordable}
                    title={affordable ? "Buy one" : `You need ${entry.price - coins} more coins`}
                    onClick={() => api.buyShopItem(
                      shop.object_id, entry.slot, entry.item.id, entry.stocked_on,
                    )}
                  >
                    <span>●</span> {entry.price}
                  </button>
                </article>
              );
            })}
          </div>
        )}

        {shop.buyback_prices && (
          <section className="shop-buyback">
            <div className="shop-section-head">
              <div>
                <div className="shop-kicker">Portable salvage</div>
                <h3>Sell from your pack</h3>
              </div>
              <span>One copy per trade. Equipped gear stays yours.</span>
            </div>
            {pack.length === 0 ? (
              <div className="shop-pack-empty">Your pack holds nothing Teo can price.</div>
            ) : (
              <div className="shop-stock">
                {pack.map((held, slot) => {
                  const price = shop.buyback_prices?.[held.item.rarity] ?? 0;
                  return (
                    <article
                      key={`${slot}-${held.item.id}`}
                      className={`shop-item rarity-${held.item.rarity}`}
                    >
                      <ItemArt item={held.item} className="shop-item-icon" />
                      <div className="shop-item-copy">
                        <strong>{held.item.name}{held.quantity > 1 ? ` x${held.quantity}` : ""}</strong>
                        <em>{held.item.rarity} {held.item.type}</em>
                        <p>{held.equipped ? "Equipped: put it away before selling." : held.item.description}</p>
                      </div>
                      <button
                        type="button"
                        className="shop-buy shop-sell"
                        disabled={held.equipped}
                        title={held.equipped ? "Put it away before selling" : "Sell one copy"}
                        onClick={() => api.sellShopItem(
                          shop.object_id, slot, held.item.id,
                        )}
                      >
                        Sell <span>●</span> {price}
                      </button>
                    </article>
                  );
                })}
              </div>
            )}
          </section>
        )}

        <footer className="shop-foot">
          <span>
            Stock is shared by every traveller. Fresh wares: {restockLabel}.
            {shop.buyback_prices ? " Buyback offers do not expire." : ""}
          </span>
          <button type="button" onClick={() => api.closeShop()}>Leave counter</button>
        </footer>
      </section>
    </div>
  );
}
