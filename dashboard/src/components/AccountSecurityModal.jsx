import { useEffect, useState } from "react";
import { X, ShieldCheck, ShieldOff, KeyRound, Plus, Trash2, Copy, Check } from "lucide-react";
import { useAuthStore } from "../store/useAuthStore";
import { useInfraStore } from "../store/useInfraStore";
import {
  setup2FA, confirm2FA, disable2FA, fetchApiTokens, createApiToken, deleteApiToken,
} from "../api/client";

// Self-service panel opened from the user menu (Header.jsx), not a Datacenter
// tab: these are settings of the signed-in ACCOUNT, not of the managed
// infrastructure.
export default function AccountSecurityModal({ onClose }) {
  const totpEnabled = useAuthStore((s) => s.totpEnabled);
  const refreshMe = useAuthStore((s) => s.refreshMe);
  const pushToast = useInfraStore((s) => s.pushToast);

  // --- 2FA ---
  const [setupData, setSetupData] = useState(null); // { secret, qr_code_svg }
  const [confirmCode, setConfirmCode] = useState("");
  const [disablePassword, setDisablePassword] = useState("");
  const [busy2fa, setBusy2fa] = useState(false);

  async function handleStartSetup() {
    setBusy2fa(true);
    try {
      setSetupData(await setup2FA());
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally {
      setBusy2fa(false);
    }
  }

  async function handleConfirm(e) {
    e.preventDefault();
    setBusy2fa(true);
    try {
      await confirm2FA(confirmCode);
      pushToast({ kind: "success", title: "2FA enabled", message: "A code will now be required at every sign-in." });
      setSetupData(null);
      setConfirmCode("");
      await refreshMe();
    } catch (e) {
      pushToast({ kind: "error", title: "Invalid code", message: e.message });
    } finally {
      setBusy2fa(false);
    }
  }

  async function handleDisable(e) {
    e.preventDefault();
    setBusy2fa(true);
    try {
      await disable2FA(disablePassword);
      pushToast({ kind: "success", title: "2FA disabled", message: "" });
      setDisablePassword("");
      await refreshMe();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    } finally {
      setBusy2fa(false);
    }
  }

  // --- Jetons API ---
  const [tokens, setTokens] = useState(null);
  const [newTokenName, setNewTokenName] = useState("");
  const [freshToken, setFreshToken] = useState(null); // { id, name, token } -- displayed ONCE
  const [copied, setCopied] = useState(false);
  const [busyToken, setBusyToken] = useState(false);

  const reloadTokens = () => fetchApiTokens().then(setTokens).catch(() => {});
  useEffect(() => { reloadTokens(); }, []);

  async function handleCreateToken(e) {
    e.preventDefault();
    if (!newTokenName.trim()) return;
    setBusyToken(true);
    try {
      const created = await createApiToken(newTokenName.trim());
      setFreshToken(created);
      setNewTokenName("");
      reloadTokens();
    } catch (e) {
      pushToast({ kind: "error", title: "Creation failed", message: e.message });
    } finally {
      setBusyToken(false);
    }
  }

  async function handleDeleteToken(t) {
    if (!window.confirm(`Revoke the token '${t.name}'? Any script using it will immediately lose access.`)) return;
    try {
      await deleteApiToken(t.id);
      pushToast({ kind: "success", title: "Token revoked", message: t.name });
      reloadTokens();
    } catch (e) {
      pushToast({ kind: "error", title: "Failed", message: e.message });
    }
  }

  function copyToken() {
    navigator.clipboard?.writeText(freshToken.token).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-[560px] max-w-full max-h-[85vh] overflow-y-auto p-5 space-y-6" role="dialog" aria-modal="true" aria-label="Account security">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-anthracite-100">Account security</h3>
          <button aria-label="Close" onClick={onClose} className="text-anthracite-400 hover:text-anthracite-100"><X size={18} /></button>
        </div>

        {/* --- 2FA --- */}
        <section className="space-y-3">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-anthracite-100">
            <ShieldCheck size={15} /> Two-factor authentication (TOTP)
          </h4>

          {totpEnabled && !setupData && (
            <div className="space-y-3">
              <p className="text-sm text-status-running">2FA is enabled on this account.</p>
              <form onSubmit={handleDisable} className="flex items-end gap-2">
                <div className="flex-1">
                  <label className="text-xs font-medium text-anthracite-300">Password (to disable)</label>
                  <input type="password" className="input mt-1" required value={disablePassword} onChange={(e) => setDisablePassword(e.target.value)} />
                </div>
                <button type="submit" disabled={busy2fa} className="btn-danger">
                  <ShieldOff size={14} /> Disable
                </button>
              </form>
            </div>
          )}

          {!totpEnabled && !setupData && (
            <div className="flex items-center justify-between">
              <p className="text-sm text-anthracite-400">Not enabled: protects sign-in with a one-time code in addition to the password.</p>
              <button onClick={handleStartSetup} disabled={busy2fa} className="btn-primary shrink-0">
                {busy2fa ? "..." : "Enable"}
              </button>
            </div>
          )}

          {setupData && (
            <form onSubmit={handleConfirm} className="space-y-3 rounded-md border border-anthracite-600 p-3">
              <p className="text-xs text-anthracite-300">
                Scan this QR code with an authenticator app (Google Authenticator, Aegis, 1Password...), then enter the generated code to confirm.
              </p>
              <div
                className="mx-auto w-40 rounded-md bg-white p-2 [&_svg]:w-full [&_svg]:h-full"
                dangerouslySetInnerHTML={{ __html: setupData.qr_code_svg }}
              />
              <p className="text-center font-mono text-[11px] text-anthracite-500 break-all">{setupData.secret}</p>
              <div className="flex items-end gap-2">
                <div className="flex-1">
                  <label className="text-xs font-medium text-anthracite-300">6-digit code</label>
                  <input
                    className="input mt-1 text-center tracking-[0.3em]" autoFocus inputMode="numeric" maxLength={6}
                    value={confirmCode} onChange={(e) => setConfirmCode(e.target.value.replace(/\D/g, ""))}
                  />
                </div>
                <button type="submit" disabled={busy2fa || confirmCode.length !== 6} className="btn-primary">Confirm</button>
                <button type="button" className="btn-secondary" onClick={() => { setSetupData(null); setConfirmCode(""); }}>Cancel</button>
              </div>
            </form>
          )}
        </section>

        {/* --- Jetons API --- */}
        <section className="space-y-3 border-t border-anthracite-600 pt-4">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-anthracite-100">
            <KeyRound size={15} /> API tokens
          </h4>
          <p className="text-xs text-anthracite-400">
            To authenticate scripts or automation (Terraform, cron...) without using your password. Each token can be revoked individually.
          </p>

          {freshToken && (
            <div className="space-y-2 rounded-md border border-status-warning/40 bg-status-warning/10 p-3">
              <p className="text-xs text-anthracite-200">
                Copy this token now: it will never be shown again.
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 truncate rounded bg-anthracite-900 px-2 py-1.5 text-xs text-anthracite-100">{freshToken.token}</code>
                <button className="btn-secondary !py-1.5" onClick={copyToken}>
                  {copied ? <Check size={13} /> : <Copy size={13} />}
                </button>
              </div>
              <button className="text-xs text-anthracite-400 hover:text-anthracite-200" onClick={() => setFreshToken(null)}>Close</button>
            </div>
          )}

          <form onSubmit={handleCreateToken} className="flex items-end gap-2">
            <div className="flex-1">
              <label className="text-xs font-medium text-anthracite-300">Token name</label>
              <input className="input mt-1" placeholder="e.g. Terraform prod" value={newTokenName} onChange={(e) => setNewTokenName(e.target.value)} />
            </div>
            <button type="submit" disabled={busyToken || !newTokenName.trim()} className="btn-secondary">
              <Plus size={14} /> Create
            </button>
          </form>

          <div className="divide-y divide-anthracite-600 rounded-md border border-anthracite-600">
            {tokens == null && <div className="px-3 py-2 text-xs text-anthracite-400">Loading...</div>}
            {tokens && tokens.length === 0 && <div className="px-3 py-3 text-xs text-anthracite-400 text-center">No tokens.</div>}
            {tokens && tokens.map((t) => (
              <div key={t.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <div className="text-anthracite-100 truncate">{t.name}</div>
                  <div className="text-anthracite-500 text-xs">
                    Created on {new Date(t.created_at).toLocaleDateString()} · {t.last_used_at ? `last used on ${new Date(t.last_used_at).toLocaleDateString()}` : "never used"}
                  </div>
                </div>
                <button aria-label="Delete" className="btn-danger !py-1" onClick={() => handleDeleteToken(t)}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
