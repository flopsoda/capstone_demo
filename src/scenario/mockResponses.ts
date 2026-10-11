import type { MessageContent } from "../types/scenario";
import { MNA_SAMPLE_PROMPT } from "./mnaScenario";

/**
 * Fixed reply of the (mock) Group Super tab to input that does not trigger
 * the scenario. The specialist tabs are answered by the real agents (App.tsx).
 */
export function getGroupSuperFallback(): MessageContent {
  return [
    { kind: "text", text: "현재 데모에서는 뉴로엣지 인수안 검토 시나리오만 지원합니다." },
    { kind: "text", text: `예: ${MNA_SAMPLE_PROMPT}` },
  ];
}
