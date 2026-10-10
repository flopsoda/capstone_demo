export type RightPanelView = "graph" | "sessions";

interface PanelSwitchProps {
  view: RightPanelView;
  onChange: (view: RightPanelView) => void;
  /** Number of sessions currently open (shown as a small live counter). */
  liveCount: number;
}

/** Compact segmented toggle: topology graph vs. live session windows. */
export function PanelSwitch({ view, onChange, liveCount }: PanelSwitchProps) {
  return (
    <div className="panel-switch" role="tablist" aria-label="Right panel view">
      <button
        type="button"
        role="tab"
        aria-selected={view === "graph"}
        className={`panel-switch__tab${view === "graph" ? " panel-switch__tab--on" : ""}`}
        onClick={() => onChange("graph")}
      >
        Graph
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={view === "sessions"}
        className={`panel-switch__tab${view === "sessions" ? " panel-switch__tab--on" : ""}`}
        onClick={() => onChange("sessions")}
      >
        Sessions
        {liveCount > 0 && (
          <span className="panel-switch__live" title={`${liveCount} live session(s)`}>
            <span className="session-card__live-dot" aria-hidden="true" />
            {liveCount}
          </span>
        )}
      </button>
    </div>
  );
}
