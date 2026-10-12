import { useState } from "react";
import { AgentGraph } from "../components/AgentGraph";
import { ChatPanel } from "../components/ChatPanel";
import { Header } from "../components/Header";
import { PanelSwitch, type RightPanelView } from "../components/PanelSwitch";
import { ScenarioStatus } from "../components/ScenarioStatus";
import { SessionWindows } from "../components/SessionWindows";
import { useDemoObserver } from "../hooks/useDemoObserver";
import { GROUP_EVENTS_PATH } from "../live/api";
import { FULL_UNIVERSE } from "../types/agent";
import { DEMO_TOTAL_STEPS } from "./steps";

const HEADER = {
  title: "실행 기록",
  subtitle: "그룹 backend 관찰 채널 · 실제 lifecycle 이벤트",
  description:
    "Group Super에 들어온 요청과 그 실행(그룹 내부 위임, K전자 A2A, K전자 내부 위임 — relay된 이벤트)을 도착 순서대로 보여줍니다. 입력은 각 플랫폼 UI에서 합니다.",
  placeholder: "Group UI에서 Group Super에 요청을 보내면 여기에 실행 과정이 나타납니다.",
};

/**
 * Demo Monitor — a test / presentation observer, not part of either product
 * UI. Subscribes to the Group backend's event channel (which also carries the
 * K Electronics lifecycle relayed through the A2A Tool) and shows the full
 * agent graph, the demo Steps 1–8 derived from those events, and the
 * collaboration sessions. It never starts anything.
 */
export function DemoMonitor() {
  const [view, setView] = useState<RightPanelView>("graph");
  const observer = useDemoObserver(GROUP_EVENTS_PATH);

  return (
    <div className="app">
      <Header
        title="Demo Monitor"
        subtitle="두 Agent System의 실제 lifecycle 관찰 (테스트 · 발표용)"
        badge={observer.connected ? "관찰 채널 연결됨" : "관찰 채널 연결 중..."}
      />
      <div className="app__body">
        <div className="main">
          <div className="toolbar">
            <ScenarioStatus
              title="뉴로엣지 M&A 데모"
              status={observer.status}
              currentStep={observer.currentStep}
              totalSteps={DEMO_TOTAL_STEPS}
              stepTitle={observer.stepTitle}
              onReset={observer.reset}
            />
            <PanelSwitch view={view} onChange={setView} liveCount={observer.sessionStack.length} />
          </div>

          <main className={`workspace workspace--${view}`}>
            <div className="panel panel--chat">
              <ChatPanel
                tab="group_super"
                header={HEADER}
                readOnly
                items={observer.messages}
                busy={observer.status === "running"}
                onSend={() => {}}
                sessions={observer.sessions}
                onOpenSessions={view === "graph" ? () => setView("sessions") : undefined}
              />
            </div>

            {view === "graph" ? (
              <div className="panel panel--graph">
                <AgentGraph nodes={observer.nodes} edges={observer.edges} universe={FULL_UNIVERSE} />
              </div>
            ) : (
              <SessionWindows sessions={observer.sessions} />
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
