/**
 * Dialogue is the flagship mechanic (recruit a follower, talk your way into
 * and out of a fight), and it lives where transient context lives: the right
 * column, temporarily taking the place of inspection + chronicle. The world,
 * your stats, and the belt stay fully visible — when a conversation goes
 * wrong you WATCH the hall turn on you beside the words. Esc closes it.
 */
import { useEffect, useRef, useState } from "react";
import { useGame, useGameApi } from "../store/gameStore";
import { npcIcon } from "../grid/RoomGrid";

// Keyed by NAME, not id: server ids are row-derived and can change across
// reseeds, but Mara is Mara.
const SUGGESTIONS: Record<string, { neutral: string[]; hostile: string[] }> = {
  Basil: {
    neutral: ["What do you know about the north road?", "What remedies do you make?", "How long have you lived in Oakrun?"],
    hostile: ["I meant no threat. Let's speak plainly."],
  },
  "Elowen Pike": {
    neutral: ["What news came through the inn?", "Who arrived from the north?", "Tell me about Oakrun."],
    hostile: [],
  },
  "Rowan Hale": {
    neutral: ["What did the northern report say?", "Why do the dates trouble you?", "Who sent you?"],
    hostile: [],
  },
  Mara: {
    neutral: ["Fight beside me?", "Can you heal me?", "What keeps you in this hall?"],
    hostile: [],
  },
  Gorrik: {
    neutral: ["Any rumors, Gorrik?", "Got a room for the night?", "Your ale is watered, old man."],
    hostile: ["I'm sorry — that was out of line.", "Easy now. Peace."],
  },
};

function dispositionLabel(d: string): string {
  if (d === "hostile") return "⚔️ hostile";
  if (d === "friendly") return "❤ friendly";
  return "· neutral";
}

export function DialoguePanel() {
  const { dialogue, room, knownPeople } = useGame();
  const api = useGameApi();
  const [draft, setDraft] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const npc = dialogue && room ? room.npcs[dialogue.npcId] : null;
  const knownNpc = npc?.world_id
    ? knownPeople.find((candidate) => candidate.world_id === npc.world_id)
    : undefined;

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [dialogue?.lines.length, dialogue?.pending]);

  useEffect(() => {
    if (dialogue) inputRef.current?.focus();
  }, [dialogue?.npcId]);

  if (!dialogue || !npc) return null;

  const say = (text: string) => {
    api.talk(text);
    setDraft("");
  };

  const authoredTopics = npc.dialogue_topics ?? knownNpc?.dialogue_topics;
  const suggestions = authoredTopics?.map((topic) => ({
    id: topic.id,
    label: topic.label,
    prompt: topic.prompt,
  })) ?? (
    SUGGESTIONS[npc.name]?.[npc.disposition === "hostile" ? "hostile" : "neutral"] ?? []
  ).map((prompt) => ({ id: prompt, label: prompt, prompt }));
  const activity = npc.activity ?? knownNpc?.activity;
  const relationshipLabel = npc.disposition === "hostile"
    ? dispositionLabel("hostile")
    : knownNpc?.relationship ?? dispositionLabel(npc.disposition);
  const relationshipClass = npc.disposition === "hostile"
    ? "disp-hostile"
    : knownNpc
      ? `bond-${knownNpc.relationship}`
      : `disp-${npc.disposition}`;

  return (
    <section className="panel panel-dialogue">
      <div className="dialogue-head">
        {npc.image ? (
          <span className="dialogue-portrait">
            <img src={npc.image} alt="" draggable={false} />
          </span>
        ) : (
          <span className="dialogue-icon">{npcIcon(npc.role)}</span>
        )}
        <div className="dialogue-who">
          <span className="dialogue-name">{npc.name}</span>
          <span className="dialogue-role">{npc.role}</span>
        </div>
        <span className={`disp-chip ${relationshipClass}`}>{relationshipLabel}</span>
        <button
          className="panel-close"
          onClick={() => api.closeDialogue()}
          title="Close (Esc)"
          aria-label={`Close conversation with ${npc.name}`}
        >
          ×
        </button>
      </div>

      {(activity?.label || knownNpc?.relationship_note) && (
        <div className="dialogue-context">
          {activity?.label && (
            <span className={`dialogue-activity activity-${activity.kind}`}>
              <i className="activity-pulse" aria-hidden />
              {activity.label}
            </span>
          )}
          {knownNpc?.relationship_note && (
            <span className="dialogue-bond-note">{knownNpc.relationship_note}</span>
          )}
        </div>
      )}

      <div
        className="dialogue-log"
        ref={logRef}
        role="log"
        aria-live="polite"
        aria-relevant="additions text"
        aria-busy={dialogue.pending}
      >
        {dialogue.lines.length === 0 && !dialogue.pending && (
          <div className="dialogue-line line-hint">
            {npc.name} waits to hear what you have to say.
          </div>
        )}
        {dialogue.lines.map((line, i) => (
          <div key={i} className={`dialogue-line line-${line.who}`}>
            <span className="line-speaker">{line.who === "player" ? "You" : npc.name}</span>
            {line.text}
          </div>
        ))}
        {dialogue.pending && <div className="dialogue-line line-hint">{npc.name} considers…</div>}
      </div>

      {suggestions.length > 0 && (
        <div className="dialogue-chips">
          {suggestions.map((suggestion) => (
            <button
              key={suggestion.id}
              disabled={dialogue.pending}
              onClick={() => say(suggestion.prompt)}
            >
              {suggestion.label}
            </button>
          ))}
        </div>
      )}

      <div className="dialogue-input-row">
        <input
          ref={inputRef}
          value={draft}
          maxLength={300}
          placeholder={`Say something to ${npc.name}…`}
          aria-label={`Say something to ${npc.name}`}
          disabled={dialogue.pending}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") say(draft);
            if (e.key === "Escape") api.closeDialogue();
          }}
        />
        <button disabled={dialogue.pending || !draft.trim()} onClick={() => say(draft)}>
          Say
        </button>
      </div>
    </section>
  );
}
