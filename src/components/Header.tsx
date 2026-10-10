export function Header() {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <div className="app-header__logo" aria-hidden="true">
          EA
        </div>
        <div>
          <div className="app-header__title">Executive AI</div>
          <div className="app-header__subtitle">
            Multi-Agent Decision Support Demo
          </div>
        </div>
      </div>
      <span className="badge badge--outline">M&amp;A Scenario Demo</span>
    </header>
  );
}
