export type AgentId =
  | "chairman"
  | "group_super"
  | "k_super"
  | "advisory"
  | "news"
  | "meeting";

export type AgentState = "idle" | "active" | "completed" | "error";

export type EdgeState = "idle" | "active" | "completed";

/**
 * a2a        — communication between independent Super Agent systems
 * delegation — a Super Agent handing a natural-language task to one of its
 *              own specialist agents (internal agent delegation)
 */
export type CommunicationType = "a2a" | "delegation";

/** Chat tabs available in the sidebar. K Electronics Super is not directly chat-able. */
export type ChatTabId = "group_super" | "advisory" | "news" | "meeting";

export type AgentKind = "user" | "super" | "shared";

export interface AgentMeta {
  id: AgentId;
  name: string;
  shortName: string;
  role: string;
  kind: AgentKind;
  /** Workflow Tool this specialist agent runs (shown on the graph node). */
  workflow?: string;
}

export const AGENTS: Record<AgentId, AgentMeta> = {
  chairman: {
    id: "chairman",
    name: "그룹 회장",
    shortName: "그룹 회장",
    role: "사용자",
    kind: "user",
  },
  group_super: {
    id: "group_super",
    name: "그룹 슈퍼에이전트",
    shortName: "그룹 슈퍼에이전트",
    role: "슈퍼에이전트 · 그룹",
    kind: "super",
  },
  k_super: {
    id: "k_super",
    name: "K전자 슈퍼에이전트",
    shortName: "K전자 슈퍼에이전트",
    role: "슈퍼에이전트 · 계열사",
    kind: "super",
  },
  meeting: {
    id: "meeting",
    name: "미팅 에이전트",
    shortName: "미팅 에이전트",
    role: "회의 기록",
    kind: "shared",
    workflow: "run_meeting_context_retrieval",
  },
  news: {
    id: "news",
    name: "뉴스 에이전트",
    shortName: "뉴스 에이전트",
    role: "뉴스 · 리스크",
    kind: "shared",
    workflow: "run_company_risk_news_analysis",
  },
  advisory: {
    id: "advisory",
    name: "경영자문 에이전트",
    shortName: "경영자문 에이전트",
    role: "경영 · 전략",
    kind: "shared",
    workflow: "run_mna_review",
  },
};

/**
 * Logical topology. Each entry is a directed-capable link.
 * `from` is always the upper node in the layout so that the
 * "forward" direction is top → bottom and "reverse" is bottom → top.
 */
export interface TopologyEdge {
  id: string;
  from: AgentId;
  to: AgentId;
  communication: CommunicationType;
}

export const TOPOLOGY: TopologyEdge[] = [
  // User request / final report (rendered neutral, no label)
  { id: "chairman-group", from: "chairman", to: "group_super", communication: "delegation" },

  // Super Agent ↔ Super Agent
  { id: "group-k", from: "group_super", to: "k_super", communication: "a2a" },

  // Internal agent delegation inside K Electronics
  { id: "k-meeting", from: "k_super", to: "meeting", communication: "delegation" },
  { id: "k-news", from: "k_super", to: "news", communication: "delegation" },
  { id: "k-advisory", from: "k_super", to: "advisory", communication: "delegation" },
];

/** Fixed layout positions for the pipeline graph. */
export const NODE_POSITIONS: Record<AgentId, { x: number; y: number }> = {
  chairman: { x: 314, y: 10 },
  group_super: { x: 314, y: 140 },
  k_super: { x: 314, y: 290 },
  meeting: { x: 40, y: 460 },
  news: { x: 314, y: 460 },
  advisory: { x: 588, y: 460 },
};

/** Returns the topology edge connecting two agents and the direction of travel. */
export function findTopologyEdge(
  a: AgentId,
  b: AgentId,
): { edge: TopologyEdge; direction: "forward" | "reverse" } | null {
  for (const edge of TOPOLOGY) {
    if (edge.from === a && edge.to === b) return { edge, direction: "forward" };
    if (edge.from === b && edge.to === a) return { edge, direction: "reverse" };
  }
  return null;
}
