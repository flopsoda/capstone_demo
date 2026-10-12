/**
 * Agent ids as the UI knows them. Each backend system names its own
 * specialists `meeting` / `news` / `advisory`; the Group system's ones are
 * shown here under `group_*` so both systems' agents fit one graph (the
 * mapper resolves backend `(system, agent)` → AgentId per surface).
 */
export type AgentId =
  | "chairman"
  | "group_super"
  | "group_meeting"
  | "group_news"
  | "group_advisory"
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
  group_meeting: {
    id: "group_meeting",
    name: "그룹 미팅 에이전트",
    shortName: "그룹 미팅",
    role: "그룹 회의 기록",
    kind: "shared",
  },
  group_news: {
    id: "group_news",
    name: "그룹 뉴스 에이전트",
    shortName: "그룹 뉴스",
    role: "그룹 뉴스 · 리스크",
    kind: "shared",
  },
  group_advisory: {
    id: "group_advisory",
    name: "그룹 경영자문 에이전트",
    shortName: "그룹 경영자문",
    role: "그룹 경영 · 전략",
    kind: "shared",
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
  },
  news: {
    id: "news",
    name: "뉴스 에이전트",
    shortName: "뉴스 에이전트",
    role: "뉴스 · 리스크",
    kind: "shared",
  },
  advisory: {
    id: "advisory",
    name: "경영자문 에이전트",
    shortName: "경영자문 에이전트",
    role: "경영 · 전략",
    kind: "shared",
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

  // Internal agent delegation inside the Group
  { id: "group-meeting", from: "group_super", to: "group_meeting", communication: "delegation" },
  { id: "group-news", from: "group_super", to: "group_news", communication: "delegation" },
  { id: "group-advisory", from: "group_super", to: "group_advisory", communication: "delegation" },

  // Super Agent ↔ Super Agent
  { id: "group-k", from: "group_super", to: "k_super", communication: "a2a" },

  // Internal agent delegation inside K Electronics
  { id: "k-meeting", from: "k_super", to: "meeting", communication: "delegation" },
  { id: "k-news", from: "k_super", to: "news", communication: "delegation" },
  { id: "k-advisory", from: "k_super", to: "advisory", communication: "delegation" },
];

export interface GraphPool {
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Which agents a graph shows, where, and the backdrops behind specialist rows. */
export interface GraphUniverse {
  agents: AgentId[];
  positions: Partial<Record<AgentId, { x: number; y: number }>>;
  pools: GraphPool[];
}

/** The K Electronics-centred graph of the original demo (Chairman → Group Super → K Super → K specialists). */
export const DEFAULT_UNIVERSE: GraphUniverse = {
  agents: ["chairman", "group_super", "k_super", "meeting", "news", "advisory"],
  positions: {
    chairman: { x: 314, y: 10 },
    group_super: { x: 314, y: 140 },
    k_super: { x: 314, y: 290 },
    meeting: { x: 40, y: 460 },
    news: { x: 314, y: 460 },
    advisory: { x: 588, y: 460 },
  },
  pools: [{ label: "K전자 · 전문가 에이전트", x: 20, y: 428, width: 800, height: 150 }],
};

/** Both agent systems side by side: the Group (left pool) and K Electronics (right pool). Used by the Demo Monitor. */
export const FULL_UNIVERSE: GraphUniverse = {
  agents: ["chairman", "group_super", "group_meeting", "group_news", "group_advisory", "k_super", "meeting", "news", "advisory"],
  positions: {
    chairman: { x: 588, y: 10 },
    group_super: { x: 588, y: 150 },
    k_super: { x: 862, y: 300 },
    group_meeting: { x: 40, y: 460 },
    group_news: { x: 314, y: 460 },
    group_advisory: { x: 588, y: 460 },
    meeting: { x: 880, y: 460 },
    news: { x: 1154, y: 460 },
    advisory: { x: 1428, y: 460 },
  },
  pools: [
    { label: "그룹 · 전문가 에이전트", x: 20, y: 428, width: 800, height: 150 },
    { label: "K전자 · 전문가 에이전트", x: 860, y: 428, width: 800, height: 150 },
  ],
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
