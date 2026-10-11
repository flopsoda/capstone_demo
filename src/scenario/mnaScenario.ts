import type { Scenario } from "../types/scenario";
import { MNA_SCENARIO_EVENTS } from "./scenarioEvents";

/** The example request shown to the user as a quick-start hint. */
export const MNA_SAMPLE_PROMPT =
  "K전자가 검토 중인 뉴로엣지 인수안을 확인해줘. 최근 리스크와 지난 내부 검토 내용을 반영해서 그룹 투자심의에서 본실사 진입을 승인할지 판단해.";

const TRIGGER_KEYWORDS = ["neuroedge", "뉴로엣지", "인수"];

export const mnaScenario: Scenario = {
  id: "neuroedge-mna",
  title: "뉴로엣지 M&A 예비 검토",
  totalSteps: 8,
  matches: (text) => {
    const lower = text.toLowerCase();
    return TRIGGER_KEYWORDS.some((k) => lower.includes(k));
  },
  events: MNA_SCENARIO_EVENTS,
};
