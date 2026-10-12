interface HeaderProps {
  title?: string;
  subtitle?: string;
  badge?: string;
}

export function Header({
  title = "경영진 AI",
  subtitle = "멀티 에이전트 의사결정 지원 데모",
  badge = "M&A 시나리오 데모",
}: HeaderProps) {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <div className="app-header__logo" aria-hidden="true">
          EA
        </div>
        <div>
          <div className="app-header__title">{title}</div>
          <div className="app-header__subtitle">{subtitle}</div>
        </div>
      </div>
      <span className="badge badge--outline">{badge}</span>
    </header>
  );
}
