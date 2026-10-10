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
        {status === "idle" ? "Agent Pipeline" : title}
      </span>
      <span className="scenario-status__step">
        {status === "idle" && "Group Super Agent에 요청을 보내면 시나리오가 시작됩니다."}
        {status === "running" && (
          <>
            Step {currentStep} / {totalSteps}
            {stepTitle && <span className="scenario-status__step-title"> · {stepTitle}</span>}
          </>
        )}
        {status === "completed" && `Step ${totalSteps} / ${totalSteps}`}
      </span>
      {status === "running" && <span className="badge badge--running">Running</span>}
      {status === "completed" && <span className="badge badge--done">Completed ✓</span>}

      <button
        type="button"
        className="btn btn--ghost scenario-status__reset"
        onClick={onReset}
        disabled={status === "idle"}
      >
        Reset
      </button>

      {status !== "idle" && (
        <div className="scenario-status__bar" aria-hidden="true">
          <div className="scenario-status__bar-fill" style={{ width: `${progress}%` }} />
        </div>
      )}
    </div>
  );
}
