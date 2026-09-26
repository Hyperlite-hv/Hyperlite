import { useCallback, useEffect, useState } from "react";
import { fetchRemoteNodes, fetchClusterPubkey, addRemoteNode, fetchRemoteNodeSummary, deleteRemoteNode } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatSizeGb } from "../lib/format";
import StatusIndicator from "../components/StatusIndicator";
import { ErrorState } from "../components/States";

const EMPTY = { name: "", hostname: "", ssh_user: "root", ssh_port: 22 };

// Nodes: the local host plus the registered remote nodes (libvirt over SSH, no agent), with the cluster
// public key to authorize and a connection test on add. Same endpoints as the historical tab; the SSH
// port, accepted by the API but never editable there, is now a field.
export default function NodesPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const storeNodes = useInfraStore((s) => s.nodes);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const local = storeNodes.find((n) => n.id === "local");
  const localVms = useInfraStore((s) => s.vms).filter((v) => v.node === "local");
  const [nodes, setNodes] = useState(null);
  const [error, setError] = useState(null);
  const [pubkey, setPubkey] = useState(null);
  const [summaries, setSummaries] = useState({});
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try { const r = await fetchRemoteNodes(); setNodes(Array.isArray(r) ? r : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { if (caps.admin) fetchClusterPubkey().then((r) => setPubkey(r.public_key)).catch(() => {}); }, [caps.admin]);
  useEffect(() => {
    (nodes || []).forEach((n) => {
      fetchRemoteNodeSummary(n.name).then((s) => setSummaries((p) => ({ ...p, [n.name]: s }))).catch(() => setSummaries((p) => ({ ...p, [n.name]: null })));
    });
  }, [nodes]);

  async function copyKey() {
    try { await navigator.clipboard.writeText(pubkey); pushToast({ kind: "success", title: t("nd.keyCopied"), message: t("nd.keyCopiedHelp") }); }
    catch { pushToast({ kind: "error", title: t("nd.copyFailed"), message: t("nd.copyManual") }); }
  }
  async function add(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await addRemoteNode({ ...form, ssh_port: Number(form.ssh_port) || 22 });
      pushToast({ kind: "success", title: t("nd.added"), message: form.name });
      setAdding(false); setForm(EMPTY); await reload();
    } catch (err) { pushToast({ kind: "error", title: t("nd.connFailed"), message: errorMessage(err) }); }
    finally { setBusy(false); }
  }
  async function remove(n) {
    if (!(await confirmAction({ title: t("nd.removeTitle", { name: n.name }), message: t("nd.removeMsg"), confirmLabel: t("nd.remove"), danger: true }))) return;
    try { await deleteRemoteNode(n.name); pushToast({ kind: "success", title: t("nd.removed"), message: n.name }); await reload(); }
    catch (err) { pushToast({ kind: "error", title: t("action.failed", { action: t("nd.remove") }), message: errorMessage(err) }); }
  }
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  if (error && nodes == null) return <ErrorState message={error} onRetry={reload} />;
  const list = nodes || [];

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="nd-list">
        <div className="nx-cardhead">
          <h2 id="nd-list">{t("nav.nodes")} <span className="nx-count">{nodes ? list.length + (local ? 1 : 0) : "…"}</span></h2>
          {caps.admin && <button type="button" className="nx-btn nx-btn--primary" aria-expanded={adding} onClick={() => setAdding((a) => !a)}>{t("nd.add")}</button>}
        </div>
        <p className="nx-muted" style={{ marginTop: 0, maxWidth: "62ch" }}>{t("nd.intro")}</p>

        {adding && (
          <form className="nx-form" onSubmit={add}>
            {pubkey && (
              <div>
                <p style={{ margin: 0 }}>{t("nd.step1")} <code className="nx-mono">~/.ssh/authorized_keys</code></p>
                <div className="nx-keybox"><code className="nx-mono">{pubkey}</code><button type="button" className="nx-btn" onClick={copyKey}>{t("action.copy")}</button></div>
              </div>
            )}
            <p style={{ margin: 0 }}>{t("nd.step2")}</p>
            <div className="nx-formgrid">
              <label>{t("ct.name")}<input className="nx-input" aria-label="Name" required value={form.name} onChange={set("name")} placeholder="node-2" /></label>
              <label>{t("nd.host")}<input className="nx-input" aria-label="IP address or hostname" required value={form.hostname} onChange={set("hostname")} placeholder="192.168.1.20" /></label>
              <label>{t("nd.sshUser")}<input className="nx-input" aria-label="SSH user" value={form.ssh_user} onChange={set("ssh_user")} /></label>
              <label>{t("nd.sshPort")}<input className="nx-input" aria-label="SSH port" type="number" min={1} max={65535} value={form.ssh_port} onChange={set("ssh_port")} /></label>
            </div>
            <div className="nx-formactions">
              <button type="button" className="nx-btn" onClick={() => setAdding(false)}>{t("action.cancel")}</button>
              <button type="submit" className="nx-btn nx-btn--primary" disabled={busy || !form.name || !form.hostname}>{busy ? t("nd.testing") : t("nd.testAdd")}</button>
            </div>
          </form>
        )}

        {nodes == null ? <p className="nx-muted" role="status">{t("loading")}</p> : (
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("ct.name")}</th><th scope="col">{t("nd.connection")}</th><th scope="col" className="nx-num">{t("nd.vmsRunning")}</th><th scope="col" className="nx-num">{t("nd.vmsStopped")}</th><th scope="col" className="nx-num">{t("stor.free")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {local && (
                  <tr key="local">
                    <td><StatusIndicator kind="node" wire={local.etat} /></td>
                    <th scope="row"><button type="button" className="nx-link" onClick={() => navigateTo("node", "local", "summary")}>{local.nom}</button> <span className="nx-tag">{t("nd.thisHost")}</span></th>
                    <td className="nx-mono">{t("nd.localConn")}</td>
                    <td className="nx-num nx-mono">{localVms.filter((v) => v.etat === "actif").length}</td>
                    <td className="nx-num nx-mono">{localVms.filter((v) => v.etat !== "actif").length}</td>
                    <td className="nx-num nx-mono">{local.stockage_total_go != null ? `${formatSizeGb(local.stockage_total_go - (local.stockage_utilise_go || 0), lang) ?? "?"} / ${formatSizeGb(local.stockage_total_go, lang)}` : <span className="nx-muted">{t("ns.notReported")}</span>}</td>
                    <td />
                  </tr>
                )}
                {list.map((n) => {
                  const s = summaries[n.name];
                  const na = <span className="nx-muted">{s === undefined ? "…" : t("ns.notReported")}</span>;
                  return (
                    <tr key={n.id}>
                      <td><StatusIndicator kind="node" wire={n.statut === "en_ligne" ? "online" : "erreur"} /></td>
                      <th scope="row"><button type="button" className="nx-link" onClick={() => navigateTo("node", String(n.id), "summary")}>{n.name}</button></th>
                      <td className="nx-mono">{n.ssh_user}@{n.hostname}:{n.ssh_port}</td>
                      <td className="nx-num nx-mono">{s ? s.vms_actives : na}</td>
                      <td className="nx-num nx-mono">{s ? s.vms_arretees : na}</td>
                      <td className="nx-num nx-mono">{s && s.stockage_capacite_go != null ? `${formatSizeGb(s.stockage_disponible_go, lang) ?? "?"} / ${formatSizeGb(s.stockage_capacite_go, lang)}` : na}</td>
                      <td className="nx-num">{caps.admin && <button type="button" className="nx-btn nx-btn--danger" aria-label={`Remove node ${n.name}`} onClick={() => remove(n)}>{t("nd.remove")}</button>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
