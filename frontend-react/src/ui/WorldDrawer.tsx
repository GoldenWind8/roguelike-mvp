import { useEffect, useMemo, useRef } from "react";
import type {
  KnownNpcAvailability,
  KnownNpcCondition,
  KnownNpcView,
  KnowledgeProvenance,
  RelationshipTone,
  RumorView,
  WorldChronicleEntry,
  WorldPhase,
} from "../net/types";
import { useGame, useGameApi, type WorldDrawerTab } from "../store/gameStore";

const TABS: { id: WorldDrawerTab; label: string; icon: string }[] = [
  { id: "rumors", label: "Rumors", icon: "⌁" },
  { id: "people", label: "People", icon: "◇" },
  { id: "chronicle", label: "Chronicle", icon: "◷" },
];

const PROVENANCE: Record<KnowledgeProvenance, string> = {
  witnessed: "You witnessed this",
  heard: "Something you heard",
  found: "Something you found",
};

const RELATIONSHIP_LABEL: Record<RelationshipTone, string> = {
  hostile: "Hostile",
  wary: "Wary",
  unfamiliar: "Unfamiliar",
  cordial: "Cordial",
  trusting: "Trusting",
  devoted: "Devoted",
};

const AVAILABILITY: Record<KnownNpcAvailability, { label: string; icon: string }> = {
  present: { label: "Nearby", icon: "●" },
  travelling: { label: "On the road", icon: "↝" },
  away: { label: "Elsewhere", icon: "○" },
  unknown: { label: "Whereabouts unknown", icon: "?" },
  dead: { label: "Known dead", icon: "†" },
};

const AVAILABILITY_ORDER: Record<KnownNpcAvailability, number> = {
  present: 0,
  travelling: 1,
  away: 2,
  unknown: 3,
  dead: 4,
};

const CONDITION: Record<KnownNpcCondition, { label: string; icon: string }> = {
  well: { label: "Appeared unhurt", icon: "·" },
  wounded: { label: "Wounded when last seen", icon: "✚" },
  critical: { label: "Gravely wounded when last seen", icon: "!" },
  dead: { label: "Known dead", icon: "†" },
  unknown: { label: "Condition unknown", icon: "?" },
};

function timeIcon(phase: WorldPhase | undefined): string {
  switch (phase) {
    case "deep_night": return "☾";
    case "dawn": return "◐";
    case "morning": return "☼";
    case "afternoon": return "☀";
    case "dusk": return "◑";
    case "night": return "☽";
    default: return "◷";
  }
}

/** Server prose is preferred. ISO timestamps are made human-readable, while
 * authored calendar text ("third bell", "yesterday at dusk") passes through. */
function displayWhen(value: string): string {
  if (!value) return "Time uncertain";
  if (!value.includes("T")) return value;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function chronicleMinute(entry: WorldChronicleEntry): number {
  return entry.world_minute ?? entry.world_tick ?? 0;
}

function RumorCard({ rumor }: { rumor: RumorView }) {
  return (
    <article className={`world-card rumor-card ${rumor.unread ? "world-card-unread" : ""}`}>
      <div className="world-card-kicker">
        <span className={`provenance provenance-${rumor.provenance}`}>
          {PROVENANCE[rumor.provenance]}
        </span>
        <span>{displayWhen(rumor.learned_at)}</span>
      </div>
      <h3>{rumor.title}</h3>
      <p>{rumor.body}</p>
      {(rumor.source || rumor.place) && (
        <footer className="world-card-foot">
          {rumor.source && <span>From {rumor.source}</span>}
          {rumor.place && <span>Near {rumor.place}</span>}
        </footer>
      )}
    </article>
  );
}

function PersonPortrait({ npc }: { npc: KnownNpcView }) {
  const monogram = npc.name.trim().charAt(0).toUpperCase() || "?";
  return (
    <span className="world-person-portrait" aria-hidden>
      {npc.image && (
        <img
          src={npc.image}
          alt=""
          draggable={false}
          onLoad={(event) => {
            event.currentTarget.hidden = false;
          }}
          onError={(event) => {
            event.currentTarget.hidden = true;
          }}
        />
      )}
      <span
        className={[
          "world-person-monogram",
          npc.image ? "" : "world-person-monogram-only",
        ].join(" ")}
      >
        {monogram}
      </span>
    </span>
  );
}

function PersonCard({ npc }: { npc: KnownNpcView }) {
  // Every non-present state in this payload is an observation snapshot, not
  // simulator truth: e.g. "away" means the player returned and found them
  // gone, while "dead" means the player witnessed the body.
  const visibleAvailability = npc.availability;
  const availability = AVAILABILITY[visibleAvailability];
  const condition = npc.condition ? CONDITION[npc.condition.kind] : null;
  const showCondition = condition
    && npc.condition?.kind !== "well"
    && npc.condition?.kind !== "unknown"
    && !(visibleAvailability === "dead" && npc.condition?.kind === "dead");
  return (
    <article
      className={[
        "world-card",
        "person-card",
        `person-${visibleAvailability}`,
        npc.unread ? "world-card-unread" : "",
      ].join(" ")}
    >
      <div className="person-card-head">
        <PersonPortrait npc={npc} />
        <div className="person-identity">
          <h3>{npc.name}</h3>
          <span>{npc.role}</span>
        </div>
        <span
          className={`relationship-word relationship-${npc.relationship}`}
          title="How they seem to regard you"
        >
          {RELATIONSHIP_LABEL[npc.relationship]}
        </span>
      </div>

      <div className="person-cues">
        <div className={`person-cue availability-${visibleAvailability}`}>
          <span aria-hidden>{availability.icon}</span>
          <span>{availability.label}</span>
        </div>
        {visibleAvailability === "present" && npc.activity?.label && (
          <div className={`person-cue activity-${npc.activity.kind}`}>
            <span className="activity-pulse" aria-hidden />
            <span>{npc.activity.label}</span>
          </div>
        )}
        {showCondition && condition && npc.condition && (
          <div className={`person-cue condition-${npc.condition.kind}`}>
            <span aria-hidden>{condition.icon}</span>
            <span>{condition.label}</span>
          </div>
        )}
      </div>

      {npc.relationship_note && <p className="relationship-note">{npc.relationship_note}</p>}

      {npc.last_seen && visibleAvailability !== "present" && (
        <footer className="last-seen">
          <span>Last seen: {npc.last_seen.room_name}</span>
          <span>{displayWhen(npc.last_seen.at)}</span>
          {npc.last_seen.note && <em>{npc.last_seen.note}</em>}
        </footer>
      )}
    </article>
  );
}

function ChronicleCard({ entry }: { entry: WorldChronicleEntry }) {
  return (
    <article className={`world-card away-card ${entry.unread ? "world-card-unread" : ""}`}>
      <div className="world-card-kicker">
        <span className={`provenance provenance-${entry.provenance}`}>
          {PROVENANCE[entry.provenance]}
        </span>
        <span>{displayWhen(entry.happened_at)}</span>
      </div>
      <h3>{entry.title}</h3>
      <p>{entry.body}</p>
      {entry.place && <footer className="world-card-foot"><span>Near {entry.place}</span></footer>}
    </article>
  );
}

function EmptyWorldPage({
  symbol,
  title,
  children,
}: {
  symbol: string;
  title: string;
  children: string;
}) {
  return (
    <div className="world-empty">
      <span className="world-empty-symbol" aria-hidden>{symbol}</span>
      <strong>{title}</strong>
      <p>{children}</p>
    </div>
  );
}

function RumorsPage({ rumors }: { rumors: RumorView[] }) {
  if (rumors.length === 0) {
    return (
      <EmptyWorldPage symbol="⌁" title="The roads are keeping their counsel.">
        Speak with travellers, read what people leave behind, and listen where stories cross.
      </EmptyWorldPage>
    );
  }
  return (
    <>
      <p className="world-page-note">
        Stories as they reached you. They may be mistaken, incomplete, or told for a reason.
      </p>
      <div className="world-card-list">
        {/* world_sync is chronological and deltas append; reversing avoids
            lexicographic "Day 9" > "Day 10" timestamp mistakes. */}
        {[...rumors].reverse().map((rumor) => <RumorCard key={rumor.id} rumor={rumor} />)}
      </div>
    </>
  );
}

function PeoplePage({ people }: { people: KnownNpcView[] }) {
  if (people.length === 0) {
    return (
      <EmptyWorldPage symbol="◇" title="No face has stayed with you yet.">
        Meet people, ask after names, and pay attention when familiar routines change.
      </EmptyWorldPage>
    );
  }
  const sorted = [...people].sort((a, b) =>
    AVAILABILITY_ORDER[a.availability]
    - AVAILABILITY_ORDER[b.availability]
    || a.name.localeCompare(b.name));
  return (
    <>
      <p className="world-page-note">
        Faces as you last understood them. Distance and silence make liars of certainty.
      </p>
      <div className="world-card-list people-list">
        {sorted.map((npc) => <PersonCard key={npc.world_id} npc={npc} />)}
      </div>
    </>
  );
}

function ChronicleSection({
  title,
  entries,
}: {
  title: string;
  entries: WorldChronicleEntry[];
}) {
  if (entries.length === 0) return null;
  return (
    <section className="away-section">
      <h2>{title}</h2>
      <div className="world-card-list">
        {entries.map((entry) => <ChronicleCard key={entry.id} entry={entry} />)}
      </div>
    </section>
  );
}

function ChroniclePage({ entries }: { entries: WorldChronicleEntry[] }) {
  if (entries.length === 0) {
    return (
      <EmptyWorldPage symbol="◷" title="No word followed you home.">
        That is not the same as nothing changing. Return to old places and look for what was left behind.
      </EmptyWorldPage>
    );
  }

  const sorted = [...entries].sort((a, b) =>
    chronicleMinute(b) - chronicleMinute(a)
    || b.happened_at.localeCompare(a.happened_at));
  const away = sorted.filter((entry) => entry.while_away);
  const sinceReturn = sorted.filter((entry) => !entry.while_away);

  return (
    <>
      <p className="world-page-note">
        Changes that reached you through witnesses, evidence, or word of mouth. Gaps remain.
      </p>
      <ChronicleSection title="While you were away" entries={away} />
      <ChronicleSection title={away.length > 0 ? "Since your return" : "Recent word"} entries={sinceReturn} />
    </>
  );
}

export function WorldDrawer() {
  const {
    worldDrawerOpen,
    worldDrawerTab,
    worldTime,
    rumors,
    worldChronicle,
    knownPeople,
  } = useGame();
  const api = useGameApi();
  const drawerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!worldDrawerOpen) return;
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    drawerRef.current?.focus();
    return () => previousFocus?.focus();
  }, [worldDrawerOpen]);

  const unread = useMemo(() => ({
    rumors: rumors.filter((entry) => entry.unread).length,
    people: knownPeople.filter((entry) => entry.unread).length,
    chronicle: worldChronicle.filter((entry) => entry.unread).length,
  }), [rumors, knownPeople, worldChronicle]);

  if (!worldDrawerOpen) return null;

  const moveTabFocus = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    currentTab: WorldDrawerTab,
  ) => {
    const currentIndex = TABS.findIndex((tab) => tab.id === currentTab);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % TABS.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + TABS.length) % TABS.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = TABS.length - 1;
    if (nextIndex === null) return;

    event.preventDefault();
    const nextTab = TABS[nextIndex].id;
    api.setWorldDrawerTab(nextTab);
    requestAnimationFrame(() => document.getElementById(`world-tab-${nextTab}`)?.focus());
  };

  return (
    <div
      className="world-drawer-veil"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target) api.closeWorldDrawer();
      }}
    >
      <aside
        id="world-drawer"
        className="world-drawer"
        ref={drawerRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="world-drawer-title"
        onKeyDown={(event) => {
          if (event.key !== "Tab" || !drawerRef.current) return;
          const focusable = Array.from(
            drawerRef.current.querySelectorAll<HTMLElement>("button:not(:disabled), [href], [tabindex='0']"),
          );
          if (focusable.length === 0) {
            event.preventDefault();
            return;
          }
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (event.shiftKey && (
            document.activeElement === first
            || document.activeElement === drawerRef.current
          )) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }}
      >
        <header className="world-drawer-head">
          <div>
            <span className="world-drawer-kicker">What has reached you</span>
            <h1 id="world-drawer-title">World</h1>
          </div>
          <div className="world-drawer-time">
            <span aria-hidden>{timeIcon(worldTime?.phase)}</span>
            <span>{worldTime?.label ?? "Time unreckoned"}</span>
          </div>
          <button
            className="world-drawer-close"
            onClick={() => api.closeWorldDrawer()}
            title="Close (J or Esc)"
            aria-label="Close the World"
          >
            ×
          </button>
        </header>

        <nav className="world-tabs" role="tablist" aria-label="World knowledge">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              id={`world-tab-${tab.id}`}
              className={worldDrawerTab === tab.id ? "world-tab-active" : ""}
              role="tab"
              aria-selected={worldDrawerTab === tab.id}
              aria-controls={`world-page-${tab.id}`}
              tabIndex={worldDrawerTab === tab.id ? 0 : -1}
              onClick={() => api.setWorldDrawerTab(tab.id)}
              onKeyDown={(event) => moveTabFocus(event, tab.id)}
            >
              <span className="world-tab-icon" aria-hidden>{tab.icon}</span>
              <span>{tab.label}</span>
              {unread[tab.id] > 0 && (
                <span className="world-tab-unread" aria-label={`${unread[tab.id]} unread`}>
                  {unread[tab.id] > 9 ? "9+" : unread[tab.id]}
                </span>
              )}
            </button>
          ))}
        </nav>

        <div
          className="world-drawer-body"
          id={`world-page-${worldDrawerTab}`}
          role="tabpanel"
          aria-labelledby={`world-tab-${worldDrawerTab}`}
          tabIndex={0}
        >
          {worldDrawerTab === "rumors" && <RumorsPage rumors={rumors} />}
          {worldDrawerTab === "people" && <PeoplePage people={knownPeople} />}
          {worldDrawerTab === "chronicle" && <ChroniclePage entries={worldChronicle} />}
        </div>

        <footer className="world-drawer-foot">
          <span>Memory is not a map. Rumor is not truth.</span>
          <span><kbd>J</kbd> or <kbd>Esc</kbd> to close</span>
        </footer>
      </aside>
    </div>
  );
}
