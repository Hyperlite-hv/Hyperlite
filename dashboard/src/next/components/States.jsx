import { useT } from "../i18n";

export function EmptyState({ title, help, action }) {
  return (
    <div className="nx-empty" role="status">
      <h2>{title}</h2>
      {help && <p className="nx-muted" style={{ margin: 0 }}>{help}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ title, message, onRetry }) {
  const t = useT();
  return (
    <div className="nx-error" role="alert">
      <h2>{title || t("err.title")}</h2>
      {message && <p className="nx-mono" style={{ margin: 0, overflowWrap: "anywhere" }}>{message}</p>}
      <div style={{ display: "flex", gap: "var(--space-2)" }}>
        {onRetry && <button type="button" className="nx-btn" onClick={onRetry}>{t("action.retry")}</button>}
        {message && <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigator.clipboard?.writeText(message)}>{t("err.copy")}</button>}
      </div>
    </div>
  );
}

// A failed load shown next to the control it feeds: without it, that control would
// just look empty or disabled, with nothing to explain why or to try again.
export function InlineError({ message, onRetry }) {
  const t = useT();
  return (
    <div className="nx-hint nx-hint--error" role="alert" style={{ display: "flex", gap: "var(--space-2)", alignItems: "center", flexWrap: "wrap" }}>
      <span>{t("err.loadFailed")}{message ? ` ${message}` : ""}</span>
      {onRetry && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={onRetry}>{t("action.retry")}</button>}
    </div>
  );
}

export function Skeleton({ width = "100%", height }) {
  return <div className="nx-skel" style={{ width, height }} aria-hidden="true" />;
}
