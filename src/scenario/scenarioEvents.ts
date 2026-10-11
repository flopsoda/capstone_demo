import { createAgentEventMapper } from "../live/agentEvents";
import { K_SUPER_RUN_PATH, isAgentId, streamAgentRun } from "../live/api";
import type { AgentId } from "../types/agent";
import type { ScenarioEvent } from "../types/scenario";
import { COMPANY, DUE_DILIGENCE_CONDITIONS, MNA_REVIEW_STAGES, WORKFLOWS } from "./mockData";
import { ADVISORY_RESULT } from "./mockResults";

/* Timing presets (ms). Tuned for a live team-meeting walkthrough. */
const T = {
  hop: 1600, // simple transition
  short: 2200, // brief pause between transitions
  read: 4000, // time to read a returned result before its session collapses
  collapse: 2600, // session collapse animation (COLLAPSE_MS) + settle
  analyze: 3600, // an agent "working"
  stage: 1300, // one internal workflow stage
};

/* A2A request from Group Super to K Electronics Super (Step 2). The same text
 * is shown in the session card and sent to the real K Super in Step 3. */
const A2A_REQUEST_INTRO = `${COMPANY.name} 인수안에 대해\n계열사 차원의 예비 검토를 수행해주세요.`;
const A2A_REQUEST_ITEMS = [
  "과거 경영회의 결정사항",
  "최근 외부 리스크",
  "재무 및 전략적 인수 타당성",
  "본실사 진입 여부",
];
export const A2A_REQUEST_TEXT = `${A2A_REQUEST_INTRO}\n\n다음을 포함해주세요:\n${A2A_REQUEST_ITEMS.map((s) => `- ${s}`).join("\n")}`;

/**
 * Scenario Step shown for each specialist the real K Super delegates to.
 * "Step" is a UI concept only: the backend has no steps, so the step counter
 * advances when the backend reports the corresponding delegation_start.
 */
const LIVE_DELEGATION_STEPS: Partial<Record<AgentId, ScenarioEvent>> = {
  meeting: { type: "step", step: 3, title: "미팅 에이전트 위임" },
  news: { type: "step", step: 4, title: "뉴스 에이전트 위임" },
};

/**
 * Pre-defined 8-step NeuroEdge M&A scenario.
 * The runner replays these in order; the graph and chat derive from them.
 *
 * Steps 3–4 are one `live` event: the real K Electronics Super (LangGraph)
 * delegates to the real Meeting Agent (run_meeting_context_retrieval), reads
 * its answer, then delegates to the real News Agent
 * (run_company_risk_news_analysis). The backend streams its lifecycle and the
 * UI follows it event by event. Every other step is mock playback.
 *
 * Communication model:
 *   그룹 회장 → 그룹 슈퍼에이전트            user request
 *   그룹 슈퍼에이전트 ⇄ K전자 슈퍼에이전트    A2A
 *   K전자 슈퍼에이전트 → 전문가 에이전트      internal agent delegation
 *   전문가 에이전트 → 워크플로 도구           encapsulated (status text only)
 */
export const MNA_SCENARIO_EVENTS: ScenarioEvent[] = [
  /* ------------------------------------------------------------ */
  /* 1단계 — 그룹 회장 → 그룹 슈퍼에이전트                          */
  /* ------------------------------------------------------------ */
  { type: "step", step: 1, title: "회장 요청 접수", delay: 0 },
  {
    type: "agent_start",
    agent: "group_super",
    from: "chairman",
    status: "요청 분석 중...",
    delay: T.analyze,
  },
  { type: "agent_complete", agent: "chairman", delay: 0 },
  { type: "edge", from: "chairman", to: "group_super", state: "completed", delay: 0 },
  {
    type: "message",
    sender: "group_super",
    content:
      "K전자의 내부 검토자료와 최신 리스크 확인이 필요합니다.\nK전자 슈퍼에이전트에 인수안 검토를 요청하겠습니다.",
    delay: T.short,
  },

  /* ------------------------------------------------------------ */
  /* 2단계 — A2A 요청: 그룹 슈퍼에이전트 → K전자 슈퍼에이전트        */
  /* ------------------------------------------------------------ */
  { type: "step", step: 2, title: "A2A 요청 · K전자 슈퍼에이전트", delay: 0 },
  {
    type: "session_open",
    communication: "a2a",
    title: "A2A 요청",
    from: "group_super",
    to: "k_super",
    content: [
      { kind: "text", text: A2A_REQUEST_INTRO },
      { kind: "label", text: "다음을 포함해주세요" },
      { kind: "list", items: A2A_REQUEST_ITEMS },
    ],
    delay: T.hop,
  },
  {
    type: "agent_start",
    agent: "k_super",
    from: "group_super",
    communication: "a2a",
    status: "검토 계획 수립 중...",
    delay: 0,
  },
  {
    type: "agent_status",
    agent: "group_super",
    status: "K전자 응답 대기 중",
    delay: T.short,
  },

  /* ------------------------------------------------------------ */
  /* 3–4단계 — 위임: K전자 슈퍼에이전트 → 미팅 에이전트 → 뉴스 에이전트 */
  /* ------------------------------------------------------------ */
  { type: "step", step: 3, title: "미팅 에이전트 위임", delay: 0 },
  {
    // REAL: K전자 슈퍼에이전트(LangGraph)가 A2A 요청을 받아 delegate_to_meeting_agent
    // tool을 호출하고, 미팅 에이전트(LangGraph)가 run_meeting_context_retrieval을
    // 실행한다. 그 결과를 받은 K전자 슈퍼에이전트가 다시 판단해
    // delegate_to_news_agent를 호출하고, 뉴스 에이전트(LangGraph)가
    // run_company_risk_news_analysis를 실행한다. backend가 실행 중 보내는
    // lifecycle 이벤트가 도착하는 즉시 세션/노드/메시지 상태로 반영된다
    // (src/live/agentEvents.ts). Step 번호는 실제 delegation_start에 맞춰 바뀐다.
    type: "live",
    agent: "k_super",
    run: async ({ emit }) => {
      const mapEvent = createAgentEventMapper();
      await streamAgentRun(
        K_SUPER_RUN_PATH,
        A2A_REQUEST_TEXT,
        (event) => {
          if (event.type === "delegation_start" && isAgentId(event.to)) {
            const step = LIVE_DELEGATION_STEPS[event.to];
            if (step) emit(step);
          }
          mapEvent(event).forEach(emit);
        },
        "k_super",
      );
    },
    delay: T.hop,
  },

  /* ------------------------------------------------------------ */
  /* 5단계 — 위임: K전자 슈퍼에이전트 → 경영자문 에이전트            */
  /* ------------------------------------------------------------ */
  { type: "step", step: 5, title: "경영자문 위임 · run_mna_review", delay: 0 },
  {
    type: "session_open",
    communication: "delegation",
    from: "k_super",
    to: "advisory",
    content: `"${COMPANY.name} 인수안의\n본실사 진입 여부를 검토해줘.\n\n지난 전략회의의 판단 기준과\n최근 뉴스 리스크를 반영하고,\n\n재무 타당성,\n전략적 가치,\n주요 리스크,\n본실사 진입 조건을\n종합적으로 평가해."`,
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
    title: "M&A 예비 검토",
    content: ADVISORY_RESULT,
    delay: T.read,
  },
  { type: "agent_complete", agent: "advisory", from: "k_super", delay: 0 },
  { type: "session_close", delay: T.collapse },

  /* ------------------------------------------------------------ */
  /* 6단계 — K전자 슈퍼에이전트가 세 결과를 종합                      */
  /* ------------------------------------------------------------ */
  { type: "step", step: 6, title: "K전자 종합 결과", delay: 0 },
  {
    type: "agent_status",
    agent: "k_super",
    status: "결과 종합 중...",
    emphasized: true,
    delay: T.short,
  },
  {
    type: "message",
    sender: "k_super",
    title: "K전자 M&A 검토 결과",
    content: [
      { kind: "label", text: "권고" },
      { kind: "badge", text: "조건부 진행", tone: "info" },
      { kind: "label", text: "근거" },
      {
        kind: "list",
        items: [
          `현재 요구가격 ${COMPANY.askingValuation}은 내부 적정가치 ${COMPANY.estimatedFairValue}보다 높음`,
          "반면 인수를 통해 자체 개발 대비 약 2 ~ 3년 빠르게 AI NPU 기술 확보 가능",
          "전략적 기술 가치는 높음",
          "고객 집중도, 핵심인력 이탈, 특허소송 리스크 확인 필요",
        ],
      },
      { kind: "label", text: "본실사 조건" },
      { kind: "list", ordered: true, items: [...DUE_DILIGENCE_CONDITIONS] },
    ],
    delay: T.short,
  },

  /* ------------------------------------------------------------ */
  /* 7단계 — A2A 응답: K전자 슈퍼에이전트 → 그룹 슈퍼에이전트        */
  /* ------------------------------------------------------------ */
  { type: "step", step: 7, title: "A2A 응답 · 그룹 슈퍼에이전트", delay: 0 },
  { type: "agent_status", agent: "k_super", status: "결과 반환 중", delay: 0 },
  { type: "edge", from: "k_super", to: "group_super", state: "active", delay: 0 },
  {
    type: "agent_status",
    agent: "group_super",
    status: "A2A 응답 수신 중",
    emphasized: true,
    delay: 0,
  },
  // Closing the A2A session hands the response back to Group Super; the
  // session card in the main transcript shows it as the result.
  {
    type: "session_close",
    resultTitle: "A2A 응답",
    result: [
      { kind: "label", text: "권고" },
      { kind: "badge", text: "조건부 진행", tone: "info" },
      {
        kind: "text",
        text: "위 4개 핵심 조건을\n본실사 단계에서 검증하는 것을 전제로\n본실사 진입을 권고합니다.",
      },
    ],
    delay: T.analyze,
  },
  { type: "edge", from: "k_super", to: "group_super", state: "completed", delay: 0 },
  { type: "agent_complete", agent: "k_super", delay: 0 },
  {
    type: "agent_status",
    agent: "group_super",
    status: "최종 보고 준비 중",
    emphasized: true,
    delay: T.hop,
  },

  /* ------------------------------------------------------------ */
  /* 8단계 — 그룹 최종 판단: 그룹 슈퍼에이전트 → 그룹 회장           */
  /* ------------------------------------------------------------ */
  { type: "step", step: 8, title: "그룹 최종 판단", delay: 0 },
  { type: "edge", from: "group_super", to: "chairman", state: "active", delay: 0 },
  { type: "agent_start", agent: "chairman", status: "보고 수신 중", delay: 0 },
  {
    type: "message",
    sender: "group_super",
    title: "그룹 투자 심의 의견",
    content: [
      {
        kind: "text",
        text: `K전자 측 검토 결과,\n${COMPANY.name}는 현재 요구가격 기준으로는\n재무적 부담이 존재합니다.`,
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
          "창업자 CTO 및 핵심 설계팀 3년 근속 조건 확보",
          "특허소송 잠재손실 검증",
          "알파모바일 계약 유지 여부 검증",
        ],
      },
      { kind: "label", text: "최종 권고" },
      { kind: "badge", text: "조건부 승인", tone: "ok" },
    ],
    delay: T.analyze,
  },
  { type: "scenario_complete", delay: 0 },
];
