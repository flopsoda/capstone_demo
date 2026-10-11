import { useCallback, useEffect, useRef, useState } from "react";
import { AgentGraph } from "./components/AgentGraph";
import { ChatPanel } from "./components/ChatPanel";
import { Header } from "./components/Header";
import { PanelSwitch, type RightPanelView } from "./components/PanelSwitch";
import { ScenarioStatus } from "./components/ScenarioStatus";
import { SessionWindows } from "./components/SessionWindows";
import { Sidebar } from "./components/Sidebar";
import { useScenarioRunner } from "./hooks/useScenarioRunner";
import { createAgentEventMapper } from "./live/agentEvents";
import { MEETING_AGENT_RUN_PATH, NEWS_AGENT_RUN_PATH, streamAgentRun } from "./live/api";
import { MNA_SAMPLE_PROMPT, mnaScenario } from "./scenario/mnaScenario";
import { getMockResponse } from "./scenario/mockResponses";
import { AGENTS, type ChatTabId } from "./types/agent";
import type { ChatItem } from "./types/scenario";

type SideTab = Exclude<ChatTabId, "group_super">;

/** Specialist tabs answered by the real backend agent (same core K Super delegates to). */
const LIVE_AGENT_PATHS: Partial<Record<SideTab, string>> = {
  meeting: MEETING_AGENT_RUN_PATH,
  news: NEWS_AGENT_RUN_PATH,
};

const MOCK_REPLY_DELAY = 700;
/** Direct specialist-agent call: node stays active while the Workflow Tool "runs". */
const DIRECT_RUN_DELAY = 1400;

let sideIdCounter = 0;
const sideId = () => `s${++sideIdCounter}`;

export default function App() {
  const [tab, setTab] = useState<ChatTabId>("group_super");
  const [view, setView] = useState<RightPanelView>("graph");
  const runner = useScenarioRunner(mnaScenario);

  /* Standalone chats for the specialist agents (not part of the scenario). */
  const [sideChats, setSideChats] = useState<Record<SideTab, ChatItem[]>>({
    advisory: [],
    news: [],
    meeting: [],
  });
  const [replyPending, setReplyPending] = useState<ChatTabId | null>(null);
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
            content: getMockResponse("group_super", text),
          });
          setReplyPending(null);
        }, MOCK_REPLY_DELAY);
        return;
      }

      // Direct user → specialist agent. The same agent core and Workflow Tool
      // that the Super Agent delegates to; the graph node lights up on its own.
      const side = tab;
      const pushSide = (...items: ChatItem[]) =>
        setSideChats((prev) => ({ ...prev, [side]: [...prev[side], ...items] }));
      pushSide({ id: sideId(), kind: "user", content: text });
      setReplyPending(side);

      const livePath = LIVE_AGENT_PATHS[side];
      if (livePath) {
        // REAL: the same specialist agent graph that K Super delegates to (Meeting: Step 3, News: Step 4).
        // Backend events drive the node (status / Workflow Tool) and this chat live.
        const mapEvent = createAgentEventMapper();
        runner
          .runDirectLive(side, "", () =>
            streamAgentRun(
              livePath,
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
        return;
      }

      runner.runDirect(side, AGENTS[side].workflow ?? "", DIRECT_RUN_DELAY);
      replyTimer.current = window.setTimeout(() => {
        setSideChats((prev) => ({
          ...prev,
          [side]: [
            ...prev[side],
            {
              id: sideId(),
              kind: "assistant",
              sender: side,
              content: getMockResponse(side, text),
            },
          ],
        }));
        setReplyPending(null);
      }, DIRECT_RUN_DELAY);
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
        <Sidebar selected={tab} onSelect={setTab} />

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
