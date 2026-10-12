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
}

export function Sidebar({ tabs, selected, onSelect }: SidebarProps) {
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
