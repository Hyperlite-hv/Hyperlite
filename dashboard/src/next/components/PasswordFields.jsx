import { useState } from "react";
import { Check, Copy, Eye, EyeOff, WandSparkles, X } from "lucide-react";
import { useT } from "../i18n";
import { Field } from "./ui";
import { MIN_LENGTH, generatePassword, passwordChecks } from "../lib/passwordPolicy";

// Password input with a show/hide toggle, inside a labelled Field.
export function PasswordInput({ label, value, onChange, autoComplete, error, hint, inputMode, ariaLabel }) {
  const t = useT();
  const [reveal, setReveal] = useState(false);
  return (
    <Field label={label} error={error} hint={hint}>
      {(p) => (
        <div className="nx-unit">
          <input {...p} className="nx-inp nx-inp--pw" type={reveal ? "text" : "password"} value={value} autoComplete={autoComplete} inputMode={inputMode}
            aria-label={ariaLabel || label} spellCheck={false} autoCapitalize="off" onChange={(e) => onChange(e.target.value)} />
          <button type="button" className="nx-login-eye" aria-label={t(reveal ? "login.hide" : "login.show")} aria-pressed={reveal} onClick={() => setReveal((r) => !r)}>
            {reveal ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
          </button>
        </div>
      )}
    </Field>
  );
}

// New password with the rules as a live checklist, an optional confirmation and an optional generator.
export function NewPasswordFields({ label, username, value, onChange, confirm, onConfirm, generate = false, error }) {
  const t = useT();
  const [generated, setGenerated] = useState(null);
  const [copied, setCopied] = useState(false);
  const checks = passwordChecks(value, username);
  const typed = value.length > 0;
  const mismatch = onConfirm && confirm.length > 0 && confirm !== value;
  const makeOne = () => { const pw = generatePassword(username); setGenerated(pw); setCopied(false); onChange(pw); onConfirm?.(pw); };
  const copy = async () => { try { await navigator.clipboard.writeText(generated); setCopied(true); } catch { /* the field can still be read with the eye toggle */ } };
  return (
    <>
      <PasswordInput label={label} value={value} onChange={(v) => { setGenerated(null); onChange(v); }} autoComplete="new-password" error={error} />
      {generate && (
        <div className="nx-pwgen">
          <button type="button" className="nx-btn nx-btn--sm" onClick={makeOne}><WandSparkles size={14} aria-hidden="true" />{t("pw.generate")}</button>
          {generated && generated === value && (
            <button type="button" className="nx-btn nx-btn--sm nx-btn--ghost" onClick={copy}>{copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}{t(copied ? "action.copied" : "pw.copy")}</button>
          )}
        </div>
      )}
      <ul className="nx-pwchecks" aria-label={t("pw.rules")} aria-live="polite">
        {checks.map((c) => (
          <li key={c.id} data-ok={typed ? String(c.ok) : undefined}>
            {typed && c.ok ? <Check size={13} aria-hidden="true" /> : typed ? <X size={13} aria-hidden="true" /> : <span className="nx-pwdot" aria-hidden="true" />}
            <span>{c.id === "length" && c.tooLong ? t("pw.rule.tooLong") : t(`pw.rule.${c.id}`, { n: MIN_LENGTH })}</span>
            {typed && <span className="nx-sr">{t(c.ok ? "pw.ok" : "pw.notOk")}</span>}
          </li>
        ))}
      </ul>
      {onConfirm && (
        <PasswordInput label={t("pw.confirm")} value={confirm} onChange={onConfirm} autoComplete="new-password" error={mismatch ? t("pw.mismatch") : null} />
      )}
    </>
  );
}
