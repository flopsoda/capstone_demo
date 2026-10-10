import type { MessageContent as MessageContentType } from "../types/scenario";

interface Props {
  content: MessageContentType;
}

/** Renders either a plain multi-line string or a list of structured blocks. */
export function MessageContent({ content }: Props) {
  if (typeof content === "string") {
    return <p className="msg-text">{content}</p>;
  }

  return (
    <div className="msg-blocks">
      {content.map((block, i) => {
        switch (block.kind) {
          case "text":
            return (
              <p key={i} className="msg-text">
                {block.text}
              </p>
            );
          case "label":
            return (
              <div key={i} className="msg-label">
                {block.text}
              </div>
            );
          case "kv":
            return (
              <dl key={i} className="msg-kv">
                {block.items.map((it) => (
                  <div key={it.k} className="msg-kv__row">
                    <dt>{it.k}</dt>
                    <dd>{it.v}</dd>
                  </div>
                ))}
              </dl>
            );
          case "list":
            return block.ordered ? (
              <ol key={i} className="msg-list">
                {block.items.map((it) => (
                  <li key={it}>{it}</li>
                ))}
              </ol>
            ) : (
              <ul key={i} className="msg-list">
                {block.items.map((it) => (
                  <li key={it}>{it}</li>
                ))}
              </ul>
            );
          case "badge":
            return (
              <span key={i} className={`badge badge--${block.tone}`}>
                {block.text}
              </span>
            );
          default:
            return null;
        }
      })}
    </div>
  );
}
