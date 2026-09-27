import { useT } from "../i18n";

// Shown instead of a page the user's role can never open: no request is sent (so no 403 toasts and no
// endless spinner), and the requirement is stated.
export default function PermissionNotice({ requires }) {
  const t = useT();
  return (
    <div className="nx-empty" role="status">
      <svg width="28" height="28" viewBox="0 0 20 20" aria-hidden="true" style={{ color: "var(--color-text-muted)" }}><rect x="4" y="9" width="12" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M7 9V6.5a3 3 0 016 0V9" fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>
      <h2>{t("perm.title")}</h2>
      <p className="nx-muted" style={{ margin: 0 }}>{t("perm.body", { role: requires })}</p>
    </div>
  );
}
