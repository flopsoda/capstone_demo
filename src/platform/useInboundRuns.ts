import { useEffect, useState } from "react";
import { createAgentEventMapper } from "../live/agentEvents";
import { isAgentId, subscribeAgentEvents, type AgentEvent } from "../live/api";
import { createEventQueue, type RunnerAction } from "../state/runState";
import type { ChatTabId } from "../types/agent";
import type { PlatformConfig } from "./config";

/** The requesting system currently being served, for the sidebar badge. */
export interface InboundActivity {
  label: string;
}

/**
 * Shows runs that another agent system started in this platform's backend
 * (A2A inbound, `origin: "a2a"` on the backend's observer channel) as a
 * received request in the Super Agent's tab: a session card with the peer's
 * request, the delegation windows the Super Agent opens while answering,
 * and the answer handed back. Nothing switches tabs; elsewhere only the
 * sidebar badge shows that an external request is being served.
 *
 * A user chat and an inbound run on the same tab at the same time would
 * share one transcript; the demo never does that.
 */
export function useInboundRuns(
  config: PlatformConfig,
  dispatch: (tab: ChatTabId, action: RunnerAction) => void,
): InboundActivity | null {
  const [active, setActive] = useState<InboundActivity | null>(null);
  const inbound = config.inbound;

  useEffect(() => {
    if (!inbound) return;
    const { eventsPath, tab, callers } = inbound;
    let runId: string | null = null;
    let lastAnswer = "";
    let mapEvent = createAgentEventMapper(config.resolveAgent);
    let queue = createEventQueue((event) => dispatch(tab, { type: "apply", event }), () => true);
    const apply = (event: Parameters<typeof queue.push>[0]) => queue.push(event);

    const begin = (ev: Extract<AgentEvent, { type: "agent_start" }>) => {
      runId = ev.run_id ?? null;
      lastAnswer = "";
      const current = runId;
      mapEvent = createAgentEventMapper(config.resolveAgent);
      queue = createEventQueue((event) => dispatch(tab, { type: "apply", event }), () => runId === current);
      const caller = callers[ev.caller ?? ""] ?? { agent: "chairman" as const, label: ev.caller ?? "외부" };
      setActive({ label: caller.label });
      apply({ type: "session_open", communication: "a2a", title: `외부 A2A 요청 · ${caller.label}`, from: caller.agent, to: tab, content: ev.request });
      apply({ type: "agent_start", agent: tab, from: caller.agent, communication: "a2a", status: "외부 요청 수신" });
    };

    return subscribeAgentEvents(eventsPath, (ev) => {
      if (ev.origin !== "a2a" || ev.relayed) return;
      if (ev.type === "agent_start" && ev.run_id !== runId && isAgentId(ev.agent) && ev.agent === tab) begin(ev);
      if (runId === null || ev.run_id !== runId) return;

      if (ev.type === "agent_end" && ev.agent === tab) lastAnswer = ev.response;
      mapEvent(ev).forEach(apply);

      if (ev.type === "done") {
        apply({ type: "session_close", resultTitle: "응답 반환", result: lastAnswer });
        apply({ type: "agent_complete", agent: tab });
      } else if (ev.type === "error") {
        const agent = config.resolveAgent(ev.system, ev.agent);
        apply({ type: "scenario_error", agent: isAgentId(agent) ? agent : tab, message: ev.message });
      }
      if (ev.type === "done" || ev.type === "error") {
        // The badge goes out when the transcript has caught up (events carry readability delays).
        const finished = runId;
        void queue.settled().then(() => {
          if (runId === finished) setActive(null);
        });
      }
    });
  }, [config, dispatch, inbound]);

  return active;
}
