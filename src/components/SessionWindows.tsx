import { useCollapsePhase } from "../hooks/useCollapsePhase";
import type { Session } from "../types/scenario";
import { SessionWindow } from "./SessionWindow";

interface SessionWindowsProps {
  sessions: Record<string, Session>;
}

/**
 * Chat windows for every live A2A / delegation session, side by side with
 * the main chat. Sessions are keyed in opening order; each window unmounts
 * itself after its closing animation, so the row grows and shrinks as the
 * orchestration proceeds (main → +A2A → +delegation → … → main).
 */
export function SessionWindows({ sessions }: SessionWindowsProps) {
  const ids = Object.keys(sessions);
  const anyLive = ids.some((id) => sessions[id].status === "open");
  // The placeholder waits until the last window has finished flying away.
  const livePhase = useCollapsePhase(anyLive);

  return (
    <>
      {ids.map((id) => (
        <SessionWindow key={id} session={sessions[id]} sessions={sessions} />
      ))}
      {livePhase === "closed" && (
        <div className="panel panel--session-empty">
          <div className="sessions__empty">
            <p>A2A 또는 Agent Delegation이 시작되면 여기에 새 채팅창이 열립니다.</p>
            <p className="sessions__empty-sub">
              세션이 끝나면 창은 요청 말풍선으로 접혀 들어가고, 결과만 남습니다.
            </p>
          </div>
        </div>
      )}
    </>
  );
}
