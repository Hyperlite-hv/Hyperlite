import { useState } from "react";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { EmptyState } from "../components/States";

// Console launcher. Both connections open in a separate window so this page stays usable. The graphical
// console (VNC) follows the backend rule (administrators and read-only observers); the SSH terminal is a
// root-equivalent shell in the guest and is reserved for administrators, which the backend enforces too.
export default function VmConsole({ resource: vm }) {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const [mode, setMode] = useState("vnc");
  if (!vm) return null;
  const running = vm.etat === "actif";
  const terminal = mode === "terminal";

  function openWindow() {
    // Stable window name per VM + mode: clicking again refocuses the same window.
    window.open(`/console/${encodeURIComponent(vm.nom)}?mode=${mode}`, `hyperlite-console-${vm.nom}-${mode}`, "width=1100,height=750,noopener");
  }

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="vc-title">
        <div className="nx-cardhead">
          <h2 id="vc-title">{t("tab.console")}</h2>
          <div className="nx-seg" role="group" aria-label={t("vc.mode")}>
            <button type="button" aria-pressed={!terminal} onClick={() => setMode("vnc")}>{t("vc.vnc")}</button>
            <button type="button" aria-pressed={terminal} onClick={() => setMode("terminal")}>{t("vc.ssh")}</button>
          </div>
        </div>
        {terminal && !caps.admin ? (
          <EmptyState title={t("vc.adminOnlyTitle")} help={t("vc.adminOnlyHelp")} />
        ) : (
          <>
            <p style={{ marginTop: 0, maxWidth: "62ch" }}>{terminal ? t("vc.sshHelp") : t("vc.vncHelp")}</p>
            {terminal && <p className="nx-notice nx-notice--warning" role="note">{t("vc.sshWarn")}</p>}
            {!running && <p className="nx-notice" role="status">{t("vc.mustRun")}</p>}
            <div><button type="button" className="nx-btn nx-btn--primary" disabled={!running} onClick={openWindow}>{t("nn.openWindow")}</button></div>
          </>
        )}
      </section>
    </div>
  );
}
