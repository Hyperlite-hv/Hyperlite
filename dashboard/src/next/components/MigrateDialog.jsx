import { useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";
import { fetchMigrationCheck, migrateVM } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatSizeMb } from "../lib/format";
import CompatChecks from "../../components/CompatChecks";

const pct = (u, tot) => (u != null && tot ? (u / tot) * 100 : null);
const clamp = (v) => Math.max(0, Math.min(100, v));

// Every node but the VM's own, with what it would carry once the VM is there, most room first. A node that cannot
// take the VM stays listed with the reason, so nobody wonders where it went.
export function rankTargets(vm, nodes) {
  return nodes.filter((n) => n.id !== (vm.node || "local")).map((n) => {
    const cpu = n.cpu_utilisation ?? null;
    const mem = pct(n.used_memory_mb, n.memoire_totale_mo);
    // What the VM would add: its memory in full; its vCPUs as a share of the node's cores, at half load (a guess
    // shown as a guess: the server's diagnostic decides).
    const addMem = n.memoire_totale_mo && vm.memory_mb ? (vm.memory_mb / n.memoire_totale_mo) * 100 : null;
    const addCpu = n.cpu_coeurs ? (((vm.vcpu || 1) / n.cpu_coeurs) * 100) / 2 : null;
    const memAfter = mem != null && addMem != null ? mem + addMem : null;
    const reason = n.state !== "online" ? "mig.r.offline" : n.maintenance ? "mig.r.maintenance" : memAfter != null && memAfter > 100 ? "mig.r.noRoom" : null;
    return { node: n, cpu, mem, addCpu, addMem, memAfter, cpuAfter: cpu != null && addCpu != null ? cpu + addCpu : null, reason };
  }).sort((a, b) => (a.reason ? 1 : 0) - (b.reason ? 1 : 0) || (a.memAfter ?? 200) - (b.memAfter ?? 200) || (a.cpuAfter ?? 200) - (b.cpuAfter ?? 200));
}

function Bar({ now, add, disabled, label }) {
  return (
    <span className="nx-migbar" role="img" aria-label={label} data-disabled={disabled || undefined}>
      <span style={{ width: `${clamp(now)}%` }} data-hot={now + add >= 85 || undefined} />
      <span className="is-add" style={{ left: `${clamp(now)}%`, width: `${clamp(add)}%` }} />
    </span>
  );
}

// Live migration of one VM: the nodes ranked by the room they would have left, the compatibility diagnostic of the
// chosen one (the same read-only endpoint as before), then start. Blockers stop the button unless the admin explicitly
// chooses to ignore them; the server checks again in any case.
export default function MigrateDialog({ vm, nodes, onClose }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const refreshAll = useInfraStore((s) => s.refreshAll);
  const ranked = useMemo(() => rankTargets(vm, nodes), [vm, nodes]);
  const usable = ranked.filter((r) => !r.reason);
  const [target, setTarget] = useState(usable[0]?.node.id || "");
  const [check, setCheck] = useState(null);
  const [ignore, setIgnore] = useState(false);
  const [busy, setBusy] = useState(false);
  const first = useRef(null);
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);
  const from = nodes.find((n) => n.id === (vm.node || "local"))?.name || vm.node;

  useEffect(() => { first.current?.focus(); const el = opener.current; return () => el?.focus?.(); }, []);
  useEffect(() => {
    setIgnore(false);
    if (!target) { setCheck(null); return undefined; }
    let alive = true;
    setCheck({ loading: true });
    fetchMigrationCheck(vm.name, target, vm.node)
      .then((report) => alive && setCheck({ report }))
      .catch((e) => alive && setCheck({ error: errorMessage(e) }));
    return () => { alive = false; };
  }, [target, vm.name, vm.node]);

  const blocked = Boolean(check?.report?.summary?.blocking) && !ignore;
  const name = nodes.find((n) => n.id === target)?.name || target;
  const fmt = (v) => (v == null ? "—" : `${Math.round(v)} %`);

  async function start() {
    setBusy(true);
    try {
      await migrateVM(vm.name, target, vm.node, ignore);
      pushToast({ kind: "success", title: t("mig.started"), message: t("mig.startedMsg", { vm: vm.name, node: name }) });
      onClose();
      refreshAll?.();
    } catch (e) {
      pushToast({ kind: "error", title: t("mig.failed"), message: errorMessage(e) });
      setBusy(false);
    }
  }

  return (
    <div className="nx-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="nx-dialog nx-migdlg" role="dialog" aria-modal="true" aria-labelledby="mig-title" onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.stopPropagation(); onClose(); } }}>
        <div className="nx-migdlg-h">
          <div>
            <h2 id="mig-title">{t("mig.title", { name: vm.name })}</h2>
            <p>{t("mig.from", { node: from, vcpu: vm.vcpu, mem: formatSizeMb(vm.memory_mb, lang) })}</p>
          </div>
          <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" aria-label={t("action.close")} onClick={onClose} disabled={busy}><X size={18} aria-hidden="true" /></button>
        </div>
        <div className="nx-migdlg-b">
          <p className="nx-muted" style={{ margin: 0 }}>{t("mig.help")}</p>
          <fieldset className="nx-migset">
            <legend>{t("mig.target")} <span className="nx-muted">· {t("mig.ranked")}</span></legend>
            {ranked.length === 0 && <p className="nx-muted" role="status" style={{ margin: 0 }}>{t("mig.noTarget")}</p>}
            {ranked.map((r, i) => {
              const id = r.node.id;
              const off = Boolean(r.reason);
              const best = !off && r === usable[0];
              const warn = !off && (r.memAfter ?? 0) >= 85;
              return (
                <label key={id} className={`nx-migopt${target === id ? " is-on" : ""}${off ? " is-off" : ""}`}>
                  <input ref={best ? first : undefined} type="radio" name="mig-target" value={id} checked={target === id} disabled={off || busy} onChange={() => setTarget(id)} aria-describedby={`mig-v-${i}`} />
                  <span className="nx-migopt-nm">{r.node.name}</span>
                  <span className="nx-migopt-m">
                    <span>CPU</span><Bar now={r.cpu ?? 0} add={r.addCpu ?? 0} disabled={off} label={`CPU ${fmt(r.cpuAfter)}`} /><span>{fmt(r.cpuAfter)}</span>
                    <span>RAM</span><Bar now={r.mem ?? 0} add={r.addMem ?? 0} disabled={off} label={`RAM ${fmt(r.memAfter)}`} /><span>{fmt(r.memAfter)}</span>
                  </span>
                  <span id={`mig-v-${i}`} className="nx-migopt-v" data-tone={off ? "danger" : best ? "success" : warn ? "warning" : undefined}>
                    {off ? t(r.reason) : best ? t("mig.best") : warn ? t("mig.tight", { p: Math.round(r.memAfter) }) : t("mig.possible")}
                  </span>
                </label>
              );
            })}
            {ranked.length > 0 && <small className="nx-muted">{t("mig.barHelp", { name: vm.name })}</small>}
          </fieldset>
          {check?.loading && <p className="nx-muted" role="status" style={{ margin: 0 }}>{t("mig.checking")}</p>}
          {check?.error && <p className="nx-notice nx-notice--warning" role="status" style={{ margin: 0 }}>{t("mig.checkFailed", { error: check.error })}</p>}
          {check?.report && <CompatChecks report={check.report} labels={{ blocking: t("cc.blocking"), warning: t("cc.warning"), ok: t("cc.ok"), allOk: t("cc.allOk"), action: t("cc.action"), toggle: (show, n) => t(show ? "cc.hideOk" : "cc.showOk", { n }) }} />}
          {check?.report?.summary?.blocking && (
            <label className="nx-check"><input type="checkbox" checked={ignore} onChange={(e) => setIgnore(e.target.checked)} disabled={busy} /> {t("mig.ignore")}</label>
          )}
        </div>
        <div className="nx-dialog-actions nx-migdlg-f">
          <button type="button" className="nx-btn" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
          <button type="button" className="nx-btn nx-btn--primary" disabled={!target || busy || check?.loading || blocked} onClick={start}>
            {busy ? t("mig.starting") : target ? t("mig.goTo", { node: name }) : t("mig.go")}
          </button>
        </div>
      </div>
    </div>
  );
}
