import type { ScenarioStatus as ScenarioStatusType } from "../types/scenario";

interface ScenarioStatusProps {
  title: string;
  status: ScenarioStatusType;
  currentStep: number;
  totalSteps: number;
  stepTitle: string;
  onReset: () => void;
}

/** One-line scenario status for the toolbar: title · step · state, plus Reset. */
export function ScenarioStatus({
  title,
  status,
  currentStep,
  totalSteps,
  stepTitle,
  onReset,
}: ScenarioStatusProps) {
  const progress =
    status === "completed" ? 100 : Math.round((currentStep / totalSteps) * 100);

  return (
    <div className="scenario-status">
      <span className="scenario-status__title">
        {status === "idle" ? "에이전트 파이프라인" : title}
      </span>
      <span className="scenario-status__step">
        {status === "idle" && "Group UI에서 그룹 슈퍼에이전트에 요청을 보내면 실행이 여기에 나타납니다."}
        {(status === "running" || status === "error") && (
          <>
            {currentStep} / {totalSteps} 단계
            {stepTitle && <span className="scenario-status__step-title"> · {stepTitle}</span>}
          </>
        )}
        {status === "completed" && `${totalSteps} / ${totalSteps} 단계`}
      </span>
      {status === "running" && <span className="badge badge--running">진행 중</span>}
      {status === "completed" && <span className="badge badge--done">완료 ✓</span>}
      {status === "error" && <span className="badge badge--high">오류 · 중단</span>}

      <button
        type="button"
        className="btn btn--ghost scenario-status__reset"
        onClick={onReset}
        disabled={status === "idle"}
      >
        초기화
      </button>

      {status !== "idle" && (
        <div className="scenario-status__bar" aria-hidden="true">
          <div className="scenario-status__bar-fill" style={{ width: `${progress}%` }} />
        </div>
      )}
    </div>
  );
}
