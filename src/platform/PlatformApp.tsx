import { useState } from "react";
import { ChatPanel } from "../components/ChatPanel";
import { Header } from "../components/Header";
import { SessionWindows } from "../components/SessionWindows";
import { Sidebar } from "../components/Sidebar";
import type { ChatTabId } from "../types/agent";
import { tabConfig, type PlatformConfig } from "./config";
import { useInboundRuns } from "./useInboundRuns";
import { usePlatformChats } from "./usePlatformChats";

interface PlatformAppProps {
  config: PlatformConfig;
}

/**
 * A platform's product UI: the sidebar lists the agents the user can talk
 * to directly (its Super Agent and its specialists); the chat shows the
 * selected conversation; beside it, every A2A / delegation session the
 * agent opens while answering gets its own window and folds back into the
 * request card when it ends. No graph, no demo Steps — those live in the
 * Demo Monitor (/monitor). A platform with an A2A surface also shows the
 * requests other systems send to its backend (useInboundRuns).
 */
export function PlatformApp({ config }: PlatformAppProps) {
  const [tab, setTab] = useState<ChatTabId>(config.tabs[0].id);
  const chats = usePlatformChats(config);
  const inbound = useInboundRuns(config, chats.dispatch);
  const current = chats.states[tab];
  const { header, samplePrompt } = tabConfig(config, tab);
  const badges = inbound && config.inbound ? { [config.inbound.tab]: `외부 요청 처리 중 · ${inbound.label}` } : {};

  return (
    <div className="app">
      <Header title={config.title} subtitle={config.subtitle} badge={config.badge} />
      <div className="app__body">
        <Sidebar tabs={config.tabs} selected={tab} onSelect={setTab} badges={badges} />

        <div className="main">
          <main className="workspace workspace--sessions">
            <div className="panel panel--chat">
              <ChatPanel
                key={tab}
                tab={tab}
                header={header}
                items={current.messages}
                busy={chats.busy.has(tab)}
                onSend={(text) => void chats.send(tab, text)}
                samplePrompt={samplePrompt}
                sessions={current.sessions}
              />
            </div>
            <SessionWindows sessions={current.sessions} />
          </main>
        </div>
      </div>
    </div>
  );
}
