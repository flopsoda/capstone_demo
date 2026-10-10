import type { ChatTabId } from "../types/agent";

export interface ChatHeaderInfo {
  title: string;
  subtitle: string;
  description?: string;
  placeholder: string;
}

export const CHAT_HEADERS: Record<ChatTabId, ChatHeaderInfo> = {
  group_super: {
    title: "Group Super Agent",
    subtitle: "Group-level decision orchestration",
    description:
      "회장의 요청을 분석하고 계열사 Super Agent(A2A) 및 전문 Agent와 협업하여 그룹 차원의 의사결정을 제공합니다.",
    placeholder: "회장의 요청을 입력하세요.",
  },
  advisory: {
    title: "Management Advisory Agent",
    subtitle: "Business & Strategy Analysis · run_mna_review",
    placeholder: "기업 또는 전략 분석 요청을 입력하세요.",
  },
  news: {
    title: "News Intelligence Agent",
    subtitle: "News & Risk Intelligence · run_company_risk_news_analysis",
    placeholder: "기업 또는 이슈를 입력하세요.",
  },
  meeting: {
    title: "Meeting Support Agent",
    subtitle: "Meeting Memory & Action Tracking · run_meeting_context_retrieval",
    placeholder: "회의 내용 또는 Action Item을 조회하세요.",
  },
};
