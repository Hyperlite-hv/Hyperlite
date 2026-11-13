import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Download, RefreshCw, Search } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { refreshInventory, useFreshness } from "../lib/inventory";
import { normalize } from "../lib/format";
import { requestIntent } from "../lib/intents";
import { vmKey } from "../lib/vmId";
import { CATEGORIES, LIST_OF, allowedServices, serviceLabel } from "../lib/services";
import ServiceTile, { FavoriteButton } from "../components/ServiceTile";
import StatusIndicator from "../components/StatusIndicator";
import { PageHeader } from "../components/ui";
import { Cmd, FilterPill, downloadCsv } from "../components/portal";
import { EmptyState } from "../components/States";
import { useApiDocsAccess } from "../layout/SideMenu";

function Filter({ value, onChange, label }) {
  return (
    <label className="hc-filter"><Search size={16} aria-hidden="true" /><input type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder={label} aria-label={label} /></label>
  );
}

// The catalogue: every service this person may open, by family, each one pinnable in the left menu.
export default function ServicesPage() {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const caps = capabilities(useAuthStore((s) => s.role));
  const apiDocs = useApiDocsAccess(caps.admin);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState(null);
  const all = allowedServices({ admin: caps.admin, apiDocs });
  const needle = normalize(q);
  const shown = all.filter((s) => (!cat || s.cat === cat) && (!needle || normalize(`${t(serviceLabel(s.id))} ${t(`svc.d.${s.id}`)}`).includes(needle)));
  return (
    <>
      <PageHeader title={t("svc.services")} />
      <div className="hc-cat">
        <nav className="hc-cat-l" aria-label={t("svc.families")}>
          <button type="button" aria-current={cat === null ? "true" : undefined} onClick={() => setCat(null)}>{t("svc.cat.all")}</button>
          {CATEGORIES.map((c) => <button key={c} type="button" aria-current={cat === c ? "true" : undefined} onClick={() => setCat(c)}><span className={`hc-swatch hc-t-${c}`} aria-hidden="true" />{t(`svc.cat.${c}`)}</button>)}
        </nav>
        <div className="hc-cat-r">
          <Filter value={q} onChange={setQ} label={t("svc.filter")} />
          {shown.length === 0 && <EmptyState title={t("svc.none")} help={t("svc.noneHelp")} />}
          {CATEGORIES.filter((c) => shown.some((s) => s.cat === c)).map((c) => (
            <section key={c}>
              <h2>{t(`svc.cat.${c}`)}</h2>
              <div className="hc-cgrid">
                {shown.filter((s) => s.cat === c).map((s) => (
                  <div key={s.id} className="hc-centry">
                    <button type="button" className="hc-centry-go" onClick={() => navigateTo("datacenter", null, s.id)}>
                      <ServiceTile id={s.id} size={28} />
                      <span><b>{t(serviceLabel(s.id))}</b><small>{t(`svc.d.${s.id}`)}</small></span>
                    </button>
                    <FavoriteButton id={s.id} />
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </>
  );
}
ServicesPage.ownHeader = true;

// What can be created, by family. Machines and containers open their wizard; the others open their service with
// the creation form ready.
const CREATABLE = [
  ["compute", [["vm", "vms", "action.createVm"], ["container", "containers", "action.createContainer"], ["k8s", "kubernetes", "create.k8s", true], ["node", "nodes", "create.node", true]]],
  ["storage", [["pool", "storage", "action.createPool"], ["network", "network", "action.createNetwork"], ["iso", "library", "create.iso", true]]],
  ["auto", [["job", "automation", "create.job", true], ["channel", "notifications", "create.channel", true], ["token", "api", "create.token", true]]],
  ["identity", [["user", "permissions", "action.createUser"]]],
];
// Entries marked "admin" lead to a service only administrators may change.
const ADMIN_ONLY_CREATE = new Set(["k8s", "node", "iso", "job", "channel", "token"]);
export function CreatePage() {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const caps = capabilities(useAuthStore((s) => s.role));
  const create = (kind, svc) => {
    if (kind === "vm" || kind === "container") { window.dispatchEvent(new CustomEvent("nx:wizard", { detail: kind })); return; }
    // The services that have a creation form open it on arrival; the others open on their page.
    if (["pool", "network", "user", "node"].includes(kind)) requestIntent(kind);
    navigateTo("datacenter", null, svc);
  };
  return (
    <>
      <PageHeader title={t("svc.create")} />
      <div className="hc-cat-r hc-cat-r--solo">
        {CREATABLE.filter(([c]) => c !== "identity" || caps.admin).map(([c, list]) => [c, list.filter(([k]) => caps.admin || !ADMIN_ONLY_CREATE.has(k))]).filter(([, list]) => list.length).map(([c, entries]) => (
          <section key={c}>
            <h2>{t(`svc.cat.${c === "storage" ? "storageNetwork" : c}`)}</h2>
            <div className="hc-cgrid">
              {entries.map(([kind, svc, label]) => (
                <div key={kind} className="hc-centry">
                  <button type="button" className="hc-centry-go" onClick={() => create(kind, svc)}>
                    <ServiceTile id={svc} size={28} />
                    <span><b>{t(label)}</b><small>{t(`create.d.${kind}`)}</small></span>
                  </button>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
CreatePage.ownHeader = true;

// Every resource of the cluster in one list: machines, containers and nodes, with a filter on any field.
export function AllResourcesPage() {
  const t = useT();
  const { nodes, vms, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, navigateTo: s.navigateTo })));
  const containers = useFreshness((s) => s.containers);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const nodeName = (id) => nodes.find((n) => n.id === (id || "local"))?.name || id || "";
  const rows = [
    ...vms.map((v) => ({ type: "vm", id: vmKey(v), name: v.name, kind: "vm", wire: v.state, node: nodeName(v.node), extra: v.ip || "" })),
    ...containers.map((c) => ({ type: "container", id: c.name, name: c.name, kind: "vm", wire: c.state === "active" ? "active" : "stopped", node: nodeName(c.node), extra: c.ip || "" })),
    ...nodes.map((n) => ({ type: "node", id: n.id, name: n.name, kind: "node", wire: n.state, node: "", extra: n.ip || "" })),
  ];
  const needle = normalize(q);
  const shown = rows.filter((r) => (!kind || r.type === kind)).filter((r) => !needle || normalize(`${r.name} ${t(`home.kind.${r.type}`)} ${r.node} ${r.extra}`).includes(needle));
  return (
    <>
      <PageHeader title={t("svc.all")} actions={(
        <>
          <Cmd icon={RefreshCw} label={t("action.refresh")} onClick={() => refreshInventory().catch(() => {})} />
          <Cmd icon={Download} label={t("list.exportCsv")} disabled={shown.length === 0} onClick={() => downloadCsv("resources.csv", [
            [t("ns.col.name"), t("home.type"), t("ns.col.state"), t("ns.node"), t("vmlist.ip")],
            ...shown.map((r) => [r.name, t(`home.kind.${r.type}`), r.wire, r.node, r.extra]),
          ])} />
        </>
      )} />
      <div className="hc-fbar">
        <Filter value={q} onChange={setQ} label={t("all.filter")} />
        <FilterPill label={t("list.typeIs")} value={kind} onChange={setKind} options={[{ value: "", label: t("list.all") }, ...["vm", "container", "node"].map((k) => ({ value: k, label: t(`home.kind.${k}`) }))]} />
      </div>
      <p className="hc-count">{t("all.count", { n: shown.length, total: rows.length })}</p>
      {shown.length === 0 ? <EmptyState title={t("all.none")} help={t("svc.noneHelp")} /> : (
        <div className="hc-gridwrap">
          <table className="hc-grid hc-grid--list">
            <thead><tr><th scope="col">{t("ns.col.name")}</th><th scope="col">{t("home.type")}</th><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("ns.node")}</th><th scope="col">{t("vmlist.ip")}</th></tr></thead>
            <tbody>
              {shown.map((r) => (
                <tr key={`${r.type}:${r.id}`}>
                  <td><span className="hc-nm"><ServiceTile id={LIST_OF[r.type]} size={18} /><button type="button" className="nx-lnk" title={r.name} onClick={() => navigateTo(r.type, r.id, "summary")}>{r.name}</button></span></td>
                  <td>{t(`home.kind.${r.type}`)}</td>
                  <td><StatusIndicator kind={r.kind} wire={r.wire} /></td>
                  <td>{r.node || "—"}</td>
                  <td className="hc-num">{r.extra || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
AllResourcesPage.ownHeader = true;
