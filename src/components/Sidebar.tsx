import { AGENTS, type ChatTabId } from "../types/agent";

interface SidebarProps {
  selected: ChatTabId;
  onSelect: (tab: ChatTabId) => void;
}

const TABS: { id: ChatTabId; label: string; hint: string }[] = [
  { id: "group_super", label: "Group Super Agent", hint: "Super Agent" },
  { id: "advisory", label: "Management Advisory", hint: "Specialist Agent" },
  { id: "news", label: "News", hint: "Specialist Agent" },
  { id: "meeting", label: "Meeting", hint: "Specialist Agent" },
];

export function Sidebar({ selected, onSelect }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar__section-title">Agents</div>
      <nav className="sidebar__nav">
        {TABS.map((tab) => {
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
        <div className="sidebar__legend-title">Legend</div>
        <div className="sidebar__legend-row">
          <span className="sidebar__dot sidebar__dot--super" /> Super Agent
        </div>
        <div className="sidebar__legend-row">
          <span className="sidebar__dot sidebar__dot--shared" /> Specialist Agent
        </div>
        <div className="sidebar__legend-row">
          <span className="legend-line legend-line--a2a" /> A2A (Super ↔ Super)
        </div>
        <div className="sidebar__legend-row">
          <span className="legend-line legend-line--task" /> Agent Delegation
        </div>
        <div className="sidebar__legend-row">
          <span className="legend-code">run_*</span> Workflow Tool
        </div>
      </div>
    </aside>
  );
}
