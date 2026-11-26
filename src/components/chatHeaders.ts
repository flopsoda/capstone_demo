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
      "회장의 요청을 분석하고, 그룹 전문가 에이전트에게 위임하거나 계열사 슈퍼에이전트(A2A)와 협업하여 그룹 차원의 판단을 제공합니다.",
    placeholder: "회장의 요청을 입력하세요.",
  },
  group_advisory: {
    title: "경영자문 에이전트",
    subtitle: "그룹 경영 · 전략 분석 · run_mna_review",
    placeholder: "그룹 차원의 인수·투자 또는 전략 분석 요청을 입력하세요.",
  },
  group_news: {
    title: "뉴스 에이전트",
    subtitle: "그룹 · 계열사 뉴스 브리핑 · run_news_briefing",
    placeholder: "기업 또는 이슈를 입력하세요.",
  },
  group_meeting: {
    title: "미팅 에이전트",
    subtitle: "그룹 회의 기록 · Action Item 추적 · run_meeting_context_retrieval",
    placeholder: "그룹 회의 내용 또는 Action Item을 조회하세요.",
  },
  k_super: {
    title: "K전자 슈퍼에이전트",
    subtitle: "K전자 차원 의사결정 오케스트레이션",
    description: "요청을 분석하고 K전자 전문가 에이전트(미팅 · 뉴스 · 경영자문)에게 위임하여 K전자 차원의 검토 결과를 제공합니다.",
    placeholder: "K전자 경영진의 요청을 입력하세요.",
  },
  advisory: {
    title: "경영자문 에이전트",
    subtitle: "경영 · 전략 분석 · run_mna_review",
    placeholder: "기업 또는 전략 분석 요청을 입력하세요.",
  },
  news: {
    title: "뉴스 에이전트",
    subtitle: "뉴스 브리핑 · 기업 리스크 · run_news_briefing",
    placeholder: "기업 또는 이슈를 입력하세요.",
  },
  meeting: {
    title: "미팅 에이전트",
    subtitle: "회의 기록 · Action Item 추적 · run_meeting_context_retrieval",
    placeholder: "회의 내용 또는 Action Item을 조회하세요.",
  },
};
