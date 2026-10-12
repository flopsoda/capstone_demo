import { useEffect, useMemo, useRef } from "react";
import {
  Background,
  BackgroundVariant,
  MarkerType,
  ReactFlow,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {
  AGENTS,
  DEFAULT_UNIVERSE,
  TOPOLOGY,
  type AgentId,
  type CommunicationType,
  type EdgeState,
  type GraphUniverse,
} from "../types/agent";
import type { EdgeRuntime, NodeRuntime } from "../types/scenario";
import { AgentNode, type AgentFlowNode } from "./AgentNode";

/* ------------------------------------------------------------------ */
/* Visual tokens for edges                                             */
/* ------------------------------------------------------------------ */

const EDGE_COLORS: Record<CommunicationType, string> = {
  a2a: "#4f46e5",
  delegation: "#0f766e",
};
const EDGE_LABELS: Record<CommunicationType, string> = {
  a2a: "A2A",
  delegation: "위임",
};
const IDLE_COLOR = "#c3cad4";
const USER_COLOR = "#475569";

function edgeStyle(communication: CommunicationType, state: EdgeState, isUserLink: boolean) {
  const color =
    state === "idle" ? IDLE_COLOR : isUserLink ? USER_COLOR : EDGE_COLORS[communication];
  const width = state === "active" ? 2.4 : state === "completed" ? 1.8 : 1;
  const opacity = state === "idle" ? 0.45 : 1;
  return { color, width, opacity };
}

/* ------------------------------------------------------------------ */
/* Shared Agent Pool backdrop                                          */
/* ------------------------------------------------------------------ */

type PoolNode = Node<{ label: string }, "pool">;

function PoolBackdrop({ data }: NodeProps<PoolNode>) {
  return (
    <div className="pool-backdrop">
      <span className="pool-backdrop__label">{data.label}</span>
    </div>
  );
}

const NODE_TYPES = { agent: AgentNode, pool: PoolBackdrop };

/* ------------------------------------------------------------------ */
/* Keep the fixed layout fully visible when the panel resizes          */
/* ------------------------------------------------------------------ */

function FitOnResize({ target }: { target: React.RefObject<HTMLDivElement | null> }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const el = target.current;
    if (!el) return;
    const fit = () => fitView({ padding: 0.08, duration: 150 });
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [fitView, target]);
  return null;
}

/* ------------------------------------------------------------------ */
/* Graph                                                               */
/* ------------------------------------------------------------------ */

interface AgentGraphProps {
  nodes: Record<AgentId, NodeRuntime>;
  edges: Record<string, EdgeRuntime>;
  /** Agent currently selected in the sidebar (shown as selected, not active). */
  selectedAgent?: AgentId;
  /** Which agents to draw and where. Defaults to the original K Electronics-centred graph. */
  universe?: GraphUniverse;
}

export function AgentGraph({ nodes, edges, selectedAgent, universe = DEFAULT_UNIVERSE }: AgentGraphProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);

  const flowNodes = useMemo<Node[]>(() => {
    const pools: PoolNode[] = universe.pools.map((p, i) => ({
      id: `pool-${i}`,
      type: "pool",
      position: { x: p.x, y: p.y },
      data: { label: p.label },
      draggable: false,
      selectable: false,
      connectable: false,
      focusable: false,
      zIndex: -1,
      style: { width: p.width, height: p.height },
    }));

    const agentNodes: AgentFlowNode[] = universe.agents.map((id) => {
      const meta = AGENTS[id];
      const rt = nodes[id];
      return {
        id,
        type: "agent",
        position: universe.positions[id] ?? { x: 0, y: 0 },
        draggable: false,
        selectable: false,
        connectable: false,
        focusable: false,
        data: {
          agentId: id,
          label: meta.shortName,
          role: meta.role,
          kind: meta.kind,
          state: rt.state,
          status: rt.status,
          emphasized: rt.emphasized,
          workflow: rt.workflow,
          selected: selectedAgent === id,
        },
      };
    });

    return [...pools, ...agentNodes];
  }, [nodes, selectedAgent, universe]);

  const flowEdges = useMemo<Edge[]>(
    () =>
      TOPOLOGY.filter((t) => universe.agents.includes(t.from) && universe.agents.includes(t.to)).map((t) => {
        const rt = edges[t.id] ?? { state: "idle", direction: "forward" };
        const forward = rt.direction === "forward";
        // Chairman ↔ CEO is the user's request/response, not an agent task.
        const isUserLink = t.from === "chairman";
        const { color, width, opacity } = edgeStyle(t.communication, rt.state, isUserLink);
        const isA2A = t.communication === "a2a";
        const showLabel = !isUserLink && (isA2A || rt.state !== "idle");

        return {
          id: t.id,
          source: forward ? t.from : t.to,
          target: forward ? t.to : t.from,
          sourceHandle: forward ? "b-out" : "t-out",
          targetHandle: forward ? "t-in" : "b-in",
          type: "default",
          animated: rt.state === "active",
          className: `edge edge--${t.communication} edge--${rt.state}`,
          zIndex: rt.state === "active" ? 10 : rt.state === "completed" ? 5 : 0,
          // A2A is one level above delegation: draw it a touch thicker.
          style: {
            stroke: color,
            strokeWidth: isA2A && rt.state !== "idle" ? width + 0.8 : width,
            opacity,
          },
          markerEnd:
            rt.state === "idle"
              ? undefined
              : { type: MarkerType.ArrowClosed, color, width: 18, height: 18 },
          label: showLabel ? EDGE_LABELS[t.communication] : undefined,
          labelShowBg: true,
          labelStyle: {
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: 0.4,
            fill: rt.state === "idle" ? "#8a94a3" : color,
          },
          labelBgStyle: {
            fill: rt.state === "idle" ? "#f6f8fb" : "#ffffff",
            fillOpacity: 0.95,
            stroke: rt.state === "idle" ? "#e3e7ee" : color,
            strokeWidth: isA2A ? 1 : 0.6,
          },
          labelBgPadding: [5, 2],
          labelBgBorderRadius: 4,
          data: { communication: t.communication, state: rt.state },
        } satisfies Edge;
      }),
    [edges, universe],
  );

  return (
    <div className="graph" ref={wrapperRef}>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.08 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
        minZoom={0.3}
        maxZoom={1.5}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#dfe4ea" />
        <FitOnResize target={wrapperRef} />
      </ReactFlow>
    </div>
  );
}
