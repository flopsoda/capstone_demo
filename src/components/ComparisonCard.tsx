import { AGENTS } from "../types/agent";
import type { ComparisonData, ComparisonSide } from "../types/scenario";

function Side({ side }: { side: ComparisonSide }) {
  const meta = AGENTS[side.agent];
  return (
    <div className={`compare__side compare__side--${side.tone}`}>
      <div className="compare__agent">{meta.shortName}</div>
      <div className={`compare__stance compare__stance--${side.tone}`}>
        <span aria-hidden="true">{side.tone === "ok" ? "✓" : "⚠"}</span> {side.stance}
      </div>
      <dl className="msg-kv">
        {side.items.map((it) => (
          <div key={it.k} className="msg-kv__row">
            <dt>{it.k}</dt>
            <dd>{it.v}</dd>
          </div>
        ))}
      </dl>
      <div className="msg-label">{side.risksLabel}</div>
      <ul className="compare__risks">
        {side.risks.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
    </div>
  );
}

/** Side-by-side CFO vs CTO review card — the key scene of the scenario. */
export function ComparisonCard({ data }: { data: ComparisonData }) {
  return (
    <div className="compare">
      <div className="compare__title">{data.title}</div>
      <div className="compare__grid">
        <Side side={data.left} />
        <div className="compare__vs" aria-hidden="true">
          VS
        </div>
        <Side side={data.right} />
      </div>
    </div>
  );
}
