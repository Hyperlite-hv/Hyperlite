import LoadingState from "./LoadingState";
import { confirmAction } from "../store/useConfirmStore";
import { useEffect, useState } from "react";
import { ShieldCheck, ShieldOff, KeyRound, Plus, Trash2, Copy, Check } from "lucide-react";
import { useAuthStore } from "../store/useAuthStore";
import { useInfraStore } from "../store/useInfraStore";
import {
  setup2FA, confirm2FA, disable2FA, fetchApiTokens, createApiToken, deleteApiToken,
} from "../api/client";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

// Self-service panel opened from the user menu (Header.jsx), not a Datacenter
// tab: these are settings of the signed-in ACCOUNT, not of the managed
// infrastructure.
export default function AccountSecurityModal({ open, onClose }) {
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
      pushToast({ kind: "error", title: "Two-factor setup failed", message: e.message });
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
  // Guarded on `open`: see the note in VMWizard.jsx — this component now stays
  // mounted while closed, so without the guard it would hit the backend on
  // every page load instead of only when the modal is actually opened.
  useEffect(() => { if (open) reloadTokens(); }, [open]);

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
    if (!(await confirmAction({ title: "Please confirm", message: `Revoke the token '${t.name}'? Any script using it will immediately lose access.`, confirmLabel: "Confirm" }))) return;
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
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="w-[560px] max-w-full max-h-[85vh] overflow-y-auto space-y-6">
        <DialogHeader>
          <DialogTitle>Account security</DialogTitle>
        </DialogHeader>

        {/* --- 2FA --- */}
        <section className="space-y-3">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <ShieldCheck size={15} /> Two-factor authentication (TOTP)
          </h4>

          {totpEnabled && !setupData && (
            <div className="space-y-3">
              <p className="text-sm text-status-running">2FA is enabled on this account.</p>
              <form onSubmit={handleDisable} className="flex items-end gap-2">
                <div className="flex-1">
                  <Label className="text-xs font-medium text-foreground/80">Password (to disable)</Label>
                  <Input aria-label="Password (to disable)" type="password" className="mt-1" required value={disablePassword} onChange={(e) => setDisablePassword(e.target.value)} />
                </div>
                <Button type="submit" disabled={busy2fa} variant="outline" className="text-status-error border-status-error/30 hover:bg-status-error/10">
                  <ShieldOff /> Disable
                </Button>
              </form>
            </div>
          )}

          {!totpEnabled && !setupData && (
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">Not enabled: protects sign-in with a one-time code in addition to the password.</p>
              <Button onClick={handleStartSetup} disabled={busy2fa} className="shrink-0">
                {busy2fa ? "..." : "Enable"}
              </Button>
            </div>
          )}

          {setupData && (
            <form onSubmit={handleConfirm} className="space-y-3 rounded-md border border-border p-3 animate-in fade-in-0 duration-150">
              <p className="text-xs text-foreground/80">
                Scan this QR code with an authenticator app (Google Authenticator, Aegis, 1Password...), then enter the generated code to confirm.
              </p>
              <div
                className="mx-auto w-40 rounded-md bg-white p-2 [&_svg]:w-full [&_svg]:h-full"
                dangerouslySetInnerHTML={{ __html: setupData.qr_code_svg }}
              />
              <p className="text-center font-mono text-[11px] text-muted-foreground break-all">{setupData.secret}</p>
              <div className="flex items-end gap-2">
                <div className="flex-1">
                  <Label className="text-xs font-medium text-foreground/80">6-digit code</Label>
                  <Input aria-label="6-digit code"
                    className="mt-1 text-center tracking-[0.3em]" autoFocus inputMode="numeric" maxLength={6}
                    value={confirmCode} onChange={(e) => setConfirmCode(e.target.value.replace(/\D/g, ""))}
                  />
                </div>
                <Button type="submit" disabled={busy2fa || confirmCode.length !== 6}>Confirm</Button>
                <Button type="button" variant="secondary" onClick={() => { setSetupData(null); setConfirmCode(""); }}>Cancel</Button>
              </div>
            </form>
          )}
        </section>

        {/* --- Jetons API --- */}
        <section className="space-y-3 border-t border-border pt-4">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <KeyRound size={15} /> API tokens
          </h4>
          <p className="text-xs text-muted-foreground">
            To authenticate scripts or automation (Terraform, cron...) without using your password. Each token can be revoked individually.
          </p>

          {freshToken && (
            <div className="space-y-2 rounded-md border border-status-warning/40 bg-status-warning/10 p-3 animate-in fade-in-0 duration-150">
              <p className="text-xs text-foreground/90">
                Copy this token now: it will never be shown again.
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 truncate rounded-sm bg-background px-2 py-1.5 text-xs text-foreground">{freshToken.token}</code>
                <Button variant="secondary" size="sm" onClick={copyToken}>
                  {copied ? <Check size={13} /> : <Copy size={13} />}
                </Button>
              </div>
              <button className="text-xs text-muted-foreground transition-colors duration-150 hover:text-foreground/90" onClick={() => setFreshToken(null)}>Dismiss</button>
            </div>
          )}

          <form onSubmit={handleCreateToken} className="flex items-end gap-2">
            <div className="flex-1">
              <Label className="text-xs font-medium text-foreground/80">Token name</Label>
              <Input aria-label="Token name" className="mt-1" placeholder="e.g. Terraform prod" value={newTokenName} onChange={(e) => setNewTokenName(e.target.value)} />
            </div>
            <Button type="submit" disabled={busyToken || !newTokenName.trim()} variant="secondary">
              <Plus /> Create
            </Button>
          </form>

          <div className="divide-y divide-border rounded-md border border-border">
            {tokens == null && <div className="px-3 py-2 text-xs text-muted-foreground"><LoadingState /></div>}
            {tokens && tokens.length === 0 && <div className="px-3 py-3 text-xs text-muted-foreground text-center">No tokens.</div>}
            {tokens && tokens.map((t) => (
              <div key={t.id} className="flex items-center gap-3 px-3 py-2 text-sm transition-colors duration-150 hover:bg-muted/40">
                <div className="min-w-0 flex-1">
                  <div className="text-foreground truncate">{t.name}</div>
                  <div className="text-muted-foreground text-xs">
                    Created on {new Date(t.created_at).toLocaleDateString()} · {t.last_used_at ? `last used on ${new Date(t.last_used_at).toLocaleDateString()}` : "never used"}
                  </div>
                </div>
                <Button aria-label={`Revoke token ${t.name}`} size="icon" variant="outline" className="size-7 text-status-error border-status-error/30 hover:bg-status-error/10" onClick={() => handleDeleteToken(t)}><Trash2 size={13} /></Button>
              </div>
            ))}
          </div>
        </section>
      </DialogContent>
    </Dialog>
  );
}
