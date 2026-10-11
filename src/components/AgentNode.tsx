import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { AgentId, AgentKind, AgentState } from "../types/agent";

export interface AgentNodeData extends Record<string, unknown> {
  agentId: AgentId;
  label: string;
  role: string;
  kind: AgentKind;
  state: AgentState;
  status?: string;
  emphasized?: boolean;
  /** Workflow Tool selected by a specialist agent for the current request. */
  workflow?: string;
  /** Currently selected in the sidebar (distinct from "active"). */
  selected?: boolean;
}

export type AgentFlowNode = Node<AgentNodeData, "agent">;

const hiddenHandle = { opacity: 0, pointerEvents: "none" as const };

/**
 * Pipeline graph node. Four invisible handles let any edge travel in either
 * direction (top↔bottom) so returns like CFO → CEO render on the same line.
 */
export function AgentNode({ data }: NodeProps<AgentFlowNode>) {
  const classes = [
    "agent-node",
    `agent-node--${data.kind}`,
    `agent-node--${data.state}`,
    data.emphasized ? "agent-node--emphasized" : "",
    data.selected ? "agent-node--selected" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes}>
      <Handle type="target" position={Position.Top} id="t-in" style={hiddenHandle} />
      <Handle type="source" position={Position.Top} id="t-out" style={hiddenHandle} />
      <Handle type="source" position={Position.Bottom} id="b-out" style={hiddenHandle} />
      <Handle type="target" position={Position.Bottom} id="b-in" style={hiddenHandle} />

      <div className="agent-node__head">
        <span className="agent-node__label">{data.label}</span>
        {data.state === "completed" && (
          <span className="agent-node__check" aria-label="completed">
            ✓
          </span>
        )}
        {data.state === "active" && <span className="agent-node__pulse" aria-hidden="true" />}
        {data.state === "error" && (
          <span className="agent-node__error" aria-label="error">
            !
          </span>
        )}
      </div>
      <div className="agent-node__role">{data.role}</div>
      {data.workflow && data.state !== "idle" && (
        <div className="agent-node__workflow" title="워크플로 도구">
          {data.workflow}
        </div>
      )}
      {data.status && data.state === "active" && (
        <div className="agent-node__status">{data.status}</div>
      )}
    </div>
  );
}
