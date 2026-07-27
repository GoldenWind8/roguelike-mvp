/**
 * The chest selection popup: what waits in the chest you opened (or peeked
 * into), one rarity-lit card per item, each with its own Take button — take
 * what you want, leave the rest for whoever comes next. Cards gray out live
 * as anyone at the chest grabs things (chest_looted broadcasts feed the
 * store); closing the popup never talks to the server.
 */
import { useGame, useGameApi, type LootFind } from "../store/gameStore";
import type { ItemView } from "../net/types";
import { ItemArt } from "./ItemArt";

const STAT_WORDS: Record<string, string> = {
  attack_damage: "strength",
  defense: "armor",
  max_hp: "vigor",
};

/** One human line per payload atom — the card's "what it does". */
function effectLines(item: ItemView): string[] {
  const lines: string[] = [];
  const p = item.payload;
  if (item.type === "weapon") {
    lines.push(`Damage ${p.damage}${(p.range ?? 1) > 1 ? `, strikes up to ${p.range} tiles away` : ""}`);
  }
  if (p.throw_range) {
    const size = p.area?.size ?? 0;
    lines.push(`Thrown up to ${p.throw_range} tiles${size > 0 ? `, blast of ${size}` : ""}`);
  }
  for (const atom of p.effects ?? []) {
    if (atom.kind === "restore_hp") lines.push(`Restores ${atom.amount} vigor`);
    else if (atom.kind === "restore_hunger") lines.push(`Fills ${atom.amount} belly`);
    else if (atom.kind === "damage") lines.push(`Deals ${atom.amount} on impact`);
    else if (atom.kind === "stat_mod") {
      const word = STAT_WORDS[atom.stat ?? ""] ?? atom.stat;
      const amount = atom.amount > 0 ? `+${atom.amount}` : `${atom.amount}`;
      lines.push(atom.duration_s
        ? `${amount} ${word} for ${atom.duration_s}s`
        : `${amount} ${word} while worn`);
    }
  }
  return lines;
}

function FindCard({ find, onTake, mine }: { find: LootFind; onTake: () => void; mine: boolean }) {
  const it = find.item;
  return (
    <div className={`loot-card loot-${it.rarity} ${find.takenBy ? "loot-card-taken" : ""}`}>
      {find.minted && <div className="loot-minted">✨ never seen before</div>}
      <ItemArt item={it} className="loot-card-icon" />
      <div className="loot-card-name">{it.name}</div>
      <div className="loot-card-rarity">{it.rarity}</div>
      <div className="loot-card-desc">{it.description}</div>
      <div className="loot-card-effects">
        {effectLines(it).map((l, i) => (
          <span key={i}>{l}</span>
        ))}
      </div>
      {find.takenBy ? (
        <div className={`loot-card-fate ${mine ? "fate-held" : "fate-left"}`}>
          {mine ? "in your pack" : `taken by ${find.takenBy}`}
        </div>
      ) : (
        <button className="loot-take" onClick={onTake}>Take</button>
      )}
    </div>
  );
}

export function ChestLootModal() {
  const { lootReveal, username } = useGame();
  const api = useGameApi();
  if (!lootReveal || lootReveal.finds.length === 0) return null;
  const { objectId, finds } = lootReveal;

  // An item's CURRENT index in the chest = untaken cards before it (the
  // chest only holds what nobody has grabbed, in original roll order).
  const currentIndex = (i: number) => finds.slice(0, i).filter((f) => !f.takenBy).length;
  const untaken = finds.filter((f) => !f.takenBy);

  const takeAll = () => {
    // Sequential sends: the server processes them in order, so each item is
    // at the FRONT (index 0) when its turn comes. If one take fails (pack
    // full), the rest bounce off the item_id guard — an error line each,
    // never the wrong item.
    untaken.forEach((f) => api.takeItem(objectId, 0, f.item.id));
  };

  return (
    <div className="loot-veil" onClick={() => api.closeLoot()}>
      <div className="loot-modal" onClick={(e) => e.stopPropagation()}>
        <div className="loot-title">
          {untaken.length === 0
            ? "The chest lies empty."
            : finds.length === 1
              ? "The chest creaks open…"
              : "The chest spills its hoard…"}
        </div>
        <div className="loot-cards">
          {finds.map((find, i) => (
            <FindCard
              key={i}
              find={find}
              mine={find.takenBy === username}
              onTake={() => api.takeItem(objectId, currentIndex(i), find.item.id)}
            />
          ))}
        </div>
        <div className="loot-actions">
          {untaken.length > 1 && (
            <button className="loot-close" onClick={takeAll}>Take everything</button>
          )}
          <button className="loot-close" onClick={() => api.closeLoot()}>
            {untaken.length === 0 ? "Take your leave" : "Leave the rest"}
          </button>
        </div>
      </div>
    </div>
  );
}
