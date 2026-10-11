export function Header() {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <div className="app-header__logo" aria-hidden="true">
          EA
        </div>
        <div>
          <div className="app-header__title">경영진 AI</div>
          <div className="app-header__subtitle">멀티 에이전트 의사결정 지원 데모</div>
        </div>
      </div>
      <span className="badge badge--outline">M&amp;A 시나리오 데모</span>
    </header>
  );
}
