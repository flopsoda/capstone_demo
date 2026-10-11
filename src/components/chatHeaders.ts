import type { ChatTabId } from "../types/agent";

export interface ChatHeaderInfo {
  title: string;
  subtitle: string;
  description?: string;
  placeholder: string;
}

export const CHAT_HEADERS: Record<ChatTabId, ChatHeaderInfo> = {
  group_super: {
    title: "그룹 슈퍼에이전트",
    subtitle: "그룹 차원 의사결정 오케스트레이션",
    description:
      "회장의 요청을 분석하고 계열사 슈퍼에이전트(A2A) 및 전문가 에이전트와 협업하여 그룹 차원의 의사결정을 제공합니다.",
    placeholder: "회장의 요청을 입력하세요.",
  },
  advisory: {
    title: "경영자문 에이전트",
    subtitle: "경영 · 전략 분석 · run_mna_review",
    placeholder: "기업 또는 전략 분석 요청을 입력하세요.",
  },
  news: {
    title: "뉴스 에이전트",
    subtitle: "뉴스 · 리스크 분석 · run_company_risk_news_analysis",
    placeholder: "기업 또는 이슈를 입력하세요.",
  },
  meeting: {
    title: "미팅 에이전트",
    subtitle: "회의 기록 · Action Item 추적 · run_meeting_context_retrieval",
    placeholder: "회의 내용 또는 Action Item을 조회하세요.",
  },
};
