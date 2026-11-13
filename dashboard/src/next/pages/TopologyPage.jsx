import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Box, Database, Monitor, Server } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { useFreshness } from "../lib/inventory";
import { formatSizeGb, formatSizeMb } from "../lib/format";
import { vmKey } from "../lib/vmId";
import StatusIndicator from "../components/StatusIndicator";
import { PageHeader } from "../components/ui";
import { FilterPill } from "../components/portal";

const pct = (used, total) => (total > 0 ? Math.min(100, Math.round((used / total) * 100)) : null);
const at = (x) => x.node || "local";

function Bar({ value, label }) {
  if (value == null) return null;
  return <span className="hc-cap-bar hc-topo-bar" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} data-tone={value >= 90 ? "danger" : value >= 75 ? "warning" : undefined}><span style={{ width: `${value}%` }} /></span>;
}

// Topology: the cluster, its nodes, and on each node what runs and where it is stored, in one view. Every item opens
// its own page.
export default function TopologyPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { nodes, vms, pools, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, pools: s.storagePools, navigateTo: s.navigateTo })));
  const { containers, cluster } = useFreshness(useShallow((s) => ({ containers: s.containers, cluster: s.cluster })));
  const [show, setShow] = useState("all");
  const keep = (state) => show === "all" || (show === "running" ? state === "active" : state !== "active");
  const machines = vms.filter((v) => !v.template);

  return (
    <>
      <PageHeader title={t("tab.topology")} desc={t("topo.desc")} />
      <div className="hc-fbar">
        <FilterPill label={t("topo.show")} value={show} onChange={setShow} options={[{ value: "all", label: t("list.all") }, { value: "running", label: t("topo.running") }, { value: "stopped", label: t("topo.stopped") }]} />
      </div>
      <div className="hc-topo">
        <div className="hc-topo-root">
          <b>{cluster?.in_cluster ? cluster.name : t("crumb.datacenter")}</b>
          <small>{t("topo.summary", { nodes: nodes.length, vms: machines.length, cts: containers.length })}</small>
        </div>
        <ul className="hc-topo-nodes">
          {nodes.map((n) => {
            const mine = machines.filter((v) => at(v) === n.id && keep(v.state));
            const cts = containers.filter((c) => at(c) === n.id && keep(c.state));
            const nodePools = pools.filter((p) => at(p) === n.id);
            const off = n.state !== "online";
            return (
              <li key={n.id} className="hc-topo-node" data-off={off || undefined}>
                <button type="button" className="hc-topo-head" onClick={() => navigateTo("node", n.id, "summary")}>
                  <Server size={16} aria-hidden="true" /><b>{n.name}</b><StatusIndicator kind="node" wire={n.state} compact />
                </button>
                {!off && <div className="hc-topo-load">
                  <span>{t("ns.cpu")} {n.cpu_utilisation != null ? `${Math.round(n.cpu_utilisation)} %` : "—"}</span><Bar value={n.cpu_utilisation != null ? Math.round(n.cpu_utilisation) : null} label={`${n.name} ${t("ns.cpu")}`} />
                  <span>{t("ns.memory")} {formatSizeMb(n.used_memory_mb, lang)}</span><Bar value={pct(n.used_memory_mb, n.memoire_totale_mo)} label={`${n.name} ${t("ns.memory")}`} />
                </div>}
                <section aria-label={t("topo.vmsOf", { node: n.name })}>
                  <h3><Monitor size={13} aria-hidden="true" />{t("nav.vms")} <span>{mine.length}</span></h3>
                  {mine.length === 0 ? <p className="nx-muted">{t("topo.none")}</p> : (
                    <ul className="hc-topo-items">{mine.map((v) => (
                      <li key={vmKey(v)}><button type="button" onClick={() => navigateTo("vm", vmKey(v), "summary")} title={`${v.name} · ${v.vcpu || "?"} vCPU · ${formatSizeMb(v.memory_mb, lang)}`}>
                        <StatusIndicator kind="vm" wire={v.state} compact /><span>{v.name}</span>
                      </button></li>
                    ))}</ul>
                  )}
                </section>
                {cts.length > 0 && <section aria-label={t("topo.ctsOf", { node: n.name })}>
                  <h3><Box size={13} aria-hidden="true" />{t("tab.containers")} <span>{cts.length}</span></h3>
                  <ul className="hc-topo-items">{cts.map((c) => (
                    <li key={c.name}><button type="button" onClick={() => navigateTo("container", c.name, "summary")}>
                      <StatusIndicator kind="vm" wire={c.state === "active" ? "active" : "stopped"} compact /><span>{c.name}</span>
                    </button></li>
                  ))}</ul>
                </section>}
                {nodePools.length > 0 && <section aria-label={t("topo.poolsOf", { node: n.name })}>
                  <h3><Database size={13} aria-hidden="true" />{t("tab.storage")} <span>{nodePools.length}</span></h3>
                  <ul className="hc-topo-pools">{nodePools.map((p) => {
                    const used = p.capacity_gb ? p.capacity_gb - (p.available_gb ?? p.capacity_gb) : null;
                    return (
                      <li key={p.name}><button type="button" onClick={() => navigateTo("datacenter", null, "storage")}>
                        <span>{p.name}</span><small>{used != null ? `${formatSizeGb(used, lang)} / ${formatSizeGb(p.capacity_gb, lang)}` : "—"}</small>
                        <Bar value={pct(used, p.capacity_gb)} label={`${p.name} ${t("stor.usage")}`} />
                      </button></li>
                    );
                  })}</ul>
                </section>}
              </li>
            );
          })}
        </ul>
      </div>
    </>
  );
}
TopologyPage.ownHeader = true;
