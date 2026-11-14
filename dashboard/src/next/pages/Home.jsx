import { Fragment, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { ArrowDown, ArrowRight, ArrowUp, Check, EyeOff, LayoutDashboard, PanelLeft, PanelRight, Plus } from "lucide-react";
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
import HomePins from "../components/HomePins";
import SetupChecklist from "../components/SetupChecklist";
import { useCapacityForecast } from "../lib/useForecast";
import { formatDays } from "../lib/forecast";
import { DEFAULT_HOME, moveBlock, useAppearance } from "../lib/appearance";

const HOME_SERVICES = ["vms", "containers", "nodes", "storage", "network", "backups", "perf", "permissions"];
const pct = (used, total) => (total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0);

// What fills up within 90 days at the pace of the last month: storage pools reaching their capacity, node memory
// reaching 90 %. Says nothing until there are a few days of history.
function Forecast({ nodes }) {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const f = useCapacityForecast(nodes.map((n) => n.id));
  const nodeName = (id) => nodes.find((n) => n.id === id)?.name || id;
  if (!f) return null;
  const items = [
    ...f.pools.map((p) => ({ key: `p:${p.node}:${p.pool}`, days: p.days, kind: "full", text: t("fc.pool", { pool: p.pool, node: nodeName(p.node) }), go: () => navigateTo("datacenter", null, "storage") })),
    ...f.memory.map((m) => ({ key: `m:${m.node}`, days: m.days, kind: "at", text: t("fc.memory", { node: nodeName(m.node) }), go: () => navigateTo("node", m.node, "summary") })),
  ].sort((a, b) => a.days - b.days);
  return (
    <div className="hc-fc">
      <h3>{t("fc.title")}</h3>
      {items.length === 0 ? <p className="nx-muted">{f.enough ? t("fc.none") : t("fc.notEnough")}</p> : (
        <ul className="hc-nlist">
          {items.slice(0, 4).map((x) => (
            <li key={x.key} data-tone={x.days <= 14 ? "danger" : "warning"}>
              <div><b>{x.text}</b><small>{formatDays(x.days, t, x.kind)}</small></div>
              <button type="button" className="nx-lnk" onClick={x.go}>{t("dock.view")}</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

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
  const [editing, setEditing] = useState(false);
  const { home, setHome } = useAppearance(useShallow((s) => ({ home: s.home, setHome: s.setHome })));
  const resetHome = () => setHome(DEFAULT_HOME);
  const allowed = new Set(allowedServices({ admin: caps.admin, apiDocs }).map((s) => s.id));
  const go = (id) => navigateTo("datacenter", null, id);
  // A counted text takes its ".one" form for a single item (and for none in French).
  const tn = (key, n, vars = {}) => t((lang === "fr" ? n < 2 : n === 1) ? `${key}.one` : key, { n, ...vars });

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

  const blocks = {
    setup: (caps.admin || (vms.length === 0 && caps.create)) ? <>
          {caps.admin && <SetupChecklist />}
          {vms.length === 0 && caps.create && !caps.admin && (
            <div className="nx-bn" data-tone="info" role="status">
              <span className="nx-bn-t"><b>{t("home.emptyTitle")}</b> {t("home.emptyHelp")}</span>
              <button type="button" className="nx-btn nx-btn--primary" onClick={() => window.dispatchEvent(new CustomEvent("nx:wizard", { detail: "vm" }))}>{t("vmlist.create")}</button>
            </div>
          )}
        </> : null,
    services: (
        <section>
          <h2 className="hc-h1like">{t("home.services")}</h2>
          <div className="hc-svcs">
            {caps.create && <button type="button" className="hc-svc" onClick={() => go("create")}><span className="hc-tile hc-tile--ghost" style={{ "--s": "2.6667rem" }}><Plus size={24} aria-hidden="true" /></span><span>{t("svc.create")}</span></button>}
            {HOME_SERVICES.filter((id) => allowed.has(id)).map((id) => (
              <button key={id} type="button" className="hc-svc" data-preload={`datacenter:${id}`} onClick={() => go(id)}><ServiceTile id={id} size={40} /><span>{t(serviceLabel(id))}</span></button>
            ))}
            <button type="button" className="hc-svc" onClick={() => go("services")}><span className="hc-tile hc-tile--ghost" style={{ "--s": "2.6667rem" }}><ArrowRight size={24} aria-hidden="true" /></span><span>{t("svc.services")}</span></button>
          </div>
        </section>
    ),
    dashboard: <HomePins />,
    resources: (
        <section>
          <h2>{t("home.resources")}</h2>
          <div className="nx-tabs nx-tabs--page" role="tablist" aria-label={t("home.resources")}>
            <button type="button" role="tab" aria-selected={tab === "recent"} onClick={() => setTab("recent")}>{t("home.recent")}</button>
            <button type="button" role="tab" aria-selected={tab === "favorites"} onClick={() => setTab("favorites")}>{t("home.favorites")}</button>
          </div>
          {tab === "recent" ? (rows.length === 0 ? <p className="nx-muted">{t("home.recentNone")}</p> : (
            <div className="hc-gridwrap"><table className="hc-grid hc-grid--list">
              <thead><tr><th scope="col">{t("ns.col.name")}</th><th scope="col">{t("home.type")}</th><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("home.lastOpened")}</th></tr></thead>
              <tbody>
                {rows.map(({ r, l }) => (
                  <tr key={`${r.type}:${r.id}`}>
                    <td><span className="hc-nm"><ServiceTile id={LIST_OF[r.type]} size={18} /><button type="button" className="nx-lnk" data-preload={`${r.type}:summary`} onClick={() => navigateTo(r.type, r.id, "summary")}>{l.name}</button></span></td>
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
    ),
    navigate: (
        <section>
          <h2>{t("home.navigate")}</h2>
          <div className="hc-navs">
            <button type="button" className="hc-nav" onClick={() => go("all")}><ServiceTile id="all" size={28} /><span><b>{t("svc.all")}</b><small>{tn("home.nResources", vms.length + containers.length + nodes.length)}</small></span></button>
            <button type="button" className="hc-nav" onClick={() => go("nodes")}><ServiceTile id="nodes" size={28} /><span><b>{t("tab.nodes")}</b><small>{tn("home.nOnline", online.length, { total: nodes.length })}</small></span></button>
            <button type="button" className="hc-nav" onClick={() => go("activity")}><ServiceTile id="activity" size={28} /><span><b>{t("nav.activity")}</b><small>{ops.length ? tn("home.nOps", ops.length) : t("top.opsNone")}</small></span></button>
            <button type="button" className="hc-nav" onClick={() => go("perf")}><ServiceTile id="perf" size={28} /><span><b>{t("tab.perf")}</b><small>{t("svc.d.perf")}</small></span></button>
          </div>
        </section>
    ),
    cluster: (
        <section className="hc-card">
          <h2>{t("home.cluster")}{allowed.has("cfs") && <button type="button" className="nx-lnk" onClick={() => go("cfs")}>{t("home.details")}</button>}</h2>
          <p className="hc-hstate" data-tone={health}>{health === "ok" ? t("home.health.ok") : tn(`home.health.${health}`, health === "danger" ? alerts.filter((a) => a.level === "danger").length : alerts.length)}</p>
          <p className="nx-muted">{cluster?.in_cluster ? `${cluster.name} · ` : ""}{tn("home.nOnline", online.length, { total: nodes.length })} · {t("chead.vms", { n: vms.length, r: vms.filter((v) => v.state === "active").length })}</p>
          <Capacity label={t("ns.cpu")} text={t("home.vcpuOf", { used: vcpu, total: cores })} value={pct(vcpu, cores)} />
          <Capacity label={t("ns.memory")} text={t("home.usedOf", { used: formatSizeMb(memUsed, lang), total: formatSizeMb(memTotal, lang) })} value={pct(memUsed, memTotal)} />
          {capGb > 0 ? <Capacity label={t("tab.storage")} text={t("home.usedOf", { used: formatSizeGb(usedGb, lang), total: formatSizeGb(capGb, lang) })} value={pct(usedGb, capGb)} />
            : <div className="hc-cap"><div className="hc-cap-row"><b>{t("tab.storage")}</b><span>{t("home.noPool")}</span></div></div>}
          {online.length > 0 && <Forecast nodes={online} />}
        </section>
    ),
    watch: (
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
    ),
    running: (
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
    ),
  };
  const column = (name) => home[name].filter((id) => !home.hidden.includes(id) && blocks[id]).map((id, i, shown) => (
    editing ? (
      <div key={id} className="hc-blk" data-col={name}>
        <div className="hc-blk-bar" role="group" aria-label={t(`homeb.${id}`)}>
          <b>{t(`homeb.${id}`)}</b>
          <button type="button" className="hc-ibtn" aria-label={t("homeb.up", { name: t(`homeb.${id}`) })} title={t("homeb.upShort")} disabled={i === 0} onClick={() => setHome(moveBlock(home, id, -1))}><ArrowUp size={15} aria-hidden="true" /></button>
          <button type="button" className="hc-ibtn" aria-label={t("homeb.down", { name: t(`homeb.${id}`) })} title={t("homeb.downShort")} disabled={i === shown.length - 1} onClick={() => setHome(moveBlock(home, id, 1))}><ArrowDown size={15} aria-hidden="true" /></button>
          <button type="button" className="hc-ibtn" aria-label={t(name === "main" ? "homeb.toSide" : "homeb.toMain", { name: t(`homeb.${id}`) })} title={t(name === "main" ? "homeb.toSideShort" : "homeb.toMainShort")} onClick={() => setHome(moveBlock(home, id, "switch"))}>{name === "main" ? <PanelRight size={15} aria-hidden="true" /> : <PanelLeft size={15} aria-hidden="true" />}</button>
          <button type="button" className="hc-ibtn" aria-label={t("homeb.hide", { name: t(`homeb.${id}`) })} title={t("homeb.hideShort")} onClick={() => setHome({ ...home, hidden: [...home.hidden, id] })}><EyeOff size={15} aria-hidden="true" /></button>
        </div>
        <div className="hc-blk-body">{blocks[id]}</div>
      </div>
    ) : <Fragment key={id}>{blocks[id]}</Fragment>
  ));
  const hiddenNow = home.hidden.filter((id) => blocks[id] !== null);

  return (
    <>
      <h1 className="nx-sr">{t("nav.overview")}</h1>
      <div className="hc-home-tools">
        {editing && <span className="nx-muted">{t("homeb.editing")}</span>}
        {editing && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={resetHome}>{t("homeb.reset")}</button>}
        <button type="button" className={`nx-btn nx-btn--sm${editing ? " nx-btn--primary" : " nx-btn--ghost"}`} aria-pressed={editing} onClick={() => setEditing(!editing)}>{editing ? <Check size={15} aria-hidden="true" /> : <LayoutDashboard size={15} aria-hidden="true" />}{editing ? t("homeb.done") : t("homeb.customize")}</button>
      </div>
      {editing && hiddenNow.length > 0 && (
        <div className="hc-blk-hidden" role="group" aria-label={t("homeb.hidden")}>
          <span>{t("homeb.hidden")}</span>
          {hiddenNow.map((id) => <button key={id} type="button" className="nx-btn nx-btn--sm" onClick={() => setHome({ ...home, hidden: home.hidden.filter((x) => x !== id) })}><Plus size={14} aria-hidden="true" />{t(`homeb.${id}`)}</button>)}
        </div>
      )}
      <div className="hc-home" data-editing={editing || undefined}>
        <div className="hc-home-main">{column("main")}</div>
        <aside className="hc-home-side">{column("side")}</aside>
      </div>
    </>
  );
}
Home.ownHeader = true;
