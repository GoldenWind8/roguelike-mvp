/**
 * The at-a-glance "You" panel: vigor, armor, and strength straight off the
 * server's actor fields (hp / defense / attack_damage are all real contract
 * fields), plus what you're wearing — that part is mock-only until
 * inventory lands server-side.
 */
import { useGame } from "../store/gameStore";

export function PlayerPanel() {
  const { room, playerId, username, slots } = useGame();
  const me = playerId && room ? room.players[playerId] : null;
  if (!me) return null;

  const pct = Math.max(0, Math.round((me.hp / me.max_hp) * 100));
  const tone = pct > 60 ? "high" : pct > 30 ? "mid" : "low";
  const worn = slots.find((it) => it?.use === "passive");

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

      <div className="you-stats">
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

      {worn && (
        <div className="you-worn" title={worn.description}>
          {worn.icon} Wearing: {worn.name}
        </div>
      )}
    </section>
  );
}
