import type { ChatTabId } from "../types/agent";
import type { MessageContent } from "../types/scenario";
import { MNA_SAMPLE_PROMPT } from "./mnaScenario";
import { ADVISORY_RESULT, MEETING_CONTEXT, NEWS_RISK_BRIEF } from "./mockResults";

/**
 * Deterministic mock replies for the standalone Advisory / News / Meeting chats.
 * The same specialist agent core answers a direct user request exactly as it
 * answers a delegated one — only the requester differs.
 */
export function getMockResponse(tab: ChatTabId, _input: string): MessageContent {
  switch (tab) {
    case "news":
      return NEWS_RISK_BRIEF;

    case "meeting":
      return MEETING_CONTEXT;

    case "advisory":
      return ADVISORY_RESULT;

    case "group_super":
    default:
      return [
        {
          kind: "text",
          text: "현재 데모에서는 NeuroEdge 인수안 검토 시나리오만 지원합니다.",
        },
        { kind: "text", text: `예: ${MNA_SAMPLE_PROMPT}` },
      ];
  }
}
