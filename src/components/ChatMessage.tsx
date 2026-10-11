import { AGENTS, type AgentId } from "../types/agent";
import type { MessageContent as MessageContentType } from "../types/scenario";
import { MessageContent } from "./MessageContent";

interface UserMessageProps {
  content: MessageContentType;
  /** Who is asking — the Chairman in the main chat, the requesting agent in a session. */
  label?: string;
}

export function UserMessage({ content, label = "회장" }: UserMessageProps) {
  return (
    <div className="msg msg--user">
      <div className="msg__bubble msg__bubble--user">
        <MessageContent content={content} />
      </div>
      <div className="msg__meta">{label}</div>
    </div>
  );
}

interface AssistantMessageProps {
  sender: AgentId;
  title?: string;
  content: MessageContentType;
}

export function AssistantMessage({ sender, title, content }: AssistantMessageProps) {
  const meta = AGENTS[sender];
  return (
    <div className="msg msg--assistant">
      <div className={`msg__avatar msg__avatar--${meta.kind}`} aria-hidden="true">
        {meta.shortName.slice(0, 2).toUpperCase()}
      </div>
      <div className="msg__body">
        <div className="msg__meta">
          <span className="msg__sender">{meta.name}</span>
          {title && <span className="msg__title">{title}</span>}
        </div>
        <div className={`msg__bubble msg__bubble--${meta.kind}`}>
          <MessageContent content={content} />
        </div>
      </div>
    </div>
  );
}
