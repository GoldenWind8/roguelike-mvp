/** Shared daily shop stock. Every Buy is a server transaction; this view only
 * mirrors the remaining globally available slots. */
import { useEffect, useRef } from "react";

import { useGame, useGameApi } from "../store/gameStore";
import { ItemArt } from "./ItemArt";
import { usePointerSettle } from "./usePointerSettle";


export function ShopModal() {
  const { shop, shopPending, room, playerId } = useGame();
  const api = useGameApi();
  const modalRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const me = room && playerId ? room.players[playerId] : null;
  const pointerSettled = usePointerSettle(shop?.object_id);

  useEffect(() => {
    if (!shop) return;
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const frame = window.requestAnimationFrame(() => closeRef.current?.focus());
    return () => {
      window.cancelAnimationFrame(frame);
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [shop?.object_id]);

  useEffect(() => {
    if (!shop) return;
    const frame = window.requestAnimationFrame(() => {
      const modal = modalRef.current;
      const active = document.activeElement;
      if (
        !modal
        || (
          modal.contains(active)
          && !(active instanceof HTMLButtonElement && active.disabled)
        )
      ) return;
      (
        modal.querySelector<HTMLElement>(
          ".shop-close:not(:disabled), button:not(:disabled)",
        )
        ?? modal
      ).focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [shop?.object_id, shop?.stock, shopPending, me?.inventory]);

  if (!shop) return null;

  const pack = me?.inventory ?? [];
  const coins = me?.coins ?? 0;
  const nextStock = new Date(shop.restocks_at);
  const restockLabel = Number.isNaN(nextStock.getTime())
    ? "tomorrow"
    : nextStock.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });

  return (
    <div
      className="shop-veil"
      onMouseDown={(event) => {
        if (
          event.currentTarget === event.target
          && !shopPending
          && pointerSettled(event)
        ) {
          api.closeShop();
        }
      }}
    >
      <section
        ref={modalRef}
        className="shop-modal"
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="shop-title"
        aria-busy={shopPending !== null}
        onKeyDown={(event) => {
          if (event.key !== "Tab" || !modalRef.current) return;
          const focusable = Array.from(
            modalRef.current.querySelectorAll<HTMLElement>(
              "button:not(:disabled), [href], [tabindex='0']",
            ),
          );
          if (focusable.length === 0) {
            event.preventDefault();
            modalRef.current.focus();
            return;
          }
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (event.shiftKey && (
            document.activeElement === first
            || document.activeElement === modalRef.current
          )) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }}
      >
        <header className="shop-head">
          <div>
            <div className="shop-kicker">Today&apos;s wares</div>
            <h2 id="shop-title">{shop.label}</h2>
          </div>
          <div className="shop-head-actions">
            <div
              className="shop-purse"
              title="Your persistent coin balance"
              aria-live="polite"
              aria-label={`${coins} coin${coins === 1 ? "" : "s"}`}
            >
              <span aria-hidden>●</span> {coins}
            </div>
            <button
              ref={closeRef}
              type="button"
              className="shop-close"
              onClick={(event) => {
                if (pointerSettled(event)) api.closeShop();
              }}
              disabled={shopPending !== null}
              title="Leave counter (Esc)"
              aria-label="Leave counter"
            >
              ×
            </button>
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
                    disabled={shopPending !== null || !affordable}
                    title={affordable
                      ? "Buy one"
                      : `You need ${entry.price - coins} more coin${entry.price - coins === 1 ? "" : "s"}`}
                    aria-label={`Buy ${entry.item.name} for ${entry.price} coin${entry.price === 1 ? "" : "s"}`}
                    onClick={(event) => {
                      if (!pointerSettled(event)) return;
                      api.buyShopItem(
                        shop.object_id,
                        entry.slot,
                        entry.item.id,
                        entry.stocked_on,
                      );
                    }}
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
                        disabled={shopPending !== null || held.equipped}
                        title={held.equipped ? "Put it away before selling" : "Sell one copy"}
                        aria-label={`Sell ${held.item.name} for ${price} coin${price === 1 ? "" : "s"}`}
                        onClick={(event) => {
                          if (!pointerSettled(event)) return;
                          api.sellShopItem(
                            shop.object_id,
                            slot,
                            held.item.id,
                          );
                        }}
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
          <button
            type="button"
            disabled={shopPending !== null}
            onClick={(event) => {
              if (pointerSettled(event)) api.closeShop();
            }}
          >
            {shopPending ? "Finishing trade…" : "Leave counter"}
          </button>
        </footer>
      </section>
    </div>
  );
}
