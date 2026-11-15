import { useMemo, useState } from "react";
import { CircleCheck, CircleHelp, CircleX, Stethoscope } from "lucide-react";
import { diagnoseConnection } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { vmKey } from "../lib/vmId";
import { Card, Empty, Field, PageHeader } from "../components/ui";

const ICON = { pass: CircleCheck, block: CircleX, unknown: CircleHelp };
const TONE = { pass: "success", block: "danger", unknown: "warning" };
const VERDICT_TONE = { reachable: "success", blocked_by_rule: "danger", not_running: "danger", nothing_listening: "warning", guest_firewall: "warning", no_answer: "warning", unknown: "info" };

// Connection diagnosis (docs/design/console-2.md, section 9): can a VM reach another VM or an address on a port? The
// Hyperlite firewalls on the path, where both are, a real try from inside the VM and whether the target listens.
export default function DiagnosePage() {
  const t = useT();
  const vms = useInfraStore((s) => s.vms);
  const sorted = useMemo(() => [...vms].sort((a, b) => a.name.localeCompare(b.name)), [vms]);
  const [f, setF] = useState({ from: "", toKind: "vm", to: "", address: "", port: 22, protocol: "tcp" });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const fromVm = sorted.find((v) => vmKey(v) === f.from);
  const toVm = sorted.find((v) => vmKey(v) === f.to);
  const valid = fromVm && (f.toKind === "vm" ? toVm : f.address.trim()) && Number(f.port) >= 1 && Number(f.port) <= 65535;
  async function run(e) {
    e.preventDefault();
    setBusy(true); setError(null); setResult(null);
    try {
      setResult(await diagnoseConnection({ from_vm: fromVm.name, from_node: fromVm.node, to: f.toKind === "vm" ? toVm.name : f.address.trim(), to_node: f.toKind === "vm" ? toVm.node : null, port: Number(f.port), protocol: f.protocol }));
    } catch (err) { setError(errorMessage(err)); }
    setBusy(false);
  }
  const tryText = (v) => (v == null ? t("dg.try.none") : t(`dg.try.${v.split(" ")[0]}`, { detail: v.split(" ").slice(1).join(" ") }));
  return (
    <>
      <PageHeader title={t("tab.diagnose")} desc={t("dg.desc")} />
      <Card>
        <form className="hc-dg-form" onSubmit={run}>
          <Field label={t("dg.from")}>{(p) => <select {...p} className="nx-inp" value={f.from} onChange={set("from")} required><option value="">—</option>{sorted.map((v) => <option key={vmKey(v)} value={vmKey(v)}>{v.name}{v.node && v.node !== "local" ? ` (${v.node})` : ""}</option>)}</select>}</Field>
          <Field label={t("dg.toKind")}>{(p) => <select {...p} className="nx-inp" value={f.toKind} onChange={set("toKind")}><option value="vm">{t("dg.aVm")}</option><option value="address">{t("dg.anAddress")}</option></select>}</Field>
          {f.toKind === "vm"
            ? <Field label={t("dg.to")}>{(p) => <select {...p} className="nx-inp" value={f.to} onChange={set("to")} required><option value="">—</option>{sorted.filter((v) => vmKey(v) !== f.from).map((v) => <option key={vmKey(v)} value={vmKey(v)}>{v.name}{v.node && v.node !== "local" ? ` (${v.node})` : ""}</option>)}</select>}</Field>
            : <Field label={t("dg.address")}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.address} onChange={set("address")} placeholder="192.168.1.20" required />}</Field>}
          <Field label={t("dg.port")}>{(p) => <input {...p} type="number" min={1} max={65535} className="nx-inp" value={f.port} onChange={set("port")} />}</Field>
          <Field label={t("dg.protocol")}>{(p) => <select {...p} className="nx-inp" value={f.protocol} onChange={set("protocol")}><option value="tcp">TCP</option><option value="udp">UDP</option></select>}</Field>
          <button type="submit" className="nx-btn nx-btn--primary" disabled={!valid || busy}><Stethoscope size={14} aria-hidden="true" /> {busy ? t("dg.running") : t("dg.run")}</button>
        </form>
      </Card>
      {error && <p className="nx-err" role="alert">{error}</p>}
      {!result && !error && !busy && <Card><Empty icon={Stethoscope} title={t("dg.startTitle")} text={t("dg.startText")} /></Card>}
      {result && (
        <>
          <div className="nx-bn" data-tone={VERDICT_TONE[result.verdict]} role="status"><span className="nx-bn-t"><b>{t(`dg.v.${result.verdict}`)}</b> {t(`dg.vh.${result.verdict}`)}</span></div>
          <Card title={t("dg.path")}>
            <ol className="hc-dg-checks">
              {result.checks.map((c, i) => { const I = ICON[c.verdict] || CircleHelp; return (
                <li key={i} data-tone={TONE[c.verdict]}><I size={16} aria-hidden="true" /><span><b>{t(`dg.layer.${c.layer}`)}</b> · {c.detail}</span></li>
              ); })}
              {result.protocol === "tcp" && <li data-tone={result.try === "OPEN" ? "success" : result.try ? "warning" : undefined}><CircleHelp size={16} aria-hidden="true" /><span><b>{t("dg.layer.try")}</b> · {tryText(result.try)}</span></li>}
              {result.listening != null && <li data-tone={result.listening === "YES" ? "success" : "warning"}><CircleHelp size={16} aria-hidden="true" /><span><b>{t("dg.layer.listen")}</b> · {t(`dg.listen.${result.listening.split(" ")[0]}`, { port: result.port })}</span></li>}
            </ol>
          </Card>
          <Card title={t("dg.where")}>
            <dl className="hc-dcard-kv">
              <div><dt>{result.place.from.vm}</dt><dd className="nx-mono">{result.place.from.ip || t("dg.noIp")} · {result.place.from.networks.join(", ") || "—"}</dd></div>
              <div><dt>{result.place.to.vm || t("dg.anAddress")}</dt><dd className="nx-mono">{result.place.to.ip || t("dg.noIp")}{result.place.to.networks.length ? ` · ${result.place.to.networks.join(", ")}` : ""}</dd></div>
              <div><dt>{t("dg.sameNet")}</dt><dd>{result.place.same_network ? t("dg.yes") : t("dg.no")}</dd></div>
            </dl>
          </Card>
        </>
      )}
    </>
  );
}
DiagnosePage.ownHeader = true;
