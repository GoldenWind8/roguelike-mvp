import { useEffect, useMemo, useState } from "react";
import { useGame, useGameApi } from "../store/gameStore";

function relativeExpiry(value: string | null): string | null {
  if (!value) return null;
  const remaining = new Date(value).getTime() - Date.now();
  if (!Number.isFinite(remaining)) return null;
  const days = Math.max(1, Math.ceil(remaining / 86_400_000));
  return `${days} day${days === 1 ? "" : "s"} left`;
}

function playerNoticeId(value: string): number | null {
  if (!value.startsWith("player:")) return null;
  const parsed = Number(value.slice("player:".length));
  return Number.isInteger(parsed) ? parsed : null;
}

export function NoticeboardModal() {
  const { noticeboard } = useGame();
  const api = useGameApi();
  const [body, setBody] = useState("");

  const ownNotice = useMemo(
    () => noticeboard?.notices.find((notice) => notice.can_delete) ?? null,
    [noticeboard],
  );
  useEffect(() => {
    if (!noticeboard || ownNotice) setBody("");
  }, [noticeboard?.id, ownNotice]);
  if (!noticeboard) return null;

  const playerCount = noticeboard.notices.filter((notice) => notice.kind === "player").length;
  const submit = () => {
    const clean = body.trim();
    if (!clean || ownNotice) return;
    api.postNotice(noticeboard.object_id, clean);
  };

  return (
    <div className="noticeboard-veil" onClick={() => api.closeNoticeboard()}>
      <section className="noticeboard-modal" onClick={(event) => event.stopPropagation()}>
        <header className="noticeboard-head">
          <div>
            <div className="noticeboard-kicker">Town notices</div>
            <h2>{noticeboard.label}</h2>
          </div>
          <button type="button" onClick={() => api.closeNoticeboard()} aria-label="Close noticeboard">
            ×
          </button>
        </header>

        <div className="noticeboard-scroll">
          {noticeboard.notices.map((notice) => {
            const deleteId = playerNoticeId(notice.id);
            return (
              <article key={notice.id} className={`notice-card notice-${notice.kind}`}>
                <p>{notice.body}</p>
                <footer>
                  <span>— {notice.author}</span>
                  {notice.kind === "player" && (
                    <span>{relativeExpiry(notice.expires_at)}</span>
                  )}
                  {notice.can_delete && deleteId !== null && (
                    <button
                      type="button"
                      onClick={() => api.deleteNotice(noticeboard.object_id, deleteId)}
                    >
                      Take down
                    </button>
                  )}
                </footer>
              </article>
            );
          })}
        </div>

        <form
          className="notice-compose"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <label htmlFor="notice-body">
            {ownNotice ? "Your notice is already pinned" : "Leave a notice"}
          </label>
          <textarea
            id="notice-body"
            value={body}
            maxLength={noticeboard.text_limit}
            disabled={Boolean(ownNotice)}
            onChange={(event) => setBody(event.target.value)}
            placeholder={
              ownNotice
                ? "Take down your current notice before posting another."
                : "A short message for other travellers…"
            }
          />
          <div className="notice-compose-foot">
            <span>
              {playerCount}/{noticeboard.max_player_posts} player notices · expires after{" "}
              {noticeboard.post_ttl_days} days
            </span>
            <span>{body.length}/{noticeboard.text_limit}</span>
            <button type="submit" disabled={Boolean(ownNotice) || !body.trim()}>
              Pin notice
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
