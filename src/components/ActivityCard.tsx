import { AGENTS, type AgentId, type CommunicationType } from "../types/agent";
import type { MessageContent as MessageContentType } from "../types/scenario";
import { MessageContent } from "./MessageContent";

interface ActivityCardProps {
  communication: CommunicationType;
  from: AgentId;
  to: AgentId;
  title?: string;
  content: MessageContentType;
}

const DEFAULT_TITLE: Record<CommunicationType, string> = {
  a2a: "A2A Request",
  delegation: "Agent Delegation",
};

/**
 * Internal activity shown inline in the chat.
 * A2A (Super Agent ↔ Super Agent) is visually stronger than an internal
 * Agent Delegation (Super Agent → its own specialist agent).
 */
export function ActivityCard({ communication, from, to, title, content }: ActivityCardProps) {
  const isA2A = communication === "a2a";
  return (
    <div className={`activity activity--${communication}`}>
      <div className="activity__head">
        <span className={`badge ${isA2A ? "badge--a2a" : "badge--task"}`}>
          {title ?? DEFAULT_TITLE[communication]}
        </span>
        <span className="activity__route">
          <span className="activity__agent">{AGENTS[from].shortName}</span>
          <span className="activity__arrow" aria-hidden="true">
            →
          </span>
          <span className="activity__agent">{AGENTS[to].shortName}</span>
        </span>
      </div>
      <div className="activity__body">
        <MessageContent content={content} />
      </div>
    </div>
  );
}
