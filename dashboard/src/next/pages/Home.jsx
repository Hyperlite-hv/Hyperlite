import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { ArrowRight, Plus } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { useFreshness } from "../lib/inventory";
import { formatSizeGb, formatSizeMb, relativeTime } from "../lib/format";
import { taskLabel } from "../lib/enums";
import { findVm } from "../lib/vmId";
import { LIST_OF, allowedServices, serviceLabel, useConsoleStore } from "../lib/services";
import ServiceTile from "../components/ServiceTile";
import StatusIndicator from "../components/StatusIndicator";
import { useAlerts, useRunningOps } from "../layout/TopBar";
import { useApiDocsAccess } from "../layout/SideMenu";

const HOME_SERVICES = ["vms", "containers", "nodes", "storage", "network", "backups", "perf", "permissions"];
const pct = (used, total) => (total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0);

function Capacity({ label, text, value }) {
  return (
    <div className="hc-cap" data-tone={value >= 90 ? "danger" : value >= 75 ? "warning" : undefined}>
      <div className="hc-cap-row"><b>{label}</b><span>{text}</span></div>
      <span className="hc-cap-bar" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value}><span style={{ width: `${value}%` }} /></span>
    </div>
  );
}

// Home: the services, the resources opened recently or pinned, then the state of the cluster, what needs
// attention and what is running.
export default function Home() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { nodes, vms, storagePools, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, storagePools: s.storagePools, navigateTo: s.navigateTo })));
  const { containers, cluster } = useFreshness(useShallow((s) => ({ containers: s.containers, cluster: s.cluster })));
  const caps = capabilities(useAuthStore((s) => s.role));
  const apiDocs = useApiDocsAccess(caps.admin);
  const { recent, favorites } = useConsoleStore(useShallow((s) => ({ recent: s.recent, favorites: s.favorites })));
  const alerts = useAlerts();
  const ops = useRunningOps();
  const [tab, setTab] = useState("recent");
  const allowed = new Set(allowedServices({ admin: caps.admin, apiDocs }).map((s) => s.id));
  const go = (id) => navigateTo("datacenter", null, id);

  const online = nodes.filter((n) => n.state === "online");
  const critical = alerts.some((a) => a.level === "danger");
  const health = critical ? "danger" : alerts.length ? "warning" : "ok";
  const cores = online.reduce((s, n) => s + (n.cpu_coeurs || 0), 0);
  const vcpu = vms.filter((v) => v.state === "active").reduce((s, v) => s + (v.vcpu || 0), 0);
  const memTotal = online.reduce((s, n) => s + (n.memoire_totale_mo || 0), 0);
  const memUsed = online.reduce((s, n) => s + (n.used_memory_mb || 0), 0);
  const capGb = storagePools.reduce((s, p) => s + (p.capacity_gb || 0), 0);
  const usedGb = storagePools.reduce((s, p) => s + Math.max(0, (p.capacity_gb || 0) - (p.available_gb ?? p.capacity_gb ?? 0)), 0);

  // A recently opened resource that still exists, with its current state.
  const live = (r) => {
    if (r.type === "vm") { const v = findVm(vms, r.id); return v && { name: v.name, kind: "vm", wire: v.state }; }
    if (r.type === "node") { const n = nodes.find((x) => x.id === r.id); return n && { name: n.name, kind: "node", wire: n.state }; }
    const c = containers.find((x) => x.name === r.id); return c && { name: c.name, kind: "vm", wire: c.state === "active" ? "active" : "stopped" };
  };
  const rows = recent.map((r) => ({ r, l: live(r) })).filter((x) => x.l);
  const favs = favorites.filter((id) => allowed.has(id));

  return (
    <div className="hc-home">
      <div className="hc-home-main">
        <section>
          <h1>{t("home.services")}</h1>
          <div className="hc-svcs">
            {caps.create && <button type="button" className="hc-svc" onClick={() => go("create")}><span className="hc-tile hc-tile--ghost" style={{ "--s": "2.6667rem" }}><Plus size={24} aria-hidden="true" /></span><span>{t("svc.create")}</span></button>}
            {HOME_SERVICES.filter((id) => allowed.has(id)).map((id) => (
              <button key={id} type="button" className="hc-svc" onClick={() => go(id)}><ServiceTile id={id} size={40} /><span>{t(serviceLabel(id))}</span></button>
            ))}
            <button type="button" className="hc-svc" onClick={() => go("services")}><span className="hc-tile hc-tile--ghost" style={{ "--s": "2.6667rem" }}><ArrowRight size={24} aria-hidden="true" /></span><span>{t("svc.services")}</span></button>
          </div>
        </section>

        <section>
          <h2>{t("home.resources")}</h2>
          <div className="nx-tabs nx-tabs--page" role="tablist" aria-label={t("home.resources")}>
            <button type="button" role="tab" aria-selected={tab === "recent"} onClick={() => setTab("recent")}>{t("home.recent")}</button>
            <button type="button" role="tab" aria-selected={tab === "favorites"} onClick={() => setTab("favorites")}>{t("home.favorites")}</button>
          </div>
          {tab === "recent" ? (rows.length === 0 ? <p className="nx-muted">{t("home.recentNone")}</p> : (
            <div className="hc-gridwrap"><table className="hc-grid">
              <thead><tr><th scope="col">{t("ns.col.name")}</th><th scope="col">{t("home.type")}</th><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("home.lastOpened")}</th></tr></thead>
              <tbody>
                {rows.map(({ r, l }) => (
                  <tr key={`${r.type}:${r.id}`}>
                    <td><span className="hc-nm"><ServiceTile id={LIST_OF[r.type]} size={18} /><button type="button" className="nx-lnk" onClick={() => navigateTo(r.type, r.id, "summary")}>{l.name}</button></span></td>
                    <td>{t(`home.kind.${r.type}`)}</td>
                    <td><StatusIndicator kind={l.kind} wire={l.wire} /></td>
                    <td>{relativeTime(r.at, lang)}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )) : (favs.length === 0 ? <p className="nx-muted">{t("svc.favoritesNone")}</p> : (
            <div className="hc-navs">{favs.map((id) => <button key={id} type="button" className="hc-nav" onClick={() => go(id)}><ServiceTile id={id} size={28} /><span><b>{t(serviceLabel(id))}</b><small>{t(`svc.d.${id}`)}</small></span></button>)}</div>
          ))}
          <p><button type="button" className="nx-lnk" onClick={() => go("all")}>{t("home.seeAll")}</button></p>
        </section>

        <section>
          <h2>{t("home.navigate")}</h2>
          <div className="hc-navs">
            <button type="button" className="hc-nav" onClick={() => go("all")}><ServiceTile id="all" size={28} /><span><b>{t("svc.all")}</b><small>{t("home.nResources", { n: vms.length + containers.length + nodes.length })}</small></span></button>
            <button type="button" className="hc-nav" onClick={() => go("nodes")}><ServiceTile id="nodes" size={28} /><span><b>{t("tab.nodes")}</b><small>{t("home.nOnline", { n: online.length, total: nodes.length })}</small></span></button>
            <button type="button" className="hc-nav" onClick={() => go("activity")}><ServiceTile id="activity" size={28} /><span><b>{t("nav.activity")}</b><small>{t("top.opsLabel", { n: ops.length })}</small></span></button>
            <button type="button" className="hc-nav" onClick={() => go("perf")}><ServiceTile id="perf" size={28} /><span><b>{t("tab.perf")}</b><small>{t("svc.d.perf")}</small></span></button>
          </div>
        </section>
      </div>

      <aside className="hc-home-side">
        <section className="hc-card">
          <h2>{t("home.cluster")}{allowed.has("cfs") && <button type="button" className="nx-lnk" onClick={() => go("cfs")}>{t("home.details")}</button>}</h2>
          <p className="hc-hstate" data-tone={health}>{t(`home.health.${health}`)}</p>
          <p className="nx-muted">{cluster?.in_cluster ? `${cluster.name} · ` : ""}{t("home.nOnline", { n: online.length, total: nodes.length })} · {t("chead.vms", { n: vms.length, r: vms.filter((v) => v.state === "active").length })}</p>
          <Capacity label={t("ns.cpu")} text={t("home.vcpuOf", { used: vcpu, total: cores })} value={pct(vcpu, cores)} />
          <Capacity label={t("ns.memory")} text={t("home.usedOf", { used: formatSizeMb(memUsed, lang), total: formatSizeMb(memTotal, lang) })} value={pct(memUsed, memTotal)} />
          <Capacity label={t("tab.storage")} text={t("home.usedOf", { used: formatSizeGb(usedGb, lang), total: formatSizeGb(capGb, lang) })} value={pct(usedGb, capGb)} />
        </section>
        <section className="hc-card">
          <h2>{t("home.watch")}</h2>
          {alerts.length === 0 ? <p className="nx-muted">{t("ns.noIncident")}</p> : (
            <ul className="hc-nlist">
              {alerts.slice(0, 6).map((a) => (
                <li key={a.id} data-tone={a.level === "warning" ? "warning" : "danger"}>
                  <div><b>{a.text}</b><small>{t(`alert.kind.${a.kind}`)}{a.detail ? ` — ${a.detail}` : ""}</small></div>
                  {a.target && <button type="button" className="nx-lnk" onClick={() => navigateTo(a.target.type, a.target.id, a.target.tab || "summary")}>{t("dock.view")}</button>}
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="hc-card">
          <h2>{t("home.running")}<button type="button" className="nx-lnk" onClick={() => go("activity")}>{t("nav.activity")}</button></h2>
          {ops.length === 0 ? <p className="nx-muted">{t("top.opsNone")}</p> : (
            <ul className="hc-nlist">
              {ops.slice(0, 5).map((o) => {
                const p = typeof o.progress === "number" && o.progress > 0 ? Math.min(100, Math.round(o.progress)) : null;
                return (
                  <li key={o.id}>
                    <div><b>{taskLabel(o.type)}</b>{o.target ? <> · {o.target}</> : null}{p != null && <span className="hc-nlist-p">{p} %</span>}
                      <span className={`nx-tray-bar${p == null ? " is-indet" : ""}`} role="progressbar" aria-label={taskLabel(o.type)} aria-valuemin={0} aria-valuemax={100} aria-valuenow={p ?? undefined}><span style={p == null ? undefined : { width: `${p}%` }} /></span>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </aside>
    </div>
  );
}
Home.ownHeader = true;
