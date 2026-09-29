import { useCallback, useEffect, useRef, useState } from "react";
import { Boxes, Download, Info, Plus, Trash2 } from "lucide-react";
import { createK8sCluster, deleteK8sCluster, fetchK8sClusters, fetchKubeconfig, fetchNetworks } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { ErrorState, InlineError } from "../components/States";
import { PageHeader, Empty, Loading, Pill, StatePill, TableWrap } from "../components/ui";

const TONE = { pret: "success", creation: "info", suppression: "info", echec: "danger" };
const MAX_WORKERS = 5;

// Kubernetes: k3s clusters built on Hyperlite VMs (one server, N workers). Hyperlite creates the VMs, installs
// and joins k3s, and hands over the kubeconfig; workloads are then deployed with kubectl or Helm.
export default function KubernetesPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const vms = useInfraStore((s) => s.vms);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [creating, setCreating] = useState(false);

  const reload = useCallback(async () => {
    try { const r = await fetchK8sClusters(); setRows(Array.isArray(r) ? r : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  // A cluster takes minutes to build: poll faster while one is being created or deleted.
  const busy = (rows || []).some((c) => c.statut === "creation" || c.statut === "suppression");
  useEffect(() => { reload(); const id = setInterval(reload, busy ? 5000 : 20000); return () => clearInterval(id); }, [reload, busy]);

  async function download(name) {
    try {
      const text = await fetchKubeconfig(name);
      const url = URL.createObjectURL(new Blob([text], { type: "application/yaml" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: `${name}.kubeconfig` });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { pushToast({ kind: "error", title: t("k8s.downloadFailed"), message: errorMessage(e) }); }
  }
  async function remove(c) {
    const vmsList = [c.serveur, ...c.workers].join(", ");
    if (!(await confirmAction({ title: t("k8s.deleteTitle", { name: c.nom }), message: t("k8s.deleteMsg", { vms: vmsList }), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteK8sCluster(c.nom); pushToast({ kind: "success", title: t("k8s.deleted"), message: c.nom }); reload(); }
    catch (e) { pushToast({ kind: "error", title: t("stor.deleteFailed"), message: errorMessage(e) }); reload(); }
  }
  const vmState = (name) => vms.find((v) => v.nom === name)?.etat;
  const list = rows || [];

  return (
    <>
      <PageHeader title={t("tab.kubernetes")} count={rows ? list.length : null} desc={t("k8s.desc")}
        actions={caps.admin && <button type="button" className="nx-btn nx-btn--primary" onClick={() => setCreating(true)}><Plus size={15} aria-hidden="true" />{t("k8s.create")}</button>} />
      {error ? <ErrorState message={error} onRetry={reload} /> : rows == null ? <Loading /> : list.length === 0 ? (
        <div className="nx-card2"><Empty icon={Boxes} title={t("k8s.none")} text={t("k8s.noneHelp")} /></div>
      ) : (
        <div className="nx-card2 nx-card2--flush">
          <TableWrap label={t("tab.kubernetes")}>
            <table className="nx-table">
              <thead><tr>
                <th scope="col">{t("k8s.cluster")}</th><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("k8s.nodes")}</th>
                <th scope="col">{t("k8s.api")}</th><th scope="col">{t("k8s.version")}</th><th scope="col">{t("lib.added")}</th>
                <th scope="col"><span className="nx-sr">{t("actions")}</span></th>
              </tr></thead>
              <tbody>
                {list.map((c) => (
                  <tr key={c.nom}>
                    <th scope="row" style={{ fontWeight: 500 }}>{c.nom}</th>
                    <td>
                      <Pill tone={TONE[c.statut] || "info"}>{t(`k8s.state.${c.statut}`)}</Pill>
                      {c.statut === "echec" && c.erreur && <div className="nx-muted" style={{ fontSize: "var(--fs-12)", marginTop: 4, maxWidth: "28rem" }}>{c.erreur}</div>}
                    </td>
                    <td>
                      {[c.serveur, ...c.workers].map((vm, i) => (
                        <div key={vm}>
                          <button type="button" className="nx-lnk" onClick={() => navigateTo("vm", vm, "summary")} disabled={!vmState(vm)}>{vm}</button>
                          <span className="nx-muted"> · {i === 0 ? t("k8s.role.server") : t("k8s.role.worker")} </span>
                          {vmState(vm) && <StatePill kind="vm" wire={vmState(vm)} />}
                        </div>
                      ))}
                    </td>
                    <td className="nx-mono">{c.adresse ? `https://${c.adresse}:6443` : "—"}</td>
                    <td className="nx-mono nx-muted">{c.version || "—"}</td>
                    <td className="nx-mono nx-muted">{c.cree_le ? formatDateTime(c.cree_le, lang) : "—"}</td>
                    <td><div className="nx-ra">{caps.admin && (<>
                      {c.kubeconfig_disponible && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => download(c.nom)}><Download size={15} aria-hidden="true" /><span className="nx-hide-narrow">{t("k8s.kubeconfig")}</span></button>}
                      {c.statut !== "creation" && c.statut !== "suppression" && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("k8s.deleteX", { name: c.nom })} title={t("vx.delete")} onClick={() => remove(c)}><Trash2 size={15} aria-hidden="true" /></button>}
                    </>)}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </div>
      )}
      {list.some((c) => c.kubeconfig_disponible) && (
        <div className="nx-bn" data-tone="info" role="note"><Info size={16} aria-hidden="true" /><span className="nx-bn-t">{t("k8s.usage")}</span></div>
      )}
      {creating && <CreateClusterDialog onClose={() => setCreating(false)} onStarted={reload} />}
    </>
  );
}

function CreateClusterDialog({ onClose, onStarted }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const storeNetworks = useInfraStore((s) => s.networks);
  // Read fresh rather than trusted from the store: the store may be stale or not loaded yet, and an
  // empty list would just disable "Create" with nothing saying why.
  const [freshNetworks, setFreshNetworks] = useState(null);
  const [networksError, setNetworksError] = useState(null);
  const loadNetworks = useCallback(() => {
    setNetworksError(null);
    fetchNetworks().then((l) => setFreshNetworks(Array.isArray(l) ? l : [])).catch((e) => setNetworksError(errorMessage(e)));
  }, []);
  useEffect(() => { loadNetworks(); }, [loadNetworks]);
  const networks = (freshNetworks ?? storeNetworks).filter((n) => n.actif);
  const pickNetwork = (list) => list.find((n) => n.type === "nat")?.nom || list[0]?.nom || "";
  const [form, setForm] = useState({ nom: "", workers: 2, vcpu: 2, memoire_mo: 2048, disque_go: 20, reseau: pickNetwork(networks) });
  useEffect(() => {
    setForm((f) => (networks.some((n) => n.nom === f.reseau) ? f : { ...f, reseau: pickNetwork(networks) }));
  }, [freshNetworks, storeNetworks]); // eslint-disable-line react-hooks/exhaustive-deps
  const lang = useLangStore((s) => s.lang);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const first = useRef(null);
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);
  useEffect(() => { first.current?.focus(); const el = opener.current; return () => el?.focus?.(); }, []);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === "number" ? Number(e.target.value) : e.target.value }));
  const nodesCount = Number(form.workers) + 1;
  const net = networks.find((n) => n.nom === form.reseau);
  const validName = /^[a-z][a-z0-9-]{1,40}[a-z0-9]$/.test(form.nom);

  async function submit(e) {
    e.preventDefault();
    if (!validName) { setError(t("k8s.nameHelp")); return; }
    setBusy(true); setError(null);
    try {
      await createK8sCluster({ ...form, workers: Number(form.workers) });
      pushToast({ kind: "success", title: t("k8s.started"), message: t("k8s.startedMsg", { name: form.nom }) });
      onStarted?.(); onClose();
    } catch (err) { setError(errorMessage(err)); setBusy(false); }
  }

  return (
    <div className="nx-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <form className="nx-dialog" role="dialog" aria-modal="true" aria-labelledby="k8s-title" onSubmit={submit} onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.stopPropagation(); onClose(); } }}>
        <h2 id="k8s-title">{t("k8s.create")}</h2>
        <p className="nx-muted" style={{ margin: 0 }}>{t("k8s.createHelp")}</p>
        <label className="nx-dialog-field">{t("k8s.name")}
          <input ref={first} className="nx-input" value={form.nom} onChange={set("nom")} disabled={busy} autoComplete="off" spellCheck={false} aria-describedby="k8s-name-help" />
          <span className="nx-hint" id="k8s-name-help">{t("k8s.nameHelp")}</span>
        </label>
        <label className="nx-dialog-field">{t("k8s.workers")}
          <input className="nx-input" type="number" min={1} max={MAX_WORKERS} value={form.workers} onChange={set("workers")} disabled={busy} />
        </label>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(8rem, 1fr))", gap: "var(--space-3)" }}>
          <label className="nx-dialog-field">{t("k8s.vcpu")}<input className="nx-input" type="number" min={1} value={form.vcpu} onChange={set("vcpu")} disabled={busy} /></label>
          <label className="nx-dialog-field">{t("k8s.memory")}<input className="nx-input" type="number" min={1024} step={256} value={form.memoire_mo} onChange={set("memoire_mo")} disabled={busy} /></label>
          <label className="nx-dialog-field">{t("k8s.disk")}<input className="nx-input" type="number" min={10} value={form.disque_go} onChange={set("disque_go")} disabled={busy} /></label>
        </div>
        <label className="nx-dialog-field">{t("k8s.network")}
          <select className="nx-input" value={form.reseau} onChange={set("reseau")} disabled={busy}>
            {networks.length === 0 && <option value="">{t("k8s.noNetwork")}</option>}
            {networks.map((n) => <option key={n.nom} value={n.nom}>{n.nom}{n.type ? ` (${n.type})` : ""}</option>)}
          </select>
        </label>
        {networksError && <InlineError message={networksError} onRetry={loadNetworks} />}
        {/* wire value of an isolated libvirt network: "isole" (see app/routers/network.py) */}
        {net?.type === "isole" && <div className="nx-bn" data-tone="warning" role="status"><span className="nx-bn-t">{t("k8s.isolatedWarn")}</span></div>}
        <p className="nx-muted" role="status" style={{ margin: 0 }}>{t("k8s.total", { n: nodesCount, vcpu: nodesCount * Number(form.vcpu), mem: new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format((nodesCount * Number(form.memoire_mo)) / 1024), disk: nodesCount * Number(form.disque_go) })}</p>
        {error && <div className="nx-bn" data-tone="danger" role="alert"><span className="nx-bn-t">{error}</span></div>}
        <div className="nx-dialog-actions">
          <button type="button" className="nx-btn" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
          <button type="submit" className="nx-btn nx-btn--primary" disabled={busy || !form.reseau}>{busy ? t("k8s.starting") : t("k8s.createGo")}</button>
        </div>
      </form>
    </div>
  );
}
KubernetesPage.ownHeader = true;
