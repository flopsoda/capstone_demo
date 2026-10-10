import type { ScenarioEvent } from "../types/scenario";
import { COMPANY, DUE_DILIGENCE_CONDITIONS, MNA_REVIEW_STAGES, WORKFLOWS } from "./mockData";
import { ADVISORY_RESULT, MEETING_CONTEXT, NEWS_RISK_ANALYSIS } from "./mockResults";

/* Timing presets (ms). Tuned for a live team-meeting walkthrough. */
const T = {
  hop: 1000, // simple transition
  short: 1400, // brief pause between transitions
  read: 2400, // time to read a returned result before its session collapses
  collapse: 1900, // session collapse animation (COLLAPSE_MS) + settle
  analyze: 2400, // an agent "working"
  stage: 800, // one internal workflow stage
};

/**
 * Pre-defined 8-step NeuroEdge M&A scenario.
 * The runner replays these in order; the graph and chat derive from them.
 *
 * Communication model:
 *   Group Chairman → Group Super            user request
 *   Group Super   ⇄ K Electronics Super     A2A
 *   K Electronics Super → specialist agent  internal agent delegation
 *   specialist agent → Workflow Tool        encapsulated (status text only)
 */
export const MNA_SCENARIO_EVENTS: ScenarioEvent[] = [
  /* ------------------------------------------------------------ */
  /* Step 1 — Group Chairman → Group Super                          */
  /* ------------------------------------------------------------ */
  { type: "step", step: 1, title: "회장 요청 접수", delay: 0 },
  {
    type: "agent_start",
    agent: "group_super",
    from: "chairman",
    status: "Analyzing request...",
    delay: T.analyze,
  },
  { type: "agent_complete", agent: "chairman", delay: 0 },
  { type: "edge", from: "chairman", to: "group_super", state: "completed", delay: 0 },
  {
    type: "message",
    sender: "group_super",
    content:
      "K전자의 내부 검토자료와 최신 리스크 확인이 필요합니다.\nK Electronics Super Agent에 인수안 검토를 요청하겠습니다.",
    delay: T.short,
  },

  /* ------------------------------------------------------------ */
  /* Step 2 — A2A Request: Group Super → K Electronics Super        */
  /* ------------------------------------------------------------ */
  { type: "step", step: 2, title: "A2A 요청 · K Electronics Super", delay: 0 },
  {
    type: "session_open",
    communication: "a2a",
    title: "A2A Request",
    from: "group_super",
    to: "k_super",
    content: [
      {
        kind: "text",
        text: "NeuroEdge 인수안에 대해\n계열사 차원의 예비 검토를 수행해주세요.",
      },
      { kind: "label", text: "다음을 포함해주세요" },
      {
        kind: "list",
        items: [
          "과거 경영회의 결정사항",
          "최근 외부 리스크",
          "재무 및 전략적 인수 타당성",
          "본실사 진입 여부",
        ],
      },
    ],
    delay: T.hop,
  },
  {
    type: "agent_start",
    agent: "k_super",
    from: "group_super",
    communication: "a2a",
    status: "Planning review...",
    delay: 0,
  },
  {
    type: "agent_status",
    agent: "group_super",
    status: "Waiting for K Electronics",
    delay: T.short,
  },

  /* ------------------------------------------------------------ */
  /* Step 3 — Delegation: K Electronics Super → Meeting Agent       */
  /* ------------------------------------------------------------ */
  { type: "step", step: 3, title: "Meeting Agent 위임", delay: 0 },
  {
    type: "session_open",
    communication: "delegation",
    from: "k_super",
    to: "meeting",
    content:
      '"NeuroEdge 인수와 관련해\n지난 전략회의에서 결정한 기준과\n아직 확인되지 않은 Action Item을 찾아줘."',
    delay: T.hop,
  },
  {
    type: "agent_start",
    agent: "meeting",
    from: "k_super",
    communication: "delegation",
    workflow: WORKFLOWS.meeting,
    status: "Retrieving context...",
    delay: T.analyze,
  },
  {
    type: "message",
    sender: "meeting",
    title: "Meeting Context",
    content: MEETING_CONTEXT,
    delay: T.read,
  },
  { type: "agent_complete", agent: "meeting", from: "k_super", delay: 0 },
  { type: "session_close", delay: T.collapse },

  /* ------------------------------------------------------------ */
  /* Step 4 — Delegation: K Electronics Super → News Agent          */
  /* ------------------------------------------------------------ */
  { type: "step", step: 4, title: "News Agent 위임", delay: 0 },
  {
    type: "session_open",
    communication: "delegation",
    from: "k_super",
    to: "news",
    content:
      '"NeuroEdge 인수 판단에 영향을 줄 수 있는\n최근 외부 리스크를 조사해줘.\n\n주요 고객, 법적 분쟁,\n핵심인력 관련 이슈를 우선 확인해."',
    delay: T.hop,
  },
  {
    type: "agent_start",
    agent: "news",
    from: "k_super",
    communication: "delegation",
    workflow: WORKFLOWS.news,
    status: "Analyzing...",
    delay: T.analyze,
  },
  {
    type: "message",
    sender: "news",
    title: "News Risk Analysis",
    content: NEWS_RISK_ANALYSIS,
    delay: T.read,
  },
  { type: "agent_complete", agent: "news", from: "k_super", delay: 0 },
  { type: "session_close", delay: T.collapse },

  /* ------------------------------------------------------------ */
  /* Step 5 — Delegation: K Electronics Super → Management Advisory */
  /* ------------------------------------------------------------ */
  { type: "step", step: 5, title: "Management Advisory 위임 · run_mna_review", delay: 0 },
  {
    type: "session_open",
    communication: "delegation",
    from: "k_super",
    to: "advisory",
    content:
      '"NeuroEdge 인수안의\n본실사 진입 여부를 검토해줘.\n\n지난 전략회의의 판단 기준과\n최근 뉴스 리스크를 반영하고,\n\n재무 타당성,\n전략적 가치,\n주요 리스크,\n본실사 진입 조건을\n종합적으로 평가해."',
    delay: T.hop,
  },
  {
    type: "agent_start",
    agent: "advisory",
    from: "k_super",
    communication: "delegation",
    workflow: WORKFLOWS.advisory,
    status: MNA_REVIEW_STAGES[0],
    delay: T.stage,
  },
  // Internal stages of run_mna_review — encapsulated in the Workflow Tool,
  // surfaced only as status text so the team can see the pipeline shape.
  ...MNA_REVIEW_STAGES.slice(1).map(
    (stage): ScenarioEvent => ({
      type: "agent_status",
      agent: "advisory",
      status: stage,
      delay: T.stage,
    }),
  ),
  {
    type: "message",
    sender: "advisory",
    title: "M&A Preliminary Review",
    content: ADVISORY_RESULT,
    delay: T.read,
  },
  { type: "agent_complete", agent: "advisory", from: "k_super", delay: 0 },
  { type: "session_close", delay: T.collapse },

  /* ------------------------------------------------------------ */
  /* Step 6 — K Electronics Super synthesizes the three results     */
  /* ------------------------------------------------------------ */
  { type: "step", step: 6, title: "K Electronics 종합 결과", delay: 0 },
  {
    type: "agent_status",
    agent: "k_super",
    status: "Synthesizing results...",
    emphasized: true,
    delay: T.short,
  },
  {
    type: "message",
    sender: "k_super",
    title: "K Electronics M&A Review Result",
    content: [
      { kind: "label", text: "Recommendation" },
      { kind: "badge", text: "CONDITIONAL PROCEED", tone: "info" },
      { kind: "label", text: "Rationale" },
      {
        kind: "list",
        items: [
          `현재 요구가격 ${COMPANY.askingValuation}은 내부 적정가치 ${COMPANY.estimatedFairValue}보다 높음`,
          "반면 인수를 통해 자체 개발 대비 약 2 ~ 3년 빠르게 AI NPU 기술 확보 가능",
          "전략적 기술 가치는 높음",
          "고객 집중도, 핵심인력 Retention, 특허소송 Risk 확인 필요",
        ],
      },
      { kind: "label", text: "Due Diligence Conditions" },
      { kind: "list", ordered: true, items: [...DUE_DILIGENCE_CONDITIONS] },
    ],
    delay: T.short,
  },

  /* ------------------------------------------------------------ */
  /* Step 7 — A2A Response: K Electronics Super → Group Super       */
  /* ------------------------------------------------------------ */
  { type: "step", step: 7, title: "A2A 응답 · Group Super", delay: 0 },
  { type: "agent_status", agent: "k_super", status: "Returning result", delay: 0 },
  { type: "edge", from: "k_super", to: "group_super", state: "active", delay: 0 },
  {
    type: "agent_status",
    agent: "group_super",
    status: "Receiving A2A response",
    emphasized: true,
    delay: 0,
  },
  // Closing the A2A session hands the response back to Group Super; the
  // session card in the main transcript shows it as the result.
  {
    type: "session_close",
    resultTitle: "A2A Response",
    result: [
      { kind: "label", text: "Recommendation" },
      { kind: "badge", text: "CONDITIONAL_PROCEED", tone: "info" },
      {
        kind: "text",
        text: "위 4개 핵심 조건을\nDue Diligence 단계에서 검증하는 것을 전제로\n본실사 진입을 권고합니다.",
      },
    ],
    delay: T.analyze,
  },
  { type: "edge", from: "k_super", to: "group_super", state: "completed", delay: 0 },
  { type: "agent_complete", agent: "k_super", delay: 0 },
  {
    type: "agent_status",
    agent: "group_super",
    status: "Preparing final report",
    emphasized: true,
    delay: T.hop,
  },

  /* ------------------------------------------------------------ */
  /* Step 8 — Final Group Decision: Group Super → Group Chairman    */
  /* ------------------------------------------------------------ */
  { type: "step", step: 8, title: "그룹 최종 판단", delay: 0 },
  { type: "edge", from: "group_super", to: "chairman", state: "active", delay: 0 },
  { type: "agent_start", agent: "chairman", status: "Receiving report", delay: 0 },
  {
    type: "message",
    sender: "group_super",
    title: "Group Investment Review",
    content: [
      {
        kind: "text",
        text: "K전자 측 검토 결과,\nNeuroEdge는 현재 요구가격 기준으로는\n재무적 부담이 존재합니다.",
      },
      {
        kind: "text",
        text: "다만 인수를 통해\nAI NPU 기술 확보 기간을\n약 2~3년 단축할 수 있어\n전략적 가치는 높습니다.",
      },
      {
        kind: "text",
        text: "따라서 다음 조건을 전제로\n본실사 진입을 승인하는 것을 권고합니다.",
      },
      {
        kind: "list",
        ordered: true,
        items: [
          "인수 협상 기준가격 2.3조 원 이하",
          "창업자 CTO 및 핵심 설계팀 3년 Retention",
          "특허소송 잠재손실 검증",
          "AlphaMobile 계약 유지 여부 검증",
        ],
      },
      { kind: "label", text: "Final Recommendation" },
      { kind: "badge", text: "CONDITIONAL APPROVAL", tone: "ok" },
    ],
    delay: T.analyze,
  },
  { type: "scenario_complete", delay: 0 },
];
