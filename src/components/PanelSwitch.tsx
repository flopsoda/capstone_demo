export type RightPanelView = "graph" | "sessions";

interface PanelSwitchProps {
  view: RightPanelView;
  onChange: (view: RightPanelView) => void;
  /** Number of sessions currently open (shown as a small live counter). */
  liveCount: number;
}

/** Compact segmented toggle: topology graph vs. live collaboration windows. */
export function PanelSwitch({ view, onChange, liveCount }: PanelSwitchProps) {
  return (
    <div className="panel-switch" role="tablist" aria-label="오른쪽 화면 전환">
      <button
        type="button"
        role="tab"
        aria-selected={view === "graph"}
        className={`panel-switch__tab${view === "graph" ? " panel-switch__tab--on" : ""}`}
        onClick={() => onChange("graph")}
      >
        그래프
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={view === "sessions"}
        className={`panel-switch__tab${view === "sessions" ? " panel-switch__tab--on" : ""}`}
        onClick={() => onChange("sessions")}
      >
        협업 대화
        {liveCount > 0 && (
          <span className="panel-switch__live" title={`진행 중인 협업 대화 ${liveCount}건`}>
            <span className="session-card__live-dot" aria-hidden="true" />
            {liveCount}
          </span>
        )}
      </button>
    </div>
  );
}
