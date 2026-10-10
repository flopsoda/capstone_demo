import { useCallback, useEffect, useRef, useState } from "react";
import { AgentGraph } from "./components/AgentGraph";
import { ChatPanel } from "./components/ChatPanel";
import { Header } from "./components/Header";
import { PanelSwitch, type RightPanelView } from "./components/PanelSwitch";
import { ScenarioStatus } from "./components/ScenarioStatus";
import { SessionWindows } from "./components/SessionWindows";
import { Sidebar } from "./components/Sidebar";
import { useScenarioRunner } from "./hooks/useScenarioRunner";
import { MNA_SAMPLE_PROMPT, mnaScenario } from "./scenario/mnaScenario";
import { getMockResponse } from "./scenario/mockResponses";
import { AGENTS, type ChatTabId } from "./types/agent";
import type { ChatItem } from "./types/scenario";

type SideTab = Exclude<ChatTabId, "group_super">;

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
      setSideChats((prev) => ({
        ...prev,
        [side]: [...prev[side], { id: sideId(), kind: "user", content: text }],
      }));
      setReplyPending(side);
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
