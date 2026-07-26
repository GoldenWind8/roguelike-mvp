/**
 * The at-a-glance "You" panel: vigor, armor, and strength straight off the
 * server's actor fields — all EFFECTIVE values (base + equipped gear +
 * ticking effects, backend/inventory.py), so the numbers here are exactly
 * what combat will use. Below them: what you're wearing and what's ticking.
 */
import { packOf, useGame } from "../store/gameStore";
import { itemIcon } from "./Hotbar";

export function PlayerPanel() {
  const { room, playerId, username } = useGame();
  const me = playerId && room ? room.players[playerId] : null;
  if (!me) return null;

  const pct = Math.max(0, Math.round((me.hp / me.max_hp) * 100));
  const tone = pct > 60 ? "high" : pct > 30 ? "mid" : "low";
  const worn = packOf(room, playerId).filter((s) => s.equipped);
  const ticking = me.active_effects ?? [];

  return (
    <section className="panel panel-you">
      <h3>You</h3>
      <div className="you-head">
        <span className="you-icon">🧝</span>
        <div className="you-id">
          <div className="you-name">{username}</div>
          <div className="you-state">{me.is_alive ? "warming by the fire" : "out cold…"}</div>
        </div>
      </div>

      <div className="you-vigor">
        <div className="vigor-bar vigor-wide">
          <div className={`hp-fill hp-${tone}`} style={{ width: `${pct}%` }} />
        </div>
        <span className="you-vigor-num">
          {me.hp} <em>/ {me.max_hp}</em>
        </span>
      </div>

      {me.hunger !== undefined && (
        <div
          className={`you-hunger ${me.hunger <= 0 ? "hunger-starving" : me.hunger <= 25 ? "hunger-low" : ""}`}
          title={
            me.hunger <= 0
              ? "You are starving — it's eating your vigor. Find food!"
              : me.hunger >= 80
                ? "Well fed: your wounds slowly knit themselves."
                : "Your belly. Food refills it; an empty one starves you."
          }
        >
          <span className="hunger-icon">🍗</span>
          <div className="hunger-bar">
            <div
              className="hunger-fill"
              style={{ width: `${Math.max(0, Math.round((me.hunger / (me.max_hunger ?? 100)) * 100))}%` }}
            />
          </div>
          <span className="you-vigor-num">
            {me.hunger <= 0 ? "starving!" : me.hunger >= 80 ? "well fed" : me.hunger <= 25 ? "hungry" : `${me.hunger}`}
          </span>
        </div>
      )}

      <div className="you-stats">
        <div className="stat-row stat-coins" title="Coins in your purse.">
          <span className="stat-icon">●</span>
          <span className="stat-label">Coins</span>
          <strong>{me.coins ?? 0}</strong>
        </div>
        <div className="stat-row" title="Softens every blow that lands on you.">
          <span className="stat-icon">🛡️</span>
          <span className="stat-label">Armor</span>
          <strong>{me.defense}</strong>
        </div>
        <div className="stat-row" title="The weight behind your strikes.">
          <span className="stat-icon">🗡️</span>
          <span className="stat-label">Strength</span>
          <strong>{me.attack_damage}</strong>
        </div>
      </div>

      {worn.length > 0 && (
        <div className="you-worn">
          {worn.map((s, i) => (
            <span key={i} title={s.item.description}>
              {itemIcon(s.item)} {s.item.name}
            </span>
          ))}
        </div>
      )}

      {ticking.length > 0 && (
        <div className="you-effects">
          {ticking.map((fx, i) => (
            <div
              key={i}
              className={`effect-row ${fx.amount >= 0 ? "effect-good" : "effect-bad"}`}
              title={`From the ${fx.source}.`}
            >
              <span>
                {fx.amount >= 0 ? "▲" : "▼"} {fx.stat.replace("_", " ")} {fx.amount > 0 ? `+${fx.amount}` : fx.amount}
              </span>
              <em>{Math.ceil(fx.remaining_s)}s</em>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
