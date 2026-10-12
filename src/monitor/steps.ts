/**
 * Demo Step interpretation — a presentation concept only. The backends know
 * nothing about Steps; the monitor reads the real lifecycle and labels it:
 *
 *   1 Group 요청 접수          agent_start(group_super)            Group run
 *   2 Group → K전자 A2A 요청   a2a_start(group_super → k_super)    Group run
 *   3 K전자 → Meeting          delegation_start(k_super → meeting) K run (relayed)
 *   4 K전자 → News             delegation_start(k_super → news)
 *   5 K전자 → Advisory         delegation_start(k_super → advisory)
 *   6 K전자 종합               agent_end(k_super)
 *   7 K전자 → Group A2A 응답   a2a_end                             Group run
 *   8 Group 최종 판단          agent_end(group_super)              Group run
 *
 * Which Steps appear, and in what order, is whatever the agents actually did.
 */

import type { AgentEvent } from "../live/api";
import type { ScenarioEvent } from "../types/scenario";

export const DEMO_TOTAL_STEPS = 8;

const K_DELEGATION_STEPS: Record<string, { step: number; title: string }> = {
  meeting: { step: 3, title: "K전자 → 미팅 에이전트 위임" },
  news: { step: 4, title: "K전자 → 뉴스 에이전트 위임" },
  advisory: { step: 5, title: "K전자 → 경영자문 에이전트 위임" },
};

export function stepFor(ev: AgentEvent): ScenarioEvent | null {
  const fromK = ev.system === "k_electronics";
  switch (ev.type) {
    case "agent_start":
      return !ev.relayed && ev.agent === "group_super" ? { type: "step", step: 1, title: "그룹 요청 접수" } : null;
    case "a2a_start":
      return { type: "step", step: 2, title: "그룹 → K전자 A2A 요청" };
    case "delegation_start": {
      const s = fromK ? K_DELEGATION_STEPS[ev.to] : undefined;
      return s ? { type: "step", ...s } : null;
    }
    case "agent_end":
      if (fromK && ev.agent === "k_super") return { type: "step", step: 6, title: "K전자 종합 결과" };
      if (!ev.relayed && ev.agent === "group_super") return { type: "step", step: 8, title: "그룹 최종 판단" };
      return null;
    case "a2a_end":
      return { type: "step", step: 7, title: "K전자 → 그룹 A2A 응답" };
    default:
      return null;
  }
}
