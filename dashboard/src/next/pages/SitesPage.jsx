import { useCallback, useEffect, useState } from "react";
import { Globe, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { fetchSites, fetchSiteRemotes, searchSites, probeSite, createSite, deleteSite, fetchThisNodeFingerprint } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { ErrorState } from "../components/States";
import SiteDetails from "../components/SiteDetails";
import { PageHeader, Card, Chip, Empty, Field, KpiStrip, Loading, SideDrawer, TableWrap } from "../components/ui";

const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$/;
const EMPTY = { name: "", address: "", port: "8000", fingerprint: "", matches: false, token: "", manage: "" };
const groups = (hex) => (hex || "").match(/.{1,4}/g)?.join(" ") || "";
const gib = (mb) => (mb == null ? "—" : `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GiB`);

function nodesOf(s) {
  const nodes = s.summary?.nodes || [];
  return { online: nodes.filter((n) => n.online).length, total: nodes.length, free: nodes.reduce((a, n) => a + (n.mem_free_mb || 0), 0) };
}

// Datacenter › Sites: every Hyperlite cluster this one views (docs/design/multi-cluster.md), as Proxmox Datacenter
// Manager, but served by any node of any cluster: their state at a glance, a search through all their VMs, and the
// remotes, each recorded from one of its nodes with its certificate compared and a viewer's token there; with a
// manage token, their VMs' power actions and moves (components/SiteDetails.jsx).
export default function SitesPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [sites, setSites] = useState(null);
  const [remotes, setRemotes] = useState([]);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState(null);
  const [own, setOwn] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([fetchSites(), fetchSiteRemotes()]);
      setSites(s); setRemotes(r); setError(null);
    } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => {
    load();
    fetchThisNodeFingerprint().then((r) => setOwn(r.fingerprint)).catch(() => setOwn(null));
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, [load]);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value,
    ...(k === "address" || k === "port" ? { fingerprint: "", matches: false } : {}) }));
  const port = Number(form?.port);
  const ready = form && NAME_RE.test(form.name) && form.address && Number.isInteger(port) && form.fingerprint && form.matches && form.token;

  async function probe() {
    setBusy(true);
    try { const r = await probeSite(form.address.trim(), port); setForm((f) => ({ ...f, fingerprint: r.fingerprint, matches: false })); }
    catch (e) { pushToast({ kind: "error", title: t("site.probeFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function save() {
    setBusy(true);
    try {
      await createSite({ name: form.name, address: form.address.trim(), port, fingerprint: form.fingerprint, view_token: form.token.trim(), manage_token: form.manage.trim() || null });
      pushToast({ kind: "success", title: t("site.saved"), message: form.name });
      setForm(null); await load();
    } catch (e) { pushToast({ kind: "error", title: t("site.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function remove(name) {
    if (!(await confirmAction({ title: t("site.deleteTitle", { name }), message: t("site.deleteMsg"), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteSite(name); setOpen(null); await load(); } catch (e) { pushToast({ kind: "error", title: t("site.deleteFailed"), message: errorMessage(e) }); }
  }
  async function search(e) {
    e.preventDefault();
    if (query.trim().length < 2) return;
    setBusy(true);
    try { setFound(await searchSites(query.trim())); } catch (err) { pushToast({ kind: "error", title: t("site.searchFailed"), message: errorMessage(err) }); } finally { setBusy(false); }
  }

  const reachable = sites?.filter((s) => s.reachable) || [];
  const vms = reachable.reduce((a, s) => ({ running: a.running + s.summary.vms.running, total: a.total + s.summary.vms.running + s.summary.vms.stopped }), { running: 0, total: 0 });
  const problems = reachable.reduce((a, s) => a + s.summary.problems.length, 0) + (sites?.length || 0) - reachable.length;
  const shown = open && sites?.find((s) => s.name === open);
  const actions = (
    <>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={load}><RefreshCw size={15} aria-hidden="true" />{t("action.refresh")}</button>
      <button type="button" className="nx-btn nx-btn--primary" onClick={() => setForm({ ...EMPTY })}><Plus size={15} aria-hidden="true" />{t("site.add")}</button>
    </>
  );

  return (
    <>
      <PageHeader title={t("tab.sites")} help={t("site.intro")} actions={actions} />
      {error && !sites ? <ErrorState message={error} onRetry={load} /> : !sites ? <Loading /> : (
        <>
          <KpiStrip label={t("site.state")} items={[
            { id: "sites", label: t("site.sites"), value: sites.length, sub: t("site.reachableN", { n: reachable.length }), dot: reachable.length === sites.length ? "success" : "warning" },
            { id: "vms", label: t("site.vms"), value: vms.running, sub: t("site.ofTotal", { n: vms.total }) },
            { id: "problems", label: t("site.problems"), value: problems, dot: problems ? "warning" : "success" },
          ]} />
          <Card title={t("site.list")} flush>
            {sites.length === 1 && <Empty icon={Globe} title={t("site.none")} text={t("site.noneHelp")} />}
            <TableWrap label={t("site.list")}>
              <table className="nx-table">
                <thead><tr>
                  <th scope="col">{t("bg.name")}</th><th scope="col">{t("site.status")}</th><th scope="col">{t("site.version")}</th>
                  <th scope="col">{t("site.nodes")}</th><th scope="col">{t("site.vms")}</th><th scope="col">{t("site.memFree")}</th><th scope="col">{t("site.problems")}</th>
                  <th scope="col"><span className="nx-sr">{t("actions")}</span></th>
                </tr></thead>
                <tbody>{sites.map((s) => {
                  const n = nodesOf(s);
                  return (
                    <tr key={s.name + (s.self ? ":self" : "")}>
                      <th scope="row">{s.name}{s.self && <> <Chip tone="info">{t("site.self")}</Chip></>}</th>
                      <td>{!s.reachable ? <Chip tone="danger" title={s.problem}>{t("site.unreachable")}</Chip> : s.summary.read_only ? <Chip tone="warning">{t("site.readOnly")}</Chip> : <Chip tone="success">{t("site.ok")}</Chip>}</td>
                      <td className="nx-mono">{s.summary?.version || "—"}</td>
                      <td>{s.reachable ? `${n.online}/${n.total}` : "—"}</td>
                      <td>{s.reachable ? <>{s.summary.vms.running}/{s.summary.vms.running + s.summary.vms.stopped}{s.summary.vms.crashed > 0 && <> <Chip tone="danger">{t("site.crashedN", { n: s.summary.vms.crashed })}</Chip></>}</> : "—"}</td>
                      <td className="nx-mono">{s.reachable ? gib(n.free) : "—"}</td>
                      <td className="nx-wrapcell">{!s.reachable ? <span className="nx-f-h is-error">{s.problem}</span> : s.summary.problems.length ? s.summary.problems.join(" · ") : <span className="nx-muted">{t("site.noProblem")}</span>}</td>
                      <td><div className="nx-ra">
                        <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("site.detailsX", { name: s.name })} onClick={() => setOpen(s.name)} disabled={s.self}>{t("site.details")}</button>
                        {!s.self && <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" aria-label={t("mx.deleteX", { name: s.name })} onClick={() => remove(s.name)}><Trash2 size={15} aria-hidden="true" /></button>}
                      </div></td>
                    </tr>
                  );
                })}</tbody>
              </table>
            </TableWrap>
          </Card>
          <Card title={t("site.search")}>
            <form className="nx-fg" onSubmit={search} role="search">
              <Field label={t("site.searchLabel")} hint={t("site.searchHint")}>{(p) => <input {...p} className="nx-inp" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="web" />}</Field>
              <div style={{ alignSelf: "end" }}><button type="submit" className="nx-btn" disabled={busy || query.trim().length < 2}><Search size={15} aria-hidden="true" />{t("site.searchGo")}</button></div>
            </form>
            {found && (found.items.length === 0 ? <p className="nx-muted">{t("site.noMatch")}</p> : (
              <TableWrap label={t("site.search")}>
                <table className="nx-table">
                  <thead><tr><th scope="col">{t("site.site")}</th><th scope="col">{t("bg.name")}</th><th scope="col">{t("site.node")}</th><th scope="col">{t("site.status")}</th><th scope="col">IP</th></tr></thead>
                  <tbody>{found.items.map((v) => (
                    <tr key={`${v.site}/${v.vm}`}><td>{v.site}</td><th scope="row">{v.vm}</th><td>{v.node || "—"}</td><td>{v.state || "—"}</td><td className="nx-mono">{v.ip || "—"}</td></tr>
                  ))}</tbody>
                </table>
              </TableWrap>
            ))}
            {found?.unreachable?.length > 0 && <p className="nx-f-h is-error">{t("site.notSearched", { names: found.unreachable.join(", ") })}</p>}
          </Card>
          {own && <p className="nx-f-h">{t("site.ownHelp")} <code className="nx-mono" aria-label={t("brm.ownFingerprint")}>{groups(own)}</code></p>}
        </>
      )}
      <SideDrawer open={!!shown} title={shown?.name || ""} onClose={() => setOpen(null)}>
        {shown && <SiteDetails key={shown.name} site={shown} remote={remotes.find((r) => r.name === shown.name)} onChanged={load} />}
      </SideDrawer>
      <SideDrawer open={!!form} title={t("site.add")} onClose={() => setForm(null)} busy={busy} footer={<>
        <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setForm(null)}>{t("action.cancel")}</button>
        <button type="button" className="nx-btn nx-btn--primary" disabled={!ready || busy} onClick={save}>{t("mx.save")}</button>
      </>}>
        {form && <>
          <p className="nx-muted" style={{ margin: 0 }}>{t("site.help")}</p>
          <Field label={t("bg.name")} error={form.name && !NAME_RE.test(form.name) ? t("bst.nameRule") : null}>{(p) => <input {...p} className="nx-inp" value={form.name} onChange={set("name")} placeholder="lyon" />}</Field>
          <div className="nx-fg">
            <Field label={t("brm.address")} hint={t("site.addressHint")}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.address} onChange={set("address")} placeholder="203.0.113.10" />}</Field>
            <Field label={t("brm.port")}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min={1} max={65535} value={form.port} onChange={set("port")} />}</Field>
          </div>
          <button type="button" className="nx-btn" disabled={busy || !form.address || !Number.isInteger(port)} onClick={probe}>{t("brm.probe")}</button>
          {form.fingerprint && <>
            <Field label={t("brm.fingerprint")} hint={t("brm.fingerprintHint")}>{(p) => <input {...p} className="nx-inp nx-mono" readOnly value={groups(form.fingerprint)} />}</Field>
            <label className="nx-check"><input type="checkbox" checked={form.matches} onChange={set("matches")} /> {t("brm.matches")}</label>
          </>}
          <Field label={t("site.token")} hint={t("site.tokenHint")}>{(p) => <input {...p} className="nx-inp nx-mono" type="password" autoComplete="off" value={form.token} onChange={set("token")} />}</Field>
          <Field label={t("site.manageTokenLabel")} hint={t("site.manageTokenHint")}>{(p) => <input {...p} className="nx-inp nx-mono" type="password" autoComplete="off" value={form.manage} onChange={set("manage")} />}</Field>
        </>}
      </SideDrawer>
    </>
  );
}
SitesPage.ownHeader = true;
