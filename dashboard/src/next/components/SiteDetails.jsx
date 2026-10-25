import { useCallback, useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";
import { fetchSiteList, setSiteToken, deleteSiteToken, siteVmAction, siteVmMigrate } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { Chip, Field, Loading, TableWrap } from "./ui";

const ACTIONS = ["start", "shutdown", "reboot", "stop", "reset"];
const FORCED = new Set(["stop", "reset"]);
const gib = (mb) => (mb == null ? "—" : `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GiB`);

// One remote cluster in the Sites page's drawer: its nodes, its VMs and, with its manage token, their power actions
// and their move to another of its nodes, as Proxmox Datacenter Manager's; the rest (console, settings) in its own
// dashboard. The remote checks every action with the rights of the token's account and records it in its journal.
export default function SiteDetails({ site, remote, onChanged }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [vms, setVms] = useState(null);
  const [vmError, setVmError] = useState(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [moving, setMoving] = useState(null);
  const canManage = remote?.uses?.includes("manage");
  const endpoints = remote?.endpoints || [];
  const first = endpoints[0];
  const loadVms = useCallback(() => {
    if (!site.reachable) return;
    fetchSiteList(site.name, "vms").then((r) => { setVms(Array.isArray(r) ? r : []); setVmError(null); }).catch((e) => setVmError(errorMessage(e)));
  }, [site.name, site.reachable]);
  useEffect(() => { loadVms(); }, [loadVms]);

  async function saveToken() {
    setBusy(true);
    try { await setSiteToken(site.name, "manage", token.trim()); setToken(""); pushToast({ kind: "success", title: t("site.tokenSaved"), message: site.name }); await onChanged(); }
    catch (e) { pushToast({ kind: "error", title: t("site.tokenFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function dropToken() {
    if (!(await confirmAction({ title: t("site.dropTokenTitle"), message: t("site.dropTokenMsg"), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteSiteToken(site.name, "manage"); await onChanged(); } catch (e) { pushToast({ kind: "error", title: t("site.tokenFailed"), message: errorMessage(e) }); }
  }
  async function run(vm, action) {
    if (FORCED.has(action) && !(await confirmAction({ title: t(`site.a.${action}Title`, { name: vm.name }), message: t("site.forcedMsg"), confirmLabel: t(`site.a.${action}`), danger: true }))) return;
    setBusy(true);
    try { await siteVmAction(site.name, vm.name, action, vm.on); pushToast({ kind: "success", title: t("site.actionSent"), message: `${t(`site.a.${action}`)} · ${vm.name}` }); setTimeout(loadVms, 2000); }
    catch (e) { pushToast({ kind: "error", title: t("site.actionFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function move() {
    setBusy(true);
    try { await siteVmMigrate(site.name, moving.vm.name, moving.vm.on, moving.target); pushToast({ kind: "success", title: t("site.moveSent"), message: `${moving.vm.name} → ${moving.target}` }); setMoving(null); setTimeout(loadVms, 3000); }
    catch (e) { pushToast({ kind: "error", title: t("site.moveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  const targets = (vm) => (site.summary?.nodes || []).filter((n) => n.online && n.name !== vm.on).map((n) => n.name);

  return (
    <>
      {!site.reachable && <div className="nx-bn" data-tone="danger" role="alert">{site.problem}</div>}
      {first && <a className="nx-btn" href={`https://${first.address.includes(":") ? `[${first.address}]` : first.address}:${first.port}/`} target="_blank" rel="noopener noreferrer"><ExternalLink size={15} aria-hidden="true" />{t("site.openDashboard")}</a>}
      {site.reachable && (
        <TableWrap label={t("site.nodes")}>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("site.node")}</th><th scope="col">{t("site.status")}</th><th scope="col">{t("site.vms")}</th><th scope="col">CPU</th><th scope="col">{t("site.memFree")}</th></tr></thead>
            <tbody>{site.summary.nodes.map((n) => (
              <tr key={n.name}>
                <th scope="row">{n.name}</th>
                <td>{n.online ? <Chip tone="success">{t("site.online")}</Chip> : <Chip tone="danger">{t("site.offline")}</Chip>}</td>
                <td>{n.vms_running}/{n.vms_total}</td>
                <td className="nx-mono">{n.cpu_pct == null ? "—" : `${Math.round(n.cpu_pct)} %`}{n.cpus ? ` · ${n.cpus}` : ""}</td>
                <td className="nx-mono">{gib(n.mem_free_mb)}{n.mem_total_mb ? ` / ${gib(n.mem_total_mb)}` : ""}</td>
              </tr>
            ))}</tbody>
          </table>
        </TableWrap>
      )}
      {site.reachable && <>
        <h3 className="nx-f-label">{t("site.vmsOf", { name: site.name })}</h3>
        {!canManage && <p className="nx-f-h" style={{ margin: 0 }}>{t("site.noManage")}</p>}
        {vmError ? <div className="nx-bn" data-tone="danger" role="alert">{vmError}</div> : !vms ? <Loading /> : vms.length === 0 ? <p className="nx-muted" style={{ margin: 0 }}>{t("site.noVm")}</p> : (
          <TableWrap label={t("site.vmsOf", { name: site.name })}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("bg.name")}</th><th scope="col">{t("site.node")}</th><th scope="col">{t("site.status")}</th>{canManage && <th scope="col"><span className="nx-sr">{t("actions")}</span></th>}</tr></thead>
              <tbody>{vms.map((vm) => (
                <tr key={`${vm.on}/${vm.name}`}>
                  <th scope="row">{vm.name}</th>
                  <td>{vm.on || "—"}</td>
                  <td>{vm.state || "—"}</td>
                  {canManage && <td><div className="nx-ra">
                    <select className="nx-inp" aria-label={t("site.actionX", { name: vm.name })} value="" disabled={busy} onChange={(e) => {
                      const action = e.target.value;
                      if (action === "migrate") setMoving({ vm, target: targets(vm)[0] || "" }); else if (action) run(vm, action);
                    }}>
                      <option value="">{t("site.action")}</option>
                      {ACTIONS.map((a) => <option key={a} value={a}>{t(`site.a.${a}`)}</option>)}
                      {targets(vm).length > 0 && <option value="migrate">{t("site.a.migrate")}</option>}
                    </select>
                  </div></td>}
                </tr>
              ))}</tbody>
            </table>
          </TableWrap>
        )}
        {moving && (
          <div className="nx-fg" role="group" aria-label={t("site.moveX", { name: moving.vm.name })}>
            <Field label={t("site.moveTo", { name: moving.vm.name })} hint={t("site.moveHint")}>{(p) => <select {...p} className="nx-inp" value={moving.target} onChange={(e) => setMoving((m) => ({ ...m, target: e.target.value }))}>
              {targets(moving.vm).map((n) => <option key={n} value={n}>{n}</option>)}
            </select>}</Field>
            <div style={{ alignSelf: "end", display: "flex", gap: "var(--space-2)" }}>
              <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setMoving(null)}>{t("action.cancel")}</button>
              <button type="button" className="nx-btn nx-btn--primary" disabled={busy || !moving.target} onClick={move}>{t("site.a.migrate")}</button>
            </div>
          </div>
        )}
      </>}
      {remote && <>
        <h3 className="nx-f-label">{t("site.manageToken")}</h3>
        {canManage ? (
          <div><Chip tone="success">{t("site.manageSet")}</Chip> <button type="button" className="nx-btn nx-btn--ghost" onClick={dropToken}>{t("site.dropToken")}</button></div>
        ) : (
          <div className="nx-fg">
            <Field label={t("site.manageTokenLabel")} hint={t("site.manageTokenHint")}>{(p) => <input {...p} className="nx-inp nx-mono" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />}</Field>
            <div style={{ alignSelf: "end" }}><button type="button" className="nx-btn" disabled={busy || !token.trim()} onClick={saveToken}>{t("mx.save")}</button></div>
          </div>
        )}
        <h3 className="nx-f-label">{t("site.endpoints")}</h3>
        <ul className="nx-mono" style={{ margin: 0 }}>{endpoints.map((e) => <li key={`${e.address}:${e.port}`} title={e.fingerprint}>{e.node ? `${e.node} — ` : ""}{e.address}:{e.port}</li>)}</ul>
        <p className="nx-f-h" style={{ margin: 0 }}>{t("site.endpointsHelp")}</p>
      </>}
    </>
  );
}
