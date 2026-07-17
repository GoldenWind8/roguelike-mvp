/** Server-answered close look at an object; opens only when there is one. */
import { useGame, useGameApi } from "../store/gameStore";

export function InspectionPanel() {
  const { inspection } = useGame();
  const api = useGameApi();
  if (!inspection) return null;

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
    </section>
  );
}
