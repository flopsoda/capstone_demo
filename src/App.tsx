import { useCallback, useEffect, useRef, useState } from "react";
import { AgentGraph } from "./components/AgentGraph";
import { ChatPanel } from "./components/ChatPanel";
import { Header } from "./components/Header";
import { PanelSwitch, type RightPanelView } from "./components/PanelSwitch";
import { ScenarioStatus } from "./components/ScenarioStatus";
import { SessionWindows } from "./components/SessionWindows";
import { Sidebar, type SidebarTab } from "./components/Sidebar";
import { useScenarioRunner } from "./hooks/useScenarioRunner";
import { createAgentEventMapper } from "./live/agentEvents";
import { ADVISORY_AGENT_RUN_PATH, MEETING_AGENT_RUN_PATH, NEWS_AGENT_RUN_PATH, streamAgentRun } from "./live/api";
import { MNA_SAMPLE_PROMPT, mnaScenario } from "./scenario/mnaScenario";
import { getGroupSuperFallback } from "./scenario/mockResponses";
import type { ChatItem } from "./types/scenario";

type SideTab = "advisory" | "news" | "meeting";
type LegacyTab = "group_super" | SideTab;

const LEGACY_TABS: SidebarTab[] = [
  { id: "group_super", label: "그룹 슈퍼에이전트", hint: "슈퍼에이전트" },
  { id: "advisory", label: "경영자문", hint: "전문가 에이전트" },
  { id: "news", label: "뉴스", hint: "전문가 에이전트" },
  { id: "meeting", label: "미팅", hint: "전문가 에이전트" },
];

/** Specialist tabs are answered by the real backend agents (same cores K Super delegates to). */
const LIVE_AGENT_PATHS: Record<SideTab, string> = {
  meeting: MEETING_AGENT_RUN_PATH,
  news: NEWS_AGENT_RUN_PATH,
  advisory: ADVISORY_AGENT_RUN_PATH,
};

const MOCK_REPLY_DELAY = 700;

let sideIdCounter = 0;
const sideId = () => `s${++sideIdCounter}`;

export default function App() {
  const [tab, setTab] = useState<LegacyTab>("group_super");
  const [view, setView] = useState<RightPanelView>("graph");
  const runner = useScenarioRunner(mnaScenario);

  /* Standalone chats for the specialist agents (not part of the scenario). */
  const [sideChats, setSideChats] = useState<Record<SideTab, ChatItem[]>>({
    advisory: [],
    news: [],
    meeting: [],
  });
  const [replyPending, setReplyPending] = useState<LegacyTab | null>(null);
  const replyTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (replyTimer.current !== null) window.clearTimeout(replyTimer.current);
    },
    [],
  );

  const handleSend = useCallback(
    (text: string) => {
      if (tab === "group_super") {
        if (mnaScenario.matches(text)) {
          runner.start(text);
          return;
        }
        // Non-scenario input: answer with a fixed hint, graph untouched.
        runner.appendMessage({ id: sideId(), kind: "user", content: text });
        setReplyPending("group_super");
        replyTimer.current = window.setTimeout(() => {
          runner.appendMessage({
            id: sideId(),
            kind: "assistant",
            sender: "group_super",
            content: getGroupSuperFallback(),
          });
          setReplyPending(null);
        }, MOCK_REPLY_DELAY);
        return;
      }

      // REAL: direct user → specialist agent. The same agent graph that K Super
      // delegates to (Meeting: Step 3, News: Step 4, Advisory: Step 5), invoked in
      // its "direct" context. Backend events drive the node (status / Workflow
      // Tool) and this chat live; whether a Workflow Tool runs is the agent's call.
      const side = tab;
      const pushSide = (...items: ChatItem[]) =>
        setSideChats((prev) => ({ ...prev, [side]: [...prev[side], ...items] }));
      pushSide({ id: sideId(), kind: "user", content: text });
      setReplyPending(side);

      const mapEvent = createAgentEventMapper();
      runner
        .runDirectLive(side, () =>
          streamAgentRun(
            LIVE_AGENT_PATHS[side],
            text,
            (event) => {
              for (const e of mapEvent(event)) {
                if (e.type === "message") {
                  pushSide({ id: sideId(), kind: "assistant", sender: e.sender, title: e.title, content: e.content });
                } else if (e.type === "agent_status") {
                  runner.apply(e);
                }
                // agent_start / agent_complete are owned by runDirectLive here.
              }
            },
            side,
          ),
        )
        .catch((err: unknown) => {
          pushSide({
            id: sideId(),
            kind: "assistant",
            sender: side,
            title: "오류",
            content: [
              { kind: "badge", text: "실행 실패", tone: "high" },
              { kind: "text", text: err instanceof Error ? err.message : String(err) },
            ],
          });
        })
        .finally(() => setReplyPending(null));
    },
    [tab, runner],
  );

  const items = tab === "group_super" ? runner.messages : sideChats[tab];
  const busy =
    replyPending === tab || (tab === "group_super" && runner.status === "running");

  return (
    <div className="app">
      <Header />
      <div className="app__body">
        <Sidebar tabs={LEGACY_TABS} selected={tab} onSelect={(t) => setTab(t as LegacyTab)} />

        <div className="main">
          <div className="toolbar">
            <ScenarioStatus
              title={runner.title}
              status={runner.status}
              currentStep={runner.currentStep}
              totalSteps={runner.totalSteps}
              stepTitle={runner.stepTitle}
              onReset={runner.reset}
            />
            <PanelSwitch view={view} onChange={setView} liveCount={runner.sessionStack.length} />
          </div>

          <main className={`workspace workspace--${view}`}>
            <div className="panel panel--chat">
              <ChatPanel
                key={tab}
                tab={tab}
                items={items}
                busy={busy}
                onSend={handleSend}
                samplePrompt={tab === "group_super" ? MNA_SAMPLE_PROMPT : undefined}
                sessions={tab === "group_super" ? runner.sessions : undefined}
                onOpenSessions={view === "graph" ? () => setView("sessions") : undefined}
              />
            </div>

            {view === "graph" ? (
              <div className="panel panel--graph">
                <AgentGraph nodes={runner.nodes} edges={runner.edges} selectedAgent={tab} />
              </div>
            ) : (
              <SessionWindows sessions={runner.sessions} />
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
