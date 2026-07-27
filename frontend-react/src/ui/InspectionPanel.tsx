/** Server-answered close look at an object; opens only when there is one.
 * Opened chests show what waits inside (finds nobody chose to take —
 * docs/LOOT.md); "Look inside" is the same open_chest request, which the
 * server answers with chest_contents so the selection popup can rise. */
import { useGame, useGameApi } from "../store/gameStore";
import { ItemArt } from "./ItemArt";

export function InspectionPanel() {
  const { inspection } = useGame();
  const api = useGameApi();
  if (!inspection) return null;

  const contents = inspection.contents ?? [];

  return (
    <section className="panel panel-inspection">
      <h3>
        A Closer Look
        <button className="panel-close" onClick={() => api.closeInspection()}>×</button>
      </h3>
      <div className="inspection-title">{inspection.label}</div>
      <p className="inspection-desc">{inspection.description}</p>
      <ul className="inspection-details">
        {inspection.details.map((d, i) => (
          <li key={i}>{d}</li>
        ))}
      </ul>

      {contents.length > 0 && (
        <div className="inspection-contents">
          <div className="contents-title">Waiting inside:</div>
          {contents.map((item, i) => (
            <div key={i} className={`contents-row rarity-${item.rarity}`} title={item.description}>
              <span className="contents-item">
                <ItemArt item={item} className="contents-item-icon" />
                <span>{item.name}</span>
              </span>
              <em>{item.rarity}</em>
            </div>
          ))}
          <button
            className="contents-take"
            onClick={() => {
              api.openChest(inspection.id);
              api.closeInspection();
            }}
          >
            Look inside (stand beside it)
          </button>
        </div>
      )}
    </section>
  );
}
