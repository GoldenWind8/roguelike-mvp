/** The surface the M6 party mechanic earned: who walks with you. */
import { useGame } from "../store/gameStore";

export function PartyPanel() {
  const { room, playerId } = useGame();
  const companions = room
    ? Object.values(room.npcs).filter((n) => n.party_owner_id === playerId)
    : [];

  return (
    <section className="panel">
      <h3>Companions</h3>
      {companions.length === 0 && (
        <p className="panel-empty">You walk alone — for now. Kind words can change that.</p>
      )}
      {companions.map((npc) => {
        const pct = Math.max(0, Math.round((npc.hp / npc.max_hp) * 100));
        const tone = pct > 60 ? "high" : pct > 30 ? "mid" : "low";
        return (
          <div key={npc.id} className={`companion ${npc.is_alive ? "" : "companion-dead"}`}>
            <span className="companion-icon">🧝‍♀️</span>
            <div className="companion-info">
              <div className="companion-name">
                {npc.name} <span className="companion-role">{npc.role}</span>
              </div>
              <div className="vigor-bar">
                <div className={`hp-fill hp-${tone}`} style={{ width: `${pct}%` }} />
              </div>
            </div>
            <span className="companion-note">{npc.is_alive ? "following you" : "fallen"}</span>
          </div>
        );
      })}
    </section>
  );
}
