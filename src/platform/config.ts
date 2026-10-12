/**
 * What makes one platform UI different from the other. Everything else —
 * chat panel, session windows, event mapping, state — is shared mechanism.
 */

import { CHAT_HEADERS, type ChatHeaderInfo } from "../components/chatHeaders";
import type { SidebarTab } from "../components/Sidebar";
import { resolveForFullGraph, type AgentResolver } from "../live/agentEvents";
import {
  ADVISORY_AGENT_RUN_PATH,
  GROUP_ADVISORY_AGENT_RUN_PATH,
  GROUP_MEETING_AGENT_RUN_PATH,
  GROUP_NEWS_AGENT_RUN_PATH,
  GROUP_SUPER_RUN_PATH,
  K_SUPER_DIRECT_RUN_PATH,
  MEETING_AGENT_RUN_PATH,
  NEWS_AGENT_RUN_PATH,
} from "../live/api";
import type { ChatTabId } from "../types/agent";

export interface PlatformTab extends SidebarTab {
  /** Backend run endpoint for direct chat with this agent (NDJSON stream). */
  path: string;
  header: ChatHeaderInfo;
  /** One-click example request shown when the chat is empty. */
  samplePrompt?: string;
}

export interface PlatformConfig {
  id: "group" | "k_electronics";
  title: string;
  subtitle: string;
  badge: string;
  tabs: PlatformTab[];
  /** Maps backend (system, agent) ids to the AgentIds this UI shows. */
  resolveAgent: AgentResolver;
}

export const GROUP_SAMPLE_PROMPT =
  "K전자가 검토 중인 뉴로엣지 인수안을 확인해줘. 최근 리스크와 지난 내부 검토 내용을 반영해서 그룹 투자심의에서 본실사 진입을 승인할지 판단해.";

/**
 * Group platform. Its own specialists appear as group_* so that, inside an
 * A2A session, K Electronics' specialists (relayed events) keep their ids.
 */
export const GROUP_PLATFORM: PlatformConfig = {
  id: "group",
  title: "K그룹 경영진 AI",
  subtitle: "그룹 Agent Platform",
  badge: "Group",
  resolveAgent: resolveForFullGraph,
  tabs: [
    { id: "group_super", label: "그룹 슈퍼에이전트", hint: "슈퍼에이전트", path: GROUP_SUPER_RUN_PATH, header: CHAT_HEADERS.group_super, samplePrompt: GROUP_SAMPLE_PROMPT },
    { id: "group_advisory", label: "경영자문", hint: "전문가 에이전트", path: GROUP_ADVISORY_AGENT_RUN_PATH, header: CHAT_HEADERS.group_advisory },
    { id: "group_news", label: "뉴스", hint: "전문가 에이전트", path: GROUP_NEWS_AGENT_RUN_PATH, header: CHAT_HEADERS.group_news },
    { id: "group_meeting", label: "미팅", hint: "전문가 에이전트", path: GROUP_MEETING_AGENT_RUN_PATH, header: CHAT_HEADERS.group_meeting },
  ],
};

/** K Electronics platform. Backend ids are shown as they are. */
export const K_PLATFORM: PlatformConfig = {
  id: "k_electronics",
  title: "K전자 경영진 AI",
  subtitle: "K전자 Agent Platform",
  badge: "K Electronics",
  resolveAgent: (_system, agent) => agent,
  tabs: [
    { id: "k_super", label: "K전자 슈퍼에이전트", hint: "슈퍼에이전트", path: K_SUPER_DIRECT_RUN_PATH, header: CHAT_HEADERS.k_super, samplePrompt: "뉴로엣지 인수안을 검토해줘. 지난 회의 기준과 최근 외부 리스크를 반영해서 본실사 진입 여부를 판단해." },
    { id: "advisory", label: "경영자문", hint: "전문가 에이전트", path: ADVISORY_AGENT_RUN_PATH, header: CHAT_HEADERS.advisory },
    { id: "news", label: "뉴스", hint: "전문가 에이전트", path: NEWS_AGENT_RUN_PATH, header: CHAT_HEADERS.news },
    { id: "meeting", label: "미팅", hint: "전문가 에이전트", path: MEETING_AGENT_RUN_PATH, header: CHAT_HEADERS.meeting },
  ],
};

export function tabConfig(config: PlatformConfig, tab: ChatTabId): PlatformTab {
  const found = config.tabs.find((t) => t.id === tab);
  if (!found) throw new Error(`tab ${tab} is not part of platform ${config.id}`);
  return found;
}
