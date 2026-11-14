import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Copy, CopyCheck, Download, Ellipsis, LogOut, Plus, RefreshCw, Search, Server, Tag, Trash2, X } from "lucide-react";
import { fetchMeta, leaveNodeMaintenance, saveMeta } from "../../api/client";
import { promptText } from "../../store/usePromptStore";
import { nameList, runBulk } from "../lib/bulk";
import { fetchClusterPubkey, addRemoteNode, deleteRemoteNode, testNodeConnection, fetchConfigCopies, copyConfigNow, fetchNodeMetricsHistory } from "../../api/client";
import { nodeTone } from "../lib/nodeState";
import { usePolling } from "../lib/polling";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatDateTime, formatSizeMb, formatUptimeLong } from "../lib/format";
import { refreshInventory } from "../lib/inventory";
import { useIntent } from "../lib/intents";
import { useContextTarget } from "../components/ContextMenu";
import { NodeContextMenu } from "../components/ObjectActions";
import { Cmd, FilterPill, downloadCsv } from "../components/portal";
import { PageHeader, Meter, Chip, SideDrawer, Field, TableWrap, Card } from "../components/ui";

const EMPTY = { name: "", hostname: "", ssh_user: "root", ssh_port: "22" };
const pct = (used, total) => (used != null && total ? (used / total) * 100 : null);

// The add-node drawer: authorize the cluster key, fill the address, test the connection, then add.
function AddNodeDrawer({ open, onClose, onAdded }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [pubkey, setPubkey] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState(null);
  useEffect(() => { if (open) fetchClusterPubkey().then((r) => setPubkey(r.public_key)).catch(() => setPubkey(null)); }, [open]);
  const set = (k) => (e) => { setForm((f) => ({ ...f, [k]: e.target.value })); setTest(null); };
  const port = Number(form.ssh_port);
  const valid = form.name && form.hostname && Number.isInteger(port) && port >= 1 && port <= 65535;

  async function copyKey() {
    try { await navigator.clipboard.writeText(pubkey); pushToast({ kind: "success", title: t("nd.keyCopied"), message: t("nd.keyCopiedHelp") }); }
    catch { pushToast({ kind: "error", title: t("nd.copyFailed"), message: t("nd.copyManual") }); }
  }
  async function runTest() {
    setBusy(true); setTest(null);
    try { const r = await testNodeConnection({ hostname: form.hostname, ssh_user: form.ssh_user, ssh_port: port }); setTest(r); }
    catch (e) { setTest({ ok: false, detail: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  async function add() {
    setBusy(true);
    try {
      await addRemoteNode({ ...form, ssh_port: port || 22 });
      pushToast({ kind: "success", title: t("nd.added"), message: form.name });
      setForm(EMPTY); setTest(null); onAdded(); onClose();
    } catch (err) { pushToast({ kind: "error", title: t("nd.connFailed"), message: errorMessage(err) }); }
    finally { setBusy(false); }
  }
  return (
    <SideDrawer open={open} title={t("nd.drawerTitle")} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn" disabled={busy || !valid} onClick={runTest}>{t("nd.test")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || !valid} onClick={add}>{busy ? t("nd.testing") : t("nd.testAdd")}</button>
    </>}>
      <div className="nx-bn" data-tone="warning" role="note"><span className="nx-bn-t">{t("nd.deprecated")}</span></div>
      <ol className="nx-steps-list">
        <li>{t("nd.s1")}</li>
        <li>{t("nd.s2")}</li>
        <li>{t("nd.s3")}</li>
      </ol>
      {pubkey && (
        <div className="nx-f">
          <span className="nx-f-label">{t("nd.pubkey")}</span>
          <div className="nx-keybox2"><code className="nx-mono">{pubkey}</code><button type="button" className="nx-btn nx-btn--sm" onClick={copyKey}><Copy size={14} aria-hidden="true" />{t("action.copy")}</button></div>
          <span className="nx-f-h">{t("nd.pubkeyHint")} <code className="nx-mono">~/.ssh/authorized_keys</code></span>
        </div>
      )}
      <div className="nx-fg">
        <Field label={t("nd.name")}>{(p) => <input {...p} className="nx-inp" aria-label={t("a11y.name")} required value={form.name} onChange={set("name")} placeholder="node-2" />}</Field>
        <Field label={t("nd.host")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.ip_address_or_hostname")} required value={form.hostname} onChange={set("hostname")} placeholder="192.168.1.20" />}</Field>
        <Field label={t("nd.sshUser")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.ssh_user")} value={form.ssh_user} onChange={set("ssh_user")} />}</Field>
        <Field label={t("nd.sshPort")}>{(p) => <input {...p} className="nx-inp nx-mono" aria-label={t("a11y.ssh_port")} type="number" min={1} max={65535} value={form.ssh_port} onChange={set("ssh_port")} />}</Field>
      </div>
      {test && (
        <div className="nx-bn" data-tone={test.ok ? "success" : "danger"} role="status">
          <span className="nx-bn-t">{test.ok ? t("nd.testOk", { host: test.detail }) : t("nd.testKo", { error: test.detail })}</span>
        </div>
      )}
    </SideDrawer>
  );
}

// The configuration (users, permissions, nodes, HA, schedules, keys) copied to each node, so another node can take
// over with `hyperlite-promote` if this controller is lost (docs/cluster-failover.md).
function ConfigCopyCard({ nodes }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [copies, setCopies] = useState(null);
  const [busy, setBusy] = useState(false);
  const reload = () => fetchConfigCopies().then(setCopies).catch(() => setCopies([]));
  useEffect(() => { reload(); const id = setInterval(reload, 60000); return () => clearInterval(id); }, []);
  const remote = nodes.filter((n) => n.id !== "local");
  if (remote.length === 0) return null;
  const byNode = Object.fromEntries((copies || []).map((c) => [c.node, c]));
  async function now() {
    setBusy(true);
    try {
      const r = await copyConfigNow(); setCopies(r.copies);
      const failed = r.results.filter((x) => x.status !== "ok");
      if (failed.length) pushToast({ kind: "error", title: t("cfc.failed"), message: failed.map((x) => `${x.node}: ${x.error}`).join(" · ") });
      else pushToast({ kind: "success", title: t("cfc.done"), message: t("cfc.doneMsg", { n: r.results.length }) });
    } catch (e) { pushToast({ kind: "error", title: t("cfc.failed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  return (
    <Card title={t("cfc.title")} flush actions={<button type="button" className="nx-btn nx-btn--sm" disabled={busy} onClick={now}><CopyCheck size={14} aria-hidden="true" />{t("cfc.now")}</button>}>
      <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t("cfc.help")} <code className="nx-mono">/usr/lib/hyperlite/scripts/hyperlite-promote</code></p>
      <div className="nx-bn" data-tone="warning" role="note" style={{ margin: "0 var(--space-4) var(--space-3)" }}><span className="nx-bn-t">{t("cfc.deprecated")}</span></div>
      <TableWrap>
        <table className="nx-table">
          <thead><tr><th scope="col">{t("nd.node")}</th><th scope="col">{t("cfc.last")}</th></tr></thead>
          <tbody>
            {remote.map((n) => {
              const c = byNode[n.id];
              return (
                <tr key={n.id}>
                  <th scope="row" className="nx-mono">{n.name}</th>
                  <td>{!c ? <span className="nx-muted">{t("cfc.never")}</span> : c.status === "ok"
                    ? <><Chip tone="success">{t("cfc.ok")}</Chip> <span className="nx-mono nx-muted">{formatDateTime(c.copied_at, lang)}</span></>
                    : <><Chip tone="danger">{t("cfc.ko")}</Chip> <span className="nx-f-h is-error">{c.error}</span></>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableWrap>
    </Card>
  );
}

// The last 24 hours of a node's processor load, one cell per hour (empty while nothing was recorded).
function useHeat(nodes) {
  const [heat, setHeat] = useState({});
  const ids = nodes.map((n) => n.id).join("|");
  const load = () => {
    const now = Date.now();
    nodes.forEach((n) => fetchNodeMetricsHistory(n.id, "24h").then((rows) => {
      const sums = Array(24).fill(0), counts = Array(24).fill(0);
      for (const r of Array.isArray(rows) ? rows : []) {
        if (r.cpu_pct == null) continue;
        const h = 23 - Math.floor((now - new Date(r.ts).getTime()) / 3600000);
        if (h >= 0 && h < 24) { sums[h] += r.cpu_pct; counts[h] += 1; }
      }
      setHeat((prev) => ({ ...prev, [n.id]: sums.map((v, i) => (counts[i] ? v / counts[i] : null)) }));
    }).catch(() => {}));
  };
  useEffect(load, [ids]); // eslint-disable-line react-hooks/exhaustive-deps
  usePolling(load, 300000);
  return heat;
}

function Heat({ cells, label }) {
  const t = useT();
  if (!cells) return <span className="nx-muted">…</span>;
  const known = cells.filter((v) => v != null);
  const avg = known.length ? Math.round(known.reduce((a, v) => a + v, 0) / known.length) : null;
  return (
    <span className="nx-heat" role="img" aria-label={avg == null ? t("nd.heatNone", { name: label }) : t("nd.heatAvg", { name: label, p: avg, max: Math.round(Math.max(...known)) })}>
      {cells.map((v, i) => <span key={i} style={v == null ? undefined : { "--h": `${Math.round(12 + v * 0.88)}%` }} data-empty={v == null || undefined} />)}
    </span>
  );
}

// Nodes: this host plus the registered remote nodes (libvirt over SSH, no agent), with their live load
// recorded by the backend collector.
export default function NodesPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const { nodes, vms, pushToast, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, pushToast: s.pushToast, navigateTo: s.navigateTo })));
  const ctx = useContextTarget(); // right click on a node: its actions
  const [adding, setAdding] = useState(false);
  const [q, setQ] = useState("");
  const [view, setView] = useState("all"); // all | watch | maintenance
  const [picked, setPicked] = useState(() => new Set());
  useIntent("node", () => caps.admin && setAdding(true));
  const sorted = useMemo(() => [...nodes].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })), [nodes]);
  const heat = useHeat(sorted);
  const watch = sorted.filter((n) => ["warning", "danger"].includes(nodeTone(n).tone));
  const maint = sorted.filter((n) => n.maintenance);
  const shown = (view === "watch" ? watch : view === "maintenance" ? maint : sorted).filter((n) => !q || [n.name, n.ip].some((x) => x && String(x).toLowerCase().includes(q.trim().toLowerCase())));
  const pickedList = shown.filter((n) => picked.has(n.id));
  const togglePick = (id) => setPicked((p) => { const x = new Set(p); if (x.has(id)) x.delete(id); else x.add(id); return x; });
  // Bulk work on nodes: what is safe to do on several at once. Entering maintenance moves VMs, node by node, and stays
  // a single node's action; leaving it, and tagging, are not.
  async function bulkLeave() {
    const apply = pickedList.filter((n) => n.maintenance);
    if (apply.length === 0) { pushToast({ kind: "info", title: t("bulkn.noneInMaintenance") }); return; }
    if (!(await confirmAction({ title: t("bulkn.leave.title", { n: apply.length }), message: nameList(apply, (k) => t("bulk.andMore", { n: k })), confirmLabel: t("bulkn.leave") }))) return;
    const { ok, failed } = await runBulk(apply, (n) => leaveNodeMaintenance(n.id));
    pushToast({ kind: failed.length ? "warning" : "success", title: t("bulkc.done", { ok: ok.length, n: apply.length }), message: failed.map((f) => `${f.item.name}: ${errorMessage(f.error)}`).join("\n") || undefined });
    setPicked(new Set());
    refreshInventory().catch(() => {});
  }
  async function bulkTag() {
    const tag = await promptText({ title: t("bulkn.tag.title", { n: pickedList.length }), label: t("bulkn.tag.label") });
    if (!tag?.trim()) return;
    const { ok, failed } = await runBulk(pickedList, async (n) => {
      const meta = await fetchMeta("node", n.id);
      if (!meta.tags.includes(tag.trim().toLowerCase())) await saveMeta("node", n.id, { notes: meta.notes, tags: [...meta.tags, tag.trim()] });
    });
    pushToast({ kind: failed.length ? "warning" : "success", title: t("bulkc.done", { ok: ok.length, n: pickedList.length }), message: failed.map((f) => `${f.item.name}: ${errorMessage(f.error)}`).join("\n") || undefined });
    setPicked(new Set());
  }
  const cores = nodes.reduce((a, n) => a + (n.cpu_coeurs || 0), 0);
  const mem = nodes.reduce((a, n) => a + (n.memoire_totale_mo || 0), 0);

  async function remove(n) {
    if (!(await confirmAction({ title: t("nd.removeTitle", { name: n.name }), message: t("nd.removeMsg"), confirmLabel: t("nd.remove"), danger: true }))) return;
    try { await deleteRemoteNode(n.name); pushToast({ kind: "success", title: t("nd.removed"), message: n.name }); await refreshInventory(); }
    catch (err) { pushToast({ kind: "error", title: t("action.failed", { action: t("nd.remove") }), message: errorMessage(err) }); }
  }

  const exportCsv = () => downloadCsv("nodes.csv", [
    [t("nd.node"), t("ns.col.state"), t("ns.address"), t("ns.cpu"), t("ns.memory"), t("nd.vms"), t("ns.uptime")],
    ...shown.map((n) => [n.name, t(nodeTone(n).key), n.ip || "", n.cpu_utilisation != null ? `${Math.round(n.cpu_utilisation)} %` : "", pct(n.used_memory_mb, n.memoire_totale_mo) != null ? `${Math.round(pct(n.used_memory_mb, n.memoire_totale_mo))} %` : "", vms.filter((v) => v.node === n.id).length, formatUptimeLong(n.uptime_s, lang) || ""]),
  ]);

  return (
    <>
      <PageHeader title={t("tab.nodes")} help={t("nd.intro")} actions={(
        <>
          {caps.admin && <Cmd icon={Plus} label={t("nd.addSsh")} onClick={() => setAdding(true)} />}
          <Cmd icon={RefreshCw} label={t("action.refresh")} onClick={() => refreshInventory().catch(() => {})} />
          <Cmd icon={Download} label={t("list.exportCsv")} onClick={exportCsv} disabled={shown.length === 0} />
        </>
      )} />
      <div className="hc-fbar">
        <label className="hc-filter hc-filter--wide"><Search size={16} aria-hidden="true" /><input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("nd.filter")} aria-label={t("nd.filter")} /></label>
        <FilterPill label={t("list.stateIs")} value={view === "all" ? "" : view} onChange={(v) => setView(v || "all")} options={[
          { value: "", label: t("list.all") },
          { value: "watch", label: `${t("nd.view.watch")} (${watch.length})` },
          { value: "maintenance", label: `${t("nd.view.maintenance")} (${maint.length})` },
        ]} />
        <span className="nx-sp" />
        <span className="nx-muted hc-hint">{t("nd.heatHint")}</span>
      </div>
      <p className="hc-count" role="status">{t("list.showing", { n: shown.length, total: sorted.length })} · {t("nd.totals", { cores, mem: formatSizeMb(mem, lang) || "—", vms: vms.length })}</p>
      {caps.admin && pickedList.length > 0 && (
        <div className="hc-bulkc" role="toolbar" aria-label={t("bulkc.bar", { n: pickedList.length })}>
          <strong>{t("bulkc.bar", { n: pickedList.length })}</strong>
          <button type="button" className="nx-btn nx-btn--sm" onClick={bulkLeave}><LogOut size={14} aria-hidden="true" /> {t("bulkn.leave")}</button>
          <button type="button" className="nx-btn nx-btn--sm" onClick={bulkTag}><Tag size={14} aria-hidden="true" /> {t("bulkn.tag")}</button>
          <button type="button" className="nx-btn nx-btn--sm nx-btn--ghost" aria-label={t("bulkc.clear")} onClick={() => setPicked(new Set())}><X size={14} aria-hidden="true" /></button>
        </div>
      )}
      <div className="hc-gridwrap">
        <table className="hc-grid hc-grid--list">
          <thead><tr>
            {caps.admin && <th scope="col" className="hc-pick"><input type="checkbox" aria-label={t("bulkn.all")} checked={shown.length > 0 && pickedList.length === shown.length} onChange={(e) => setPicked(new Set(e.target.checked ? shown.map((n) => n.id) : []))} /></th>}
            <th scope="col">{t("nd.node")}</th><th scope="col">{t("ns.col.state")}</th>
            <th scope="col">{t("ns.cpu")}</th><th scope="col">{t("ns.memory")}</th>
            <th scope="col">{t("nd.vms")}</th><th scope="col">{t("nd.load24")}</th><th scope="col">{t("ns.uptime")}</th>
            <th scope="col"><span className="nx-sr">{t("actions")}</span></th>
          </tr></thead>
          <tbody>
            {shown.length === 0 && <tr><td colSpan={caps.admin ? 9 : 8} className="nx-muted">{t("nd.noneShown")}</td></tr>}
            {shown.map((n) => {
              const local = n.id === "local";
              const nodeVms = vms.filter((v) => v.node === n.id);
              const run = nodeVms.length ? nodeVms.filter((v) => v.state === "active").length : n.vms_running ?? 0;
              const total = nodeVms.length || (n.vms_running ?? 0) + (n.vms_stopped ?? 0);
              const st = nodeTone(n);
              return (
                <tr key={n.id} className={ctx.is("node", n.id) ? "is-ctx" : undefined} onContextMenu={ctx.open("node", n)}>
                  {caps.admin && <td className="hc-pick"><input type="checkbox" aria-label={t("bulkc.pick", { name: n.name })} checked={picked.has(n.id)} onChange={() => togglePick(n.id)} /></td>}
                  <th scope="row" className="hc-namecell">
                    <div><Server size={16} aria-hidden="true" /><span>
                      <button type="button" className="nx-lnk" onClick={() => navigateTo("node", n.id, "summary")}>{n.name}</button>
                      <small className="nx-muted">{local ? t("node.roleLocal") : n.member ? n.ip : `${n.ssh_user || "root"}@${n.ip}:${n.ssh_port || 22}`}</small>
                    </span></div>
                  </th>
                  <td><span className="nx-stw"><span className="nx-tree-dot" data-tone={st.tone} aria-hidden="true" />{t(st.key)}</span></td>
                  <td><Meter value={n.cpu_utilisation} label={`${t("ns.cpu")} ${n.name}`} /></td>
                  <td><Meter value={pct(n.used_memory_mb, n.memoire_totale_mo)} label={`${t("ns.memory")} ${n.name}`} /></td>
                  <td>{t("nd.vmsRun", { run, total })}</td>
                  <td><Heat cells={heat[n.id]} label={n.name} /></td>
                  <td className="nx-muted">{formatUptimeLong(n.uptime_s, lang) || "—"}</td>
                  <td className="hc-rowact"><div className="nx-ra">
                    {caps.admin && !local && <button type="button" className="hc-ibtn" aria-label={t("a11y.remove_node_x", { v: n.name })} title={t("nd.remove")} onClick={() => remove(n)}><Trash2 size={16} aria-hidden="true" /></button>}
                    <button type="button" className="hc-ibtn" aria-label={t("ctx.menuOf", { name: n.name })} onClick={ctx.open("node", n)}><Ellipsis size={16} aria-hidden="true" /></button>
                  </div></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {caps.admin && <ConfigCopyCard nodes={nodes} />}
      {ctx.target && <NodeContextMenu key={ctx.target.obj.id} node={ctx.target.obj} at={ctx.target.at} returnFocus={ctx.target.el} onDone={ctx.close} />}
      <AddNodeDrawer open={adding} onClose={() => setAdding(false)} onAdded={() => refreshInventory()} />
    </>
  );
}
NodesPage.ownHeader = true;
