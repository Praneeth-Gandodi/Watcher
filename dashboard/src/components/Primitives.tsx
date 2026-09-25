import type { ReactNode } from "react";

import type { StatusTone } from "../selectors";

export function StatusPill({
  tone,
  children,
  title,
}: {
  tone: StatusTone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className={`pill pill--${tone}`} title={title}>
      <span className="pill__mark" aria-hidden="true" />
      {children}
    </span>
  );
}

export function Panel({
  title,
  kicker,
  actions,
  children,
  className = "",
  id,
}: {
  title: string;
  kicker?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section className={`panel ${className}`.trim()} aria-labelledby={id ? `${id}-title` : undefined}>
      <header className="panel__head">
        <div className="panel__heading">
          {kicker ? <p className="kicker">{kicker}</p> : null}
          <h2 className="panel__title" id={id ? `${id}-title` : undefined}>
            {title}
          </h2>
        </div>
        {actions ? <div className="panel__actions">{actions}</div> : null}
      </header>
      {children}
    </section>
  );
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      <p className="muted">{detail}</p>
    </div>
  );
}

export function Metric({
  label,
  value,
  detail,
  tone = "idle",
}: {
  label: string;
  value: string;
  detail: string;
  tone?: StatusTone;
}) {
  return (
    <div className={`metric metric--${tone}`}>
      <span className="metric__label">{label}</span>
      <span className="metric__value">{value}</span>
      <span className="metric__detail">{detail}</span>
    </div>
  );
}
