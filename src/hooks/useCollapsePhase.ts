import { useEffect, useState } from "react";

/** Duration of the "window flies into its request bubble" animation. */
export const COLLAPSE_MS = 1400;

export type CollapsePhase = "open" | "collapsing" | "closed";

/**
 * Tracks an open → closed transition so the element can stay mounted while it
 * animates away. `open` → "open"; the first render after it turns false yields
 * "collapsing"; COLLAPSE_MS later it becomes "closed".
 */
export function useCollapsePhase(open: boolean): CollapsePhase {
  const [phase, setPhase] = useState<CollapsePhase>(open ? "open" : "closed");
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    setPhase(open ? "open" : "collapsing");
  }
  useEffect(() => {
    if (phase !== "collapsing") return;
    const t = window.setTimeout(() => setPhase("closed"), COLLAPSE_MS);
    return () => window.clearTimeout(t);
  }, [phase]);
  return phase;
}
