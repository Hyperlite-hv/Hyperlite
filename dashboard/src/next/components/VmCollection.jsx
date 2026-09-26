import { useMemo, useState } from "react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { capabilities, vmActionState } from "../lib/capabilities";
import { useVmActions } from "../lib/vmActions";
import { formatSizeMb, formatUptimeLong } from "../lib/format";
import StatusIndicator from "./StatusIndicator";

const VIEW_KEY = "hyperlite-next-vmview";
const PROBLEM = new Set(["plante", "bloque", "inconnu"]);

function readView() { try { return localStorage.getItem(VIEW_KEY) || "auto"; } catch { return "auto"; } }

export function VmActions({ vm }) {
  const t = useT();
  const role = useAuthStore((s) => s.role);
  const caps = capabilities(role);
  const { run, openConsole } = useVmActions();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const running = vm.etat === "actif";
  const power = vmActionState(running ? "stop" : "start", vm, caps);
  const cons = vmActionState("console", vm, caps);
  return (
    <span style={{ display: "inline-flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
      <button type="button" className="nx-btn" aria-disabled={!cons.enabled || undefined} title={!cons.enabled ? t(cons.reason) : undefined} onClick={() => cons.enabled && openConsole(vm)}>{t("menu.console")}</button>
      <button type="button" className="nx-btn" aria-disabled={!power.enabled || undefined} title={!power.enabled ? t(power.reason) : undefined} onClick={() => power.enabled && run(vm, running ? "stop" : "start")}>{running ? t("menu.stop") : t("menu.start")}</button>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo("vm", vm.nom, "summary")}>{t("menu.open")}</button>
    </span>
  );
}

// VM collection shared by the node summary and the "Virtual machines" page: filter, state chips,
// sortable table (default) or cards, with the same actions everywhere.
export default function VmCollection({ vms, showNode = false, title, headingId = "vm-collection", action = null }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const nodes = useInfraStore((s) => s.nodes);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const [view, setViewState] = useState(readView);
  const [q, setQ] = useState("");
  const [chip, setChip] = useState("all");
  const [sort, setSort] = useState({ key: "state", dir: 1 });
  const setView = (v) => { setViewState(v); try { localStorage.setItem(VIEW_KEY, v); } catch { /* preference only */ } };
  const nodeName = (id) => nodes.find((n) => n.id === id)?.nom || id;

  const counts = useMemo(() => ({
    all: vms.length,
    running: vms.filter((v) => v.etat === "actif").length,
    stopped: vms.filter((v) => v.etat === "arrete" || v.etat === "en_arret").length,
    problems: vms.filter((v) => PROBLEM.has(v.etat)).length,
  }), [vms]);

  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    let list = vms.filter((v) => (chip === "all" || (chip === "running" && v.etat === "actif") || (chip === "stopped" && (v.etat === "arrete" || v.etat === "en_arret")) || (chip === "problems" && PROBLEM.has(v.etat)))
      && (!n || `${v.nom} ${v.ip || ""} ${v.os || ""} ${nodeName(v.node)}`.toLowerCase().includes(n)));
    const rank = (v) => (PROBLEM.has(v.etat) ? 0 : v.etat === "actif" ? 1 : 2); // problems first
    const val = { state: rank, name: (v) => v.nom.toLowerCase(), node: (v) => nodeName(v.node).toLowerCase(), vcpu: (v) => v.vcpu || 0, mem: (v) => v.memoire_mo || 0, uptime: (v) => v.uptime_s || 0 }[sort.key];
    list = [...list].sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : a.nom.localeCompare(b.nom)) * sort.dir);
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vms, q, chip, sort, nodes]);

  const mode = view === "auto" ? "table" : view; // a dense table by default; cards stay one click away
  const th = (key, label, cls) => (
    <th scope="col" className={cls} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button type="button" className="nx-thbtn" onClick={() => setSort((s) => ({ key, dir: s.key === key ? -s.dir : 1 }))}>{label}{sort.key === key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}</button>
    </th>
  );
  const ipCell = (vm) => (vm.ip ? vm.ip : <span className="nx-muted" title={t("ns.ipHelp")}>{t("ns.ipNa")} ⓘ</span>);

  return (
    <section className="nx-card" aria-labelledby={headingId}>
      <div className="nx-cardhead">
        <h2 id={headingId}>{title || t("ns.vms")} <span className="nx-count">{vms.length}</span></h2>
        <input className="nx-input" type="search" aria-label={t("ns.filterVms")} placeholder={t("ns.filterVms")} value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="nx-seg" role="group" aria-label={t("ns.viewMode")}>
          <button type="button" aria-pressed={mode === "cards"} onClick={() => setView("cards")}>{t("ns.cards")}</button>
          <button type="button" aria-pressed={mode === "table"} onClick={() => setView("table")}>{t("ns.table")}</button>
        </div>
        {action}
      </div>
      <div className="nx-chips" role="group" aria-label={t("vmlist.filterState")}>
        {[["all", t("vmlist.all")], ["running", t("state.running")], ["stopped", t("state.stopped")], ["problems", t("vmlist.problems")]].map(([id, label]) => (
          <button key={id} type="button" className="nx-chip" aria-pressed={chip === id} onClick={() => setChip(id)}>{label} <span className="nx-mono">{counts[id]}</span></button>
        ))}
      </div>
      {vms.length === 0 ? <p className="nx-muted" role="status">{t("ns.noVms")}</p>
        : shown.length === 0 ? <p className="nx-muted" role="status">{t("find.none", { q: q || t(`vmlist.${chip === "problems" ? "problems" : chip === "all" ? "all" : "all"}`) })}</p>
        : mode === "cards" ? (
          <div className="nx-cards">
            {shown.map((vm) => (
              <article key={`${vm.node}:${vm.nom}`} className="nx-vmcard" aria-label={vm.nom}>
                <header><button type="button" className="nx-link" onClick={() => navigateTo("vm", vm.nom, "summary")}>{vm.nom}</button><StatusIndicator kind="vm" wire={vm.etat} />{vm.uptime_s ? <span className="nx-muted"> · {formatUptimeLong(vm.uptime_s, lang)}</span> : null}</header>
                <p className="nx-mono nx-muted">{t("ns.vmSpec", { cpu: vm.vcpu, ram: formatSizeMb(vm.memoire_mo, lang) })} · {ipCell(vm)}{showNode ? ` · ${nodeName(vm.node)}` : ""}</p>
                <VmActions vm={vm} />
              </article>
            ))}
          </div>
        ) : (
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr>{th("state", t("ns.col.state"))}{th("name", t("ns.col.name"))}{showNode && th("node", t("ns.node"))}{th("vcpu", "vCPU", "nx-num")}{th("mem", t("ns.memory"), "nx-num")}<th scope="col">IP</th>{th("uptime", t("ns.col.uptime"))}<th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {shown.map((vm) => (
                  <tr key={`${vm.node}:${vm.nom}`}>
                    <td><StatusIndicator kind="vm" wire={vm.etat} /></td>
                    <th scope="row"><button type="button" className="nx-link" onClick={() => navigateTo("vm", vm.nom, "summary")}>{vm.nom}</button></th>
                    {showNode && <td className="nx-mono">{nodeName(vm.node)}</td>}
                    <td className="nx-num nx-mono">{vm.vcpu}</td><td className="nx-num nx-mono">{formatSizeMb(vm.memoire_mo, lang)}</td>
                    <td className="nx-mono">{ipCell(vm)}</td>
                    <td className="nx-mono">{formatUptimeLong(vm.uptime_s, lang) || "—"}</td>
                    <td><VmActions vm={vm} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </section>
  );
}
