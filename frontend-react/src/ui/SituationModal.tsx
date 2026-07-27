import { useEffect, useRef } from "react";

import { useGame, useGameApi } from "../store/gameStore";


export function SituationModal() {
  const { situation, situationPending } = useGame();
  const api = useGameApi();
  const modalRef = useRef<HTMLElement>(null);
  const firstChoiceRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!situation) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    return () => {
      returnFocusRef.current?.focus();
      returnFocusRef.current = null;
    };
  }, [situation?.id]);

  useEffect(() => {
    if (!situation) return;
    const frame = window.requestAnimationFrame(() => {
      (firstChoiceRef.current ?? closeRef.current)?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [situation?.id, situation?.resolved, situation?.choices.length]);

  if (!situation) return null;

  return (
    <div
      className="situation-veil"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target && !situationPending) {
          api.closeSituation();
        }
      }}
    >
      <section
        ref={modalRef}
        className="situation-modal"
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="situation-title"
        aria-describedby="situation-description"
        aria-busy={situationPending}
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
        <header className="situation-head">
          <div className="situation-sigil" aria-hidden>
            <span />
            <i>Ⅲ</i>
          </div>
          <div>
            <span className="situation-kicker">{situation.kicker}</span>
            <h2 id="situation-title">{situation.title}</h2>
          </div>
          <button
            ref={closeRef}
            className="situation-close"
            onClick={() => api.closeSituation()}
            disabled={situationPending}
            aria-label="Step away"
            title="Step away (Esc)"
          >
            ×
          </button>
        </header>

        <p id="situation-description" className="situation-description">
          {situation.description}
        </p>

        {situation.resolved ? (
          <div className="situation-result" role="status" aria-live="polite">
            <span aria-hidden>◇</span>
            <p>{situation.result}</p>
          </div>
        ) : situation.choices.length > 0 ? (
          <div className="situation-choices">
            {situation.choices.map((choice, index) => (
              <button
                key={choice.id}
                ref={index === 0 ? firstChoiceRef : undefined}
                disabled={situationPending}
                onClick={() => api.resolveSituation(choice.id)}
              >
                <span>{choice.label}</span>
                <small>{choice.description}</small>
              </button>
            ))}
          </div>
        ) : (
          <p className="situation-silent">
            The mechanism answers only with the cadence it already knows.
          </p>
        )}

        <footer className="situation-foot">
          {situationPending
            ? "The chain takes up your answer…"
            : "Step away, or let the answer become part of the world."}
        </footer>
      </section>
    </div>
  );
}
