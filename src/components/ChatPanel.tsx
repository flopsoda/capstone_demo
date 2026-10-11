import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { ChatTabId } from "../types/agent";
import type { ChatItem, Session } from "../types/scenario";
import { ActivityCard } from "./ActivityCard";
import { AssistantMessage, UserMessage } from "./ChatMessage";
import { ComparisonCard } from "./ComparisonCard";
import { SessionCard } from "./SessionCard";
import { CHAT_HEADERS } from "./chatHeaders";

interface ChatPanelProps {
  tab: ChatTabId;
  items: ChatItem[];
  /** True while a scenario or mock reply is in progress. */
  busy: boolean;
  onSend: (text: string) => void;
  /** Optional one-click example request shown when the chat is empty. */
  samplePrompt?: string;
  /** Sub-sessions referenced by `session` items. */
  sessions?: Record<string, Session>;
  /** Switches the right panel to the Agent Sessions view. */
  onOpenSessions?: () => void;
}

export function ChatPanel({
  tab,
  items,
  busy,
  onSend,
  samplePrompt,
  sessions = {},
  onOpenSessions,
}: ChatPanelProps) {
  const header = CHAT_HEADERS[tab];
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  // Keep the newest message in view as the scenario appends items.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [items.length, busy]);

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    setDraft("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <section className="chat">
      <div className="chat__header">
        <div className="chat__title">{header.title}</div>
        <div className="chat__subtitle">{header.subtitle}</div>
        {header.description && <p className="chat__desc">{header.description}</p>}
      </div>

      <div className="chat__list" ref={listRef}>
        {items.length === 0 && (
          <div className="chat__empty">
            <p>{header.placeholder}</p>
            {samplePrompt && (
              <button
                type="button"
                className="chat__sample"
                onClick={() => setDraft(samplePrompt)}
              >
                예시 요청 불러오기
              </button>
            )}
          </div>
        )}

        {items.map((item) => {
          switch (item.kind) {
            case "user":
              return <UserMessage key={item.id} content={item.content} />;
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
            case "comparison":
              return <ComparisonCard key={item.id} data={item.data} />;
            case "session": {
              const session = sessions[item.sessionId];
              if (!session) return null;
              return (
                <SessionCard
                  key={item.id}
                  session={session}
                  sessions={sessions}
                  onOpenSessions={onOpenSessions}
                />
              );
            }
            default:
              return null;
          }
        })}

        {busy && (
          <div className="chat__typing" aria-live="polite">
            <span /> <span /> <span />
          </div>
        )}
      </div>

      <div className="chat__input">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={header.placeholder}
          rows={2}
          disabled={busy}
        />
        <button
          type="button"
          className="btn btn--primary"
          onClick={submit}
          disabled={busy || draft.trim().length === 0}
        >
          전송
        </button>
      </div>
    </section>
  );
}
