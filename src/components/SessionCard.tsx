import { useState } from "react";
import { AGENTS, type CommunicationType } from "../types/agent";
import type { ChatItem, Session } from "../types/scenario";
import { ActivityCard } from "./ActivityCard";
import { AssistantMessage, UserMessage } from "./ChatMessage";
import { MessageContent } from "./MessageContent";

const DEFAULT_TITLE: Record<CommunicationType, string> = {
  a2a: "A2A 요청",
  delegation: "에이전트 위임",
};

/* ------------------------------------------------------------------ */
/* Transcript of a session (recursive for nested sessions)             */
/* ------------------------------------------------------------------ */

interface TranscriptProps {
  session: Session;
  sessions: Record<string, Session>;
  onOpenSessions?: () => void;
}

/** Requester bubble followed by everything the responding agent produced. */
export function SessionTranscript({ session, sessions, onOpenSessions }: TranscriptProps) {
  const from = AGENTS[session.from];
  return (
    <>
      <UserMessage content={session.request} label={from.shortName} />
      {session.items.map((item) => {
        switch (item.kind) {
          case "assistant":
            return (
              <AssistantMessage
                key={item.id}
                sender={item.sender}
                title={item.title}
                content={item.content}
              />
            );
          case "activity":
            return (
              <ActivityCard
                key={item.id}
                communication={item.communication}
                from={item.from}
                to={item.to}
                title={item.title}
                content={item.content}
              />
            );
          case "session":
            return (
              <SessionCard
                key={item.id}
                session={sessions[item.sessionId]}
                sessions={sessions}
                onOpenSessions={onOpenSessions}
              />
            );
          default:
            return null;
        }
      })}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Session card                                                        */
/* ------------------------------------------------------------------ */

interface SessionCardProps {
  session: Session;
  sessions: Record<string, Session>;
  onOpenSessions?: () => void;
}

/**
 * The request bubble that opened an A2A / delegation session.
 * While the session is open the conversation happens in its own chat
 * window; this card only says so. When the session closes, its window
 * flies into this card, which then shows the returned result and can be
 * expanded to re-read the whole conversation.
 */
export function SessionCard({ session, sessions, onOpenSessions }: SessionCardProps) {
  const [expanded, setExpanded] = useState(false);
  const isOpen = session.status === "open";
  const isA2A = session.communication === "a2a";
  const from = AGENTS[session.from];
  const to = AGENTS[session.to];

  const lastAssistant = [...session.items]
    .reverse()
    .find((i): i is Extract<ChatItem, { kind: "assistant" }> => i.kind === "assistant");
  const resultTitle = session.resultTitle ?? lastAssistant?.title ?? "완료";

  return (
    <div
      className={`session-card session-card--${session.communication} session-card--${session.status}`}
      data-session-target={session.id}
    >
      <div className="session-card__head">
        <span className={`badge ${isA2A ? "badge--a2a" : "badge--task"}`}>
          {session.title ?? DEFAULT_TITLE[session.communication]}
        </span>
        <span className="session-card__route">
          <span>{from.shortName}</span>
          <span className="activity__arrow" aria-hidden="true">
            →
          </span>
          <span>{to.shortName}</span>
        </span>
        <span className="session-card__state">
          {isOpen ? (
            <>
              <span className="session-card__live-dot" aria-hidden="true" />
              대화 진행 중
            </>
          ) : (
            <>
              <span className="session-card__done" aria-hidden="true">
                ✓
              </span>
              결과 반환
            </>
          )}
        </span>
      </div>

      <div className="session-card__request">
        <MessageContent content={session.request} />
      </div>

      {isOpen && (
        <div className="session-card__hint">
          <span>{to.shortName}와(과) 별도 채팅창에서 대화 중입니다.</span>
          {onOpenSessions && (
            <button type="button" className="session-card__link" onClick={onOpenSessions}>
              협업 대화 보기 →
            </button>
          )}
        </div>
      )}

      {!isOpen && (
        <div className="session-card__result">
          <div className="session-card__result-head">
            <span className="msg-label">{resultTitle}</span>
            {session.items.length > 0 && (
              <button
                type="button"
                className="session-card__link"
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded ? "대화 접기" : "대화 보기"}
              </button>
            )}
          </div>
          {session.result ? (
            <MessageContent content={session.result} />
          ) : (
            lastAssistant &&
            !expanded && (
              <div className="session-card__preview">
                <MessageContent content={lastAssistant.content} />
              </div>
            )
          )}
          {expanded && (
            <div className={`session-replay session-replay--${session.communication}`}>
              <div className="session-replay__owner">
                {to.name}
                {to.workflow && <code className="session__workflow">{to.workflow}</code>}
              </div>
              <div className="session-replay__items">
                <SessionTranscript session={session} sessions={sessions} />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
