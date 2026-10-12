import { useCallback, useState } from "react";
import { createEventQueue, createInitialRunnerState, reducer, type RunnerAction } from "../hooks/useScenarioRunner";
import { createAgentEventMapper } from "../live/agentEvents";
import { isAgentId, streamAgentRun } from "../live/api";
import type { ChatTabId } from "../types/agent";
import { LiveRunError, type RunnerState } from "../types/scenario";
import { tabConfig, type PlatformConfig } from "./config";

let idCounter = 0;
const nextId = () => `p${++idCounter}`;

export interface PlatformChats {
  /** Transcript + sessions of each tab (kept while switching tabs). */
  states: Record<ChatTabId, RunnerState>;
  /** Tabs with a run in progress. */
  busy: Set<ChatTabId>;
  send: (tab: ChatTabId, text: string) => Promise<void>;
}

/**
 * One conversation per sidebar tab, every reply produced by the real backend
 * agent behind that tab. The agent's lifecycle (delegations, A2A requests,
 * the sessions they open, Workflow results, the final answer) is applied in
 * arrival order through the same mapper and reducer as everywhere else.
 */
export function usePlatformChats(config: PlatformConfig): PlatformChats {
  const [states, setStates] = useState<Record<ChatTabId, RunnerState>>(() => {
    const out = {} as Record<ChatTabId, RunnerState>;
    for (const t of config.tabs) out[t.id] = createInitialRunnerState();
    return out;
  });
  const [busy, setBusy] = useState<Set<ChatTabId>>(() => new Set());

  const dispatch = useCallback((tab: ChatTabId, action: RunnerAction) => {
    setStates((prev) => ({ ...prev, [tab]: reducer(prev[tab] ?? createInitialRunnerState(), action) }));
  }, []);

  const send = useCallback(
    async (tab: ChatTabId, text: string) => {
      const { path } = tabConfig(config, tab);
      const resolve = config.resolveAgent;
      dispatch(tab, { type: "append_message", item: { id: nextId(), kind: "user", content: text } });
      dispatch(tab, { type: "apply", event: { type: "agent_start", agent: tab, status: "요청 분석 중..." } });
      setBusy((prev) => new Set(prev).add(tab));

      const mapEvent = createAgentEventMapper(resolve);
      const queue = createEventQueue((event) => dispatch(tab, { type: "apply", event }), () => true);
      try {
        await streamAgentRun(path, text, (event) => mapEvent(event).forEach(queue.push), tab);
        await queue.settled();
        dispatch(tab, { type: "apply", event: { type: "agent_complete", agent: tab } });
      } catch (err) {
        await queue.settled();
        let agent: ChatTabId | "chairman" = tab;
        if (err instanceof LiveRunError) {
          const resolved = resolve(err.system, err.rawAgent);
          if (isAgentId(resolved)) agent = resolved;
        }
        const message = err instanceof Error ? err.message : String(err);
        dispatch(tab, { type: "apply", event: { type: "scenario_error", agent, message } });
      } finally {
        setBusy((prev) => {
          const next = new Set(prev);
          next.delete(tab);
          return next;
        });
      }
    },
    [config, dispatch],
  );

  return { states, busy, send };
}
