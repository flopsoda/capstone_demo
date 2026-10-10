import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { COLLAPSE_MS, useCollapsePhase } from "../hooks/useCollapsePhase";
import { AGENTS } from "../types/agent";
import type { Session } from "../types/scenario";
import { SessionTranscript } from "./SessionCard";

interface SessionWindowProps {
  session: Session;
  sessions: Record<string, Session>;
}

interface Ghost {
  from: DOMRect;
  to: DOMRect;
  flying: boolean;
}

/**
 * A separate chat window for one A2A / delegation session.
 * Mounted while the session is open; on close it shrinks out of the row
 * while a ghost flies into the request card that spawned it, then unmounts.
 */
export function SessionWindow({ session, sessions }: SessionWindowProps) {
  const phase = useCollapsePhase(session.status === "open");
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [ghost, setGhost] = useState<Ghost | null>(null);

  const from = AGENTS[session.from];
  const to = AGENTS[session.to];
  const isA2A = session.communication === "a2a";

  // Follow the newest message as the transcript grows.
  useEffect(() => {
    const list = listRef.current;
    const content = contentRef.current;
    if (!list || !content) return;
    const follow = () => list.scrollTo({ top: list.scrollHeight, behavior: "smooth" });
    const ro = new ResizeObserver(follow);
    ro.observe(content);
    return () => ro.disconnect();
  }, []);

  // On close: measure this window and the request card in the parent window,
  // then fly a ghost between them.
  useLayoutEffect(() => {
    if (phase !== "collapsing") return;
    const source = rootRef.current;
    const target = document.querySelector(`[data-session-target="${session.id}"]`);
    if (!source || !target) return;
    const g: Ghost = {
      from: source.getBoundingClientRect(),
      to: target.getBoundingClientRect(),
      flying: false,
    };
    setGhost(g);
    const raf = window.requestAnimationFrame(() => setGhost({ ...g, flying: true }));
    const t = window.setTimeout(() => setGhost(null), COLLAPSE_MS + 100);
    return () => {
      window.cancelAnimationFrame(raf);
      window.clearTimeout(t);
    };
  }, [phase, session.id]);

  if (phase === "closed") return null;

  const rect = ghost ? (ghost.flying ? ghost.to : ghost.from) : null;

  return (
    <>
      <div
        ref={rootRef}
        className={`panel panel--session panel--session-${session.communication} panel--session-${phase}`}
        data-session-source={session.id}
      >
        <div className="chat__header session-window__header">
          <div className="session-window__title-row">
            <span className={`badge ${isA2A ? "badge--a2a" : "badge--task"}`}>
              {isA2A ? "A2A Session" : "Delegation Session"}
            </span>
            <span className="session-window__live">
              <span className="session-card__live-dot" aria-hidden="true" />
              live
            </span>
          </div>
          <div className="chat__title">{to.name}</div>
          <div className="chat__subtitle">
            {from.shortName} → {to.shortName}
            {to.workflow && (
              <>
                {" · "}
                <code className="session__workflow">{to.workflow}</code>
              </>
            )}
          </div>
        </div>

        <div className="chat__list" ref={listRef}>
          <div className="session-window__content" ref={contentRef}>
            <SessionTranscript session={session} sessions={sessions} />
            {session.items.length === 0 && (
              <div className="chat__typing" aria-live="polite">
                <span /> <span /> <span />
              </div>
            )}
          </div>
        </div>
      </div>

      {rect &&
        createPortal(
          <div
            className={`session-ghost session-ghost--${session.communication}${
              ghost?.flying ? " session-ghost--flying" : ""
            }`}
            style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
            aria-hidden="true"
          />,
          document.body,
        )}
    </>
  );
}
