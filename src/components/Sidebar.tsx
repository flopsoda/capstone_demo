import { AGENTS, type ChatTabId } from "../types/agent";

export interface SidebarTab {
  id: ChatTabId;
  label: string;
  hint: string;
}

interface SidebarProps {
  tabs: SidebarTab[];
  selected: ChatTabId;
  onSelect: (tab: ChatTabId) => void;
  /** Live note under a tab, e.g. "외부 요청 처리 중 · 그룹" while an inbound A2A run is served. */
  badges?: Partial<Record<ChatTabId, string>>;
}

export function Sidebar({ tabs, selected, onSelect, badges = {} }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar__section-title">에이전트</div>
      <nav className="sidebar__nav">
        {tabs.map((tab) => {
          const meta = AGENTS[tab.id];
          const isSelected = tab.id === selected;
          return (
            <button
              key={tab.id}
              type="button"
              className={`sidebar__item${isSelected ? " sidebar__item--selected" : ""}`}
              onClick={() => onSelect(tab.id)}
              aria-current={isSelected ? "page" : undefined}
            >
              <span
                className={`sidebar__dot sidebar__dot--${meta.kind}`}
                aria-hidden="true"
              />
              <span className="sidebar__item-text">
                <span className="sidebar__item-label">{tab.label}</span>
                <span className="sidebar__item-hint">{tab.hint}</span>
                {badges[tab.id] && (
                  <span className="sidebar__item-badge">
                    <span className="session-card__live-dot" aria-hidden="true" />
                    {badges[tab.id]}
                  </span>
                )}
              </span>
            </button>
          );
        })}
      </nav>

      <div className="sidebar__footer">
        <div className="sidebar__legend-title">범례</div>
        <div className="sidebar__legend-row">
          <span className="sidebar__dot sidebar__dot--super" /> 슈퍼에이전트
        </div>
        <div className="sidebar__legend-row">
          <span className="sidebar__dot sidebar__dot--shared" /> 전문가 에이전트
        </div>
        <div className="sidebar__legend-row">
          <span className="legend-line legend-line--a2a" /> A2A (슈퍼에이전트 간 통신)
        </div>
        <div className="sidebar__legend-row">
          <span className="legend-line legend-line--task" /> 에이전트 위임
        </div>
        <div className="sidebar__legend-row">
          <span className="legend-code">run_*</span> 워크플로 도구
        </div>
      </div>
    </aside>
  );
}
